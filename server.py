from __future__ import annotations

import argparse
import datetime as dt
import glob
import html
import json
import os
import re
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


APP_NAME = "Agent Buddy"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
DEFAULT_CONTEXT_LIMIT = 200_000
DEFAULT_NOTION_DATA_SOURCE_ID = "6023633d-dc41-4b44-a1ed-64862fb83b72"
NOTION_API_VERSION = "2026-03-11"
ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"


def now_local() -> dt.datetime:
    return dt.datetime.now().astimezone()


def hhmm(ts: dt.datetime | None = None) -> str:
    return (ts or now_local()).strftime("%H:%M")


def hhmmss(ts: dt.datetime | None = None) -> str:
    return (ts or now_local()).strftime("%H:%M:%S")


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float_text(value: Any) -> str:
    try:
        return f"{float(value or 0):.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "0"


def truncate(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def read_jsonl_tail(path: Path, max_lines: int = 250) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    # Files can be large. Read a suffix and parse complete JSONL lines from it.
    chunk_size = 256 * 1024
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - chunk_size))
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []

    lines = data.splitlines()[-max_lines:]
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


class AgentBuddyState:
    def __init__(self, workspace: Path, home: Path, context_limit: int) -> None:
        self.workspace = workspace
        self.home = home
        self.context_limit = context_limit
        self.cc_db = home / ".cc-switch" / "cc-switch.db"
        self.codex_root = home / ".codex"
        self.codex_session_index = self.codex_root / "session_index.jsonl"
        self.codex_sessions_root = self.codex_root / "sessions"
        self.claude_project_dir = home / ".claude" / "projects" / "D--Cursor-code"
        self.hermes_root = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))) / "hermes"
        self.notion_token = (
            os.environ.get("NOTION_TOKEN")
            or os.environ.get("NOTION_API_KEY")
            or os.environ.get("NOTION_INTEGRATION_TOKEN")
            or ""
        ).strip()
        self.notion_data_source_id = os.environ.get(
            "NOTION_DATA_SOURCE_ID", DEFAULT_NOTION_DATA_SOURCE_ID
        ).strip()
        self._state_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._project_cache: tuple[float, dict[str, Any]] | None = None

    def build(self, agent: str = "codex", session_id: str | None = None) -> dict[str, Any]:
        agent = agent if agent in {"codex", "notion", "hermes"} else "codex"
        cache_key = (agent, session_id or "")
        now = time.time()
        cached = self._state_cache.get(cache_key)
        cache_ttl = 30 if agent == "notion" else 5
        if cached and now - cached[0] < cache_ttl:
            return cached[1]
        state = self._build_uncached(agent, session_id)
        self._state_cache[cache_key] = (now, state)
        return state

    def _build_uncached(self, agent: str = "codex", session_id: str | None = None) -> dict[str, Any]:
        agent = agent if agent in {"codex", "notion", "hermes"} else "codex"
        sources: dict[str, str] = {}
        errors: list[str] = []

        project = self.project_state()
        cc_state = {"usage_today": self.empty_usage()}
        if agent != "notion":
            cc_state = self.cc_switch_state()
            sources["cc-switch"] = cc_state.pop("_source_status")
            if cc_state.get("_error"):
                errors.append(f"cc-switch: {cc_state['_error']}")

        if agent == "notion":
            agent_state = self.notion_state(session_id)
            sources["notion"] = agent_state.pop("_source_status")
            project = agent_state.get("project") or project
        elif agent == "hermes":
            agent_state = self.hermes_state(session_id)
            sources["hermes"] = agent_state.pop("_source_status")
            project = agent_state.get("project") or {"name": "Hermes", "path": str(self.hermes_root), "branch": "gateway"}
        else:
            agent_state = self.codex_state(session_id)
            sources["codex"] = agent_state.pop("_source_status")

        if agent_state.get("_error"):
            errors.append(f"{agent}: {agent_state['_error']}")

        project = agent_state.get("project") or project
        usage = agent_state.get("usage_today") or cc_state.get("usage_today") or self.empty_usage()
        used_tokens = safe_int(agent_state.get("used_tokens"))
        if not used_tokens:
            used_tokens = safe_int(usage.get("input_tokens")) + safe_int(usage.get("output_tokens"))
        context_limit = safe_int(agent_state.get("context_limit"), self.context_limit)
        percent = safe_int(
            agent_state.get("context_percent"),
            min(100, round((used_tokens / max(1, context_limit)) * 100)),
        )

        model = agent_state.get("model") or cc_state.get("model") or {
            "provider": "Unavailable",
            "model": "Unavailable",
            "app_type": agent,
        }

        activity = []
        activity.extend(agent_state.get("activity", []))
        if agent == "codex":
            activity.extend(cc_state.get("activity", []))
        activity = sorted(activity, key=lambda item: item.get("_sort", 0), reverse=True)[:8]
        for item in activity:
            item.pop("_sort", None)

        latest_reply = agent_state.get("latest_reply") or "Unavailable"
        labels = agent_state.get("labels") or {
            "project": "PROJECT",
            "model": "MODEL",
            "context": "CONTEXT",
            "requests": "REQ",
            "tokens": "TOKENS",
            "latest_reply": "LATEST REPLY",
        }
        metrics = agent_state.get("metrics") or {
            "requests": usage.get("requests", 0),
            "tokens": safe_int(usage.get("input_tokens")) + safe_int(usage.get("output_tokens")),
        }
        app_status = "live"
        if any(status == "error" for status in sources.values()):
            app_status = "error"
        elif any(status == "stale" for status in sources.values()):
            app_status = "stale"

        return {
            "app": {
                "name": APP_NAME,
                "status": app_status,
                "updated_at": hhmmss(),
                "agent": agent,
                "selected_session_id": agent_state.get("_selected_session_id"),
            },
            "project": project,
            "model": model,
            "context": {
                "used_tokens": used_tokens,
                "limit_tokens": context_limit,
                "percent": percent,
                "estimated": True,
                "display_text": agent_state.get("context_display"),
            },
            "usage_today": usage,
            "labels": labels,
            "metrics": metrics,
            "sessions": agent_state.get("sessions") or [
                {"name": agent.title(), "state": "unavailable", "updated_at": "--:--"}
            ],
            "counters": {"approved": 0, "denied": 0},
            "latest_reply": latest_reply,
            "activity": activity,
            "sources": sources,
            "errors": errors[:3],
        }

    def notion_state(self, session_id: str | None = None) -> dict[str, Any]:
        labels = {
            "project": "SESSION",
            "model": "SOURCE",
            "context": "STATUS",
            "requests": "TOPIC",
            "tokens": "DECISION",
            "latest_reply": "DETAILS",
        }
        base = {
            "labels": labels,
            "model": {"provider": "Notion", "model": "Notion AI", "app_type": "session-log"},
            "usage_today": self.empty_usage(),
            "metrics": {"requests": "--", "tokens": "--", "display_mode": "text"},
            "context_display": "Unavailable",
            "context_percent": 0,
        }
        if not self.notion_token:
            return {
                **base,
                "_source_status": "error",
                "_error": "NOTION_TOKEN is not configured",
                "project": {
                    "name": "Notion 未连接",
                    "path": "Session Log 会话日志",
                    "detail": "需要配置 Integration Token",
                    "branch": "--",
                },
                "sessions": [{
                    "id": "",
                    "name": "Configure Notion",
                    "state": "error",
                    "updated_at": "",
                    "selected": True,
                }],
                "latest_reply": "请配置 NOTION_TOKEN，并把 Session Log 数据库共享给该 Notion Integration。",
                "activity": [],
            }

        try:
            rows = self.query_notion_sessions()
        except (OSError, ValueError) as exc:
            return {
                **base,
                "_source_status": "error",
                "_error": str(exc),
                "project": {
                    "name": "Notion 读取失败",
                    "path": "Session Log 会话日志",
                    "detail": "检查连接权限",
                    "branch": "--",
                },
                "sessions": [{
                    "id": "",
                    "name": "Notion unavailable",
                    "state": "error",
                    "updated_at": "",
                    "selected": True,
                }],
                "latest_reply": "Notion 数据暂时不可用，请检查 Integration 权限和数据源 ID。",
                "activity": [],
            }

        if not rows:
            return {
                **base,
                "_source_status": "stale",
                "project": {
                    "name": "暂无会话日志",
                    "path": "Session Log 会话日志",
                    "detail": "database empty",
                    "branch": "--",
                },
                "sessions": [],
                "latest_reply": "Notion Session Log 中还没有记录。",
                "activity": [],
            }

        selected = next((row for row in rows if row["id"] == session_id), rows[0])
        selected_id = selected["id"]
        status = selected["status"] or "未设置"
        completed = "完结" in status or "完成" in status
        in_progress = "进行" in status
        context_percent = 100 if completed else 55 if in_progress else 0
        sessions = [
            {
                "id": row["id"],
                "name": truncate(row["title"], 28),
                "state": "active" if "进行" in row["status"] else "idle",
                "updated_at": row["date"][5:] if len(row["date"]) >= 10 else row["updated_at"],
                "selected": row["id"] == selected_id,
            }
            for row in rows[:5]
        ]
        change = selected["change"]
        todo = selected["todo"]
        try:
            details = self.query_notion_page_markdown(selected_id)
            if not details:
                details = selected["decision"] or "暂无对话详细信息"
        except (OSError, ValueError):
            details = selected["decision"] or "暂无对话详细信息"
        activity = []
        for label, value in (("变更", change), ("待办", todo), ("主题", selected["topics"])):
            if value:
                activity.append({
                    "time": selected["updated_at"],
                    "text": f"{label}: {truncate(value, 46)}",
                    "_sort": time.time(),
                })

        return {
            **base,
            "_source_status": "ok",
            "_selected_session_id": selected_id,
            "project": {
                "name": truncate(selected["title"], 32),
                "path": "Notion · Session Log 会话日志",
                "detail": f"date: {selected['date'] or '--'}",
                "branch": "--",
            },
            "context_display": status,
            "context_percent": context_percent,
            "metrics": {
                "requests": selected["topics"] or "未设置",
                "tokens": selected["decision"] or "暂无",
                "display_mode": "text",
            },
            "sessions": sessions,
            "latest_reply": details,
            "activity": activity,
        }

    def query_notion_page_markdown(self, page_id: str) -> str:
        url = f"https://api.notion.com/v1/pages/{page_id}/markdown"
        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Authorization": f"Bearer {self.notion_token}",
                "Notion-Version": NOTION_API_VERSION,
                "User-Agent": "Agent-Buddy-Screen/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(detail).get("message") or detail
            except json.JSONDecodeError:
                message = detail
            raise OSError(f"Notion HTTP {exc.code}: {truncate(str(message), 120)}") from exc
        except urllib.error.URLError as exc:
            raise OSError(f"Notion network error: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError("Notion returned invalid page markdown JSON") from exc

        markdown = data.get("markdown") if isinstance(data, dict) else None
        if not isinstance(markdown, str):
            raise ValueError("Notion response has no page markdown")
        return self.notion_markdown_text(markdown)

    def notion_markdown_text(self, markdown: str) -> str:
        text = markdown.replace("\r\n", "\n")
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"[图片: \1]", text)
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"^#{1,4}\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*[-*+]\s+", "• ", text, flags=re.MULTILINE)
        text = re.sub(r"[*_]{1,2}([^*_]+)[*_]{1,2}", r"\1", text)
        text = text.replace("`", "")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        value = "\n".join(lines)
        return value if len(value) <= 2000 else value[:1997].rstrip() + "..."

    def query_notion_sessions(self) -> list[dict[str, str]]:
        url = f"https://api.notion.com/v1/data_sources/{self.notion_data_source_id}/query"
        payload = json.dumps({
            "page_size": 20,
            "sorts": [{"property": "日期", "direction": "descending"}],
        }).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.notion_token}",
                "Notion-Version": NOTION_API_VERSION,
                "Content-Type": "application/json",
                "User-Agent": "Agent-Buddy-Screen/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(detail).get("message") or detail
            except json.JSONDecodeError:
                message = detail
            raise OSError(f"Notion HTTP {exc.code}: {truncate(str(message), 120)}") from exc
        except urllib.error.URLError as exc:
            raise OSError(f"Notion network error: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError("Notion returned invalid JSON") from exc

        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise ValueError("Notion response has no results")
        return [self.parse_notion_page(page) for page in results if isinstance(page, dict)]

    def parse_notion_page(self, page: dict[str, Any]) -> dict[str, str]:
        properties = page.get("properties") if isinstance(page.get("properties"), dict) else {}
        date_value = self.notion_property(properties.get("日期"), "date")
        updated = parse_iso(str(page.get("last_edited_time") or ""))
        return {
            "id": str(page.get("id") or ""),
            "title": self.notion_property(properties.get("会话"), "title") or "Untitled session",
            "date": date_value,
            "status": self.notion_property(properties.get("状态"), "status"),
            "topics": self.notion_property(properties.get("主题"), "multi_select"),
            "decision": self.notion_property(properties.get("结论/决策"), "rich_text"),
            "change": self.notion_property(properties.get("变更"), "rich_text"),
            "todo": self.notion_property(properties.get("待办"), "rich_text"),
            "url": str(page.get("url") or ""),
            "updated_at": updated.strftime("%H:%M") if updated else "--:--",
        }

    def notion_property(self, prop: Any, kind: str) -> str:
        if not isinstance(prop, dict):
            return ""
        if kind in {"title", "rich_text"}:
            parts = prop.get(kind)
            if not isinstance(parts, list):
                return ""
            return "".join(
                str(item.get("plain_text") or "")
                for item in parts
                if isinstance(item, dict)
            ).strip()
        if kind == "date":
            value = prop.get("date")
            return str(value.get("start") or "") if isinstance(value, dict) else ""
        if kind == "status":
            value = prop.get("status")
            return str(value.get("name") or "") if isinstance(value, dict) else ""
        if kind == "multi_select":
            values = prop.get("multi_select")
            if not isinstance(values, list):
                return ""
            return ", ".join(
                str(value.get("name") or "")
                for value in values
                if isinstance(value, dict)
            )
        return ""

    def project_state(self) -> dict[str, Any]:
        now = time.time()
        if self._project_cache and now - self._project_cache[0] < 60:
            return self._project_cache[1]
        branch = "Unavailable"
        try:
            result = subprocess.run(
                ["git", "-C", str(self.workspace), "branch", "--show-current"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            candidate = result.stdout.strip()
            if candidate:
                branch = candidate
        except (OSError, subprocess.TimeoutExpired):
            pass

        value = {
            "name": self.workspace.name or "Cursor-code",
            "path": str(self.workspace),
            "branch": branch,
        }
        self._project_cache = (now, value)
        return value

    def cc_switch_state(self) -> dict[str, Any]:
        if not self.cc_db.exists():
            return {
                "_source_status": "error",
                "_error": f"missing {self.cc_db}",
                "usage_today": self.empty_usage(),
            }

        try:
            con = sqlite3.connect(f"file:{self.cc_db}?mode=ro", uri=True, timeout=1)
            con.row_factory = sqlite3.Row
        except sqlite3.Error as exc:
            return {"_source_status": "error", "_error": str(exc), "usage_today": self.empty_usage()}

        try:
            today = now_local().date().isoformat()
            provider = self.current_provider(con)
            usage = self.daily_usage(con, today)
            activity = self.request_activity(con)
            return {
                "_source_status": "ok",
                "model": provider,
                "usage_today": usage,
                "activity": activity,
            }
        except sqlite3.Error as exc:
            return {"_source_status": "error", "_error": str(exc), "usage_today": self.empty_usage()}
        finally:
            con.close()

    def current_provider(self, con: sqlite3.Connection) -> dict[str, str]:
        row = con.execute(
            """
            select name, app_type, settings_config, provider_type
            from providers
            where is_current = 1
            order by created_at desc
            limit 1
            """
        ).fetchone()
        if row is None:
            row = con.execute(
                """
                select name, app_type, settings_config, provider_type
                from providers
                order by created_at desc
                limit 1
                """
            ).fetchone()

        if row is None:
            return {"provider": "Unavailable", "model": "Unavailable", "app_type": "codex"}

        model = "Unavailable"
        raw_config = row["settings_config"] or ""
        try:
            config = json.loads(raw_config)
            for key in ("model", "default_model", "largeModelName", "smallModelName"):
                if isinstance(config, dict) and config.get(key):
                    model = str(config[key])
                    break
        except json.JSONDecodeError:
            pass

        if model == "Unavailable":
            latest = con.execute(
                """
                select model, request_model
                from proxy_request_logs
                where model is not null or request_model is not null
                order by created_at desc
                limit 1
                """
            ).fetchone()
            if latest:
                model = latest["model"] or latest["request_model"] or model

        return {
            "provider": row["name"] or row["provider_type"] or "Unavailable",
            "model": model,
            "app_type": row["app_type"] or "codex",
        }

    def daily_usage(self, con: sqlite3.Connection, today: str) -> dict[str, Any]:
        row = con.execute(
            """
            select
              coalesce(sum(request_count), 0) as requests,
              coalesce(sum(input_tokens), 0) as input_tokens,
              coalesce(sum(output_tokens), 0) as output_tokens,
              coalesce(sum(total_cost_usd), 0) as cost_usd
            from usage_daily_rollups
            where date = ?
            """,
            (today,),
        ).fetchone()
        usage = self.empty_usage() if row is None else {
            "requests": safe_int(row["requests"]),
            "input_tokens": safe_int(row["input_tokens"]),
            "output_tokens": safe_int(row["output_tokens"]),
            "cost_usd": safe_float_text(row["cost_usd"]),
        }
        if usage["requests"] or usage["input_tokens"] or usage["output_tokens"]:
            return usage
        return self.request_log_usage(con)

    def request_log_usage(self, con: sqlite3.Connection) -> dict[str, Any]:
        start = now_local().replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + dt.timedelta(days=1)
        start_s = int(start.timestamp())
        end_s = int(end.timestamp())
        start_ms = start_s * 1000
        end_ms = end_s * 1000
        row = con.execute(
            """
            select
              count(*) as requests,
              coalesce(sum(input_tokens), 0) as input_tokens,
              coalesce(sum(output_tokens), 0) as output_tokens,
              coalesce(sum(total_cost_usd), 0) as cost_usd
            from proxy_request_logs
            where (created_at >= ? and created_at < ?)
               or (created_at >= ? and created_at < ?)
            """,
            (start_s, end_s, start_ms, end_ms),
        ).fetchone()
        if row is None:
            return self.empty_usage()
        return {
            "requests": safe_int(row["requests"]),
            "input_tokens": safe_int(row["input_tokens"]),
            "output_tokens": safe_int(row["output_tokens"]),
            "cost_usd": safe_float_text(row["cost_usd"]),
        }

    def request_activity(self, con: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = con.execute(
            """
            select created_at, app_type, model, status_code, total_cost_usd, error_message
            from proxy_request_logs
            order by created_at desc
            limit 5
            """
        ).fetchall()
        events = []
        for row in rows:
            created = safe_int(row["created_at"])
            # cc-switch timestamps are milliseconds in current builds.
            timestamp = created / 1000 if created > 10_000_000_000 else created
            event_time = dt.datetime.fromtimestamp(timestamp).astimezone()
            status = "ok" if safe_int(row["status_code"], 200) < 400 else f"http {row['status_code']}"
            if row["error_message"]:
                status = truncate(str(row["error_message"]), 28)
            text = f"{row['app_type'] or 'agent'} request {status}"
            if row["model"]:
                text += f" 路 {row['model']}"
            if row["total_cost_usd"]:
                text += f" 路 ${safe_float_text(row['total_cost_usd'])}"
            events.append({"time": event_time.strftime("%H:%M"), "text": text, "_sort": event_time.timestamp()})
        return events

    def claude_state(self, session_id: str | None = None) -> dict[str, Any]:
        files = sorted(self.claude_project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if self.claude_project_dir.exists() else []
        if not files:
            return {"_source_status": "error", "_error": f"missing {self.claude_project_dir}"}

        selected = next((path for path in files if path.stem == session_id), files[0])
        rows = read_jsonl_tail(selected, max_lines=350)
        selected_id = selected.stem
        sessions = []
        for path in files[:4]:
            updated = dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone()
            sessions.append({"id": path.stem, "name": f"Claude {path.stem[:6]}", "state": "active" if (now_local() - updated).total_seconds() < 7200 else "idle", "updated_at": updated.strftime("%H:%M"), "selected": path.stem == selected_id})

        latest_reply = "Unavailable"
        model_name = "Claude Code"
        used_tokens = 0
        activity: list[dict[str, Any]] = []
        project = None
        for row in reversed(rows):
            ts = parse_iso(row.get("timestamp"))
            sort_key = ts.timestamp() if ts else time.time() - len(activity)
            if not project and row.get("cwd"):
                project = {"name": Path(str(row.get("cwd"))).name, "path": str(row.get("cwd")), "branch": row.get("gitBranch") or "--"}
            msg = row.get("message") if isinstance(row.get("message"), dict) else {}
            if msg.get("model") and model_name == "Claude Code":
                model_name = str(msg.get("model"))
            usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
            used_tokens = used_tokens or safe_int(usage.get("input_tokens")) + safe_int(usage.get("output_tokens"))
            text = self.extract_text(msg.get("content"))
            if row.get("type") == "assistant" and latest_reply == "Unavailable" and text:
                latest_reply = truncate(text, 260)
            if len(activity) < 8:
                label = "assistant" if row.get("type") == "assistant" else "user" if row.get("type") == "user" else str(row.get("type") or "event")
                content = truncate(text, 46) if text else label
                activity.append({"time": ts.strftime("%H:%M") if ts else hhmm(), "text": f"{label}: {content}", "_sort": sort_key})
        return {"_source_status": "ok", "_selected_session_id": selected_id, "project": project, "model": {"provider": "Claude", "model": model_name, "app_type": "claude"}, "used_tokens": used_tokens, "usage_today": self.empty_usage(), "sessions": sessions, "latest_reply": latest_reply, "activity": activity}

    def hermes_state(self, session_id: str | None = None) -> dict[str, Any]:
        db_path = self.hermes_root / "state.db"
        gateway_path = self.hermes_root / "gateway_state.json"
        gateway_state = "unknown"
        if gateway_path.exists():
            try:
                gateway = json.loads(gateway_path.read_text(encoding="utf-8", errors="replace"))
                gateway_state = str(gateway.get("gateway_state") or "unknown")
            except (OSError, json.JSONDecodeError):
                gateway_state = "unknown"
        if not db_path.exists():
            return {"_source_status": "error", "_error": f"missing {db_path}"}
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
            con.row_factory = sqlite3.Row
            active_rows = con.execute("""select id, source, model, title, started_at, ended_at, message_count, tool_call_count, input_tokens, output_tokens, cache_read_tokens, reasoning_tokens, cwd, git_branch, git_repo_root, session_key from sessions where ended_at is null and coalesce(archived, 0) = 0 order by started_at desc limit 5""").fetchall()
            latest_sessions = con.execute("""select id, source, model, title, started_at, ended_at, message_count, tool_call_count, input_tokens, output_tokens, cache_read_tokens, reasoning_tokens, cwd, git_branch, git_repo_root, session_key from sessions where coalesce(archived, 0) = 0 order by started_at desc limit 5""").fetchall()
            session_rows = active_rows or latest_sessions
            selected_row = next((row for row in session_rows if row["id"] == session_id), session_rows[0] if session_rows else None)
            selected_id = selected_row["id"] if selected_row else None
            latest_messages = con.execute("""select session_id, role, content, timestamp, tool_name from messages where session_id = ? order by timestamp desc limit 12""", (selected_id,)).fetchall() if selected_id else []
            latest_reply_row = con.execute("""select content, timestamp from messages where session_id = ? and role = 'assistant' and content is not null and length(content) > 0 order by timestamp desc limit 1""", (selected_id,)).fetchone() if selected_id else None
        except sqlite3.Error as exc:
            return {"_source_status": "error", "_error": str(exc)}
        finally:
            try:
                con.close()
            except Exception:
                pass
        model = selected_row["model"] if selected_row and selected_row["model"] else "Hermes Agent"
        title = selected_row["title"] if selected_row and selected_row["title"] else "Hermes"
        cwd = selected_row["cwd"] if selected_row and selected_row["cwd"] else str(self.hermes_root)
        branch = selected_row["git_branch"] if selected_row and selected_row["git_branch"] else gateway_state
        input_tokens = safe_int(selected_row["input_tokens"]) if selected_row else 0
        output_tokens = safe_int(selected_row["output_tokens"]) if selected_row else 0
        cache_tokens = safe_int(selected_row["cache_read_tokens"]) if selected_row else 0
        message_count = safe_int(selected_row["message_count"]) if selected_row else 0
        sessions = []
        for row in session_rows[:4]:
            started = dt.datetime.fromtimestamp(float(row["started_at"] or time.time())).astimezone()
            state = "active" if row["ended_at"] is None else "idle"
            name = row["title"] or row["source"] or row["id"]
            sessions.append({"id": row["id"], "name": truncate(str(name), 22), "state": state, "updated_at": started.strftime("%H:%M"), "selected": row["id"] == selected_id})
        latest_reply = truncate(str(latest_reply_row["content"]), 260) if latest_reply_row and latest_reply_row["content"] else "Unavailable"
        activity = []
        for row in latest_messages:
            ts = dt.datetime.fromtimestamp(float(row["timestamp"] or time.time())).astimezone()
            content = row["content"] or row["tool_name"] or ""
            label = f"tool {row['tool_name']}" if row["tool_name"] else str(row["role"] or "event")
            activity.append({"time": ts.strftime("%H:%M"), "text": f"{label}: {truncate(str(content), 46)}" if content else label, "_sort": ts.timestamp()})
        status = "ok" if gateway_state == "running" or session_rows else "stale"
        return {"_source_status": status, "_selected_session_id": selected_id, "project": {"name": truncate(str(title), 24), "path": str(cwd), "branch": str(branch)}, "model": {"provider": "Hermes", "model": str(model), "app_type": "hermes"}, "used_tokens": input_tokens + output_tokens, "usage_today": {"requests": message_count, "input_tokens": input_tokens + cache_tokens, "output_tokens": output_tokens, "cost_usd": "0"}, "sessions": sessions, "latest_reply": latest_reply, "activity": activity[:8] or [{"time": hhmm(), "text": f"Gateway {gateway_state}", "_sort": time.time()}]}

    def codex_state(self, session_id: str | None = None) -> dict[str, Any]:
        try:
            sessions = self.codex_sessions()
            session_file = self.codex_session_file(session_id) if session_id else self.latest_codex_session_file()
            selected_id = self.codex_session_id(session_file) if session_file else None
            latest_reply = "Unavailable"
            activity: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            if session_file:
                rows = read_jsonl_tail(session_file)
                latest_reply = self.latest_codex_reply(rows)
                activity = self.codex_activity(rows)
                metadata = self.codex_session_metadata(rows)
            for session in sessions:
                session["selected"] = session.get("id") == selected_id
            status = "ok" if session_file or sessions else "stale"
            return {"_source_status": status, "_selected_session_id": selected_id, "sessions": sessions, "latest_reply": latest_reply, "activity": activity, **metadata}
        except Exception as exc:
            return {"_source_status": "error", "_error": str(exc), "sessions": [{"id": "", "name": "Codex", "state": "error", "updated_at": "--:--", "selected": True}], "latest_reply": "Unavailable", "activity": []}

    def codex_session_id(self, path: Path | None) -> str | None:
        if not path:
            return None
        match = re.search(r"([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})", path.name, re.IGNORECASE)
        return match.group(1) if match else path.stem

    def codex_session_files(self, limit: int | None = None) -> list[Path]:
        pattern = str(self.codex_sessions_root / "**" / "*.jsonl")
        files = sorted((Path(item) for item in glob.glob(pattern, recursive=True)), key=lambda item: item.stat().st_mtime, reverse=True)
        return files[:limit] if limit else files

    def codex_sessions(self) -> list[dict[str, Any]]:
        index = {str(row.get("id")): row for row in read_jsonl_tail(self.codex_session_index, max_lines=80) if row.get("id")}
        sessions = []
        for path in self.codex_session_files(limit=4):
            session_id = self.codex_session_id(path) or path.stem
            row = index.get(session_id, {})
            updated = dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone()
            name = truncate(str(row.get("thread_name") or f"Codex {session_id[:6]}"), 28)
            sessions.append({"id": session_id, "name": name, "state": "active" if (now_local() - updated).total_seconds() < 7200 else "idle", "updated_at": updated.strftime("%H:%M"), "selected": False})
        return sessions

    def codex_session_file(self, session_id: str | None) -> Path | None:
        if not session_id:
            return self.latest_codex_session_file()
        return next((path for path in self.codex_session_files() if self.codex_session_id(path) == session_id), None)

    def latest_codex_session_file(self) -> Path | None:
        files = self.codex_session_files(limit=1)
        return files[0] if files else None
    def codex_session_metadata(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        project = None
        context_limit = 0
        for row in rows:
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            if row.get("type") == "session_meta":
                cwd = payload.get("cwd")
                if cwd:
                    project = {"name": Path(str(cwd)).name, "path": str(cwd), "branch": "--"}
            value = payload.get("model_context_window") or payload.get("context_window")
            if isinstance(value, dict):
                value = value.get("max_tokens") or value.get("limit")
            context_limit = context_limit or safe_int(value)
        state: dict[str, Any] = {}
        if project:
            state["project"] = project
        if context_limit:
            state["context_limit"] = context_limit
        return state

    def latest_codex_reply(self, rows: list[dict[str, Any]]) -> str:
        for row in reversed(rows):
            payload = row.get("payload")
            if not isinstance(payload, dict):
                continue
            if row.get("type") not in {"response_item", "agent_message", "assistant_message"}:
                continue

            text = self.extract_text(payload)
            if text:
                return truncate(text, 260)
        return "Unavailable"

    def codex_activity(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for row in reversed(rows):
            if len(events) >= 8:
                break
            ts = parse_iso(str(row.get("timestamp") or ""))
            sort_key = ts.timestamp() if ts else time.time() - len(events)
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            typ = str(row.get("type") or payload.get("type") or "event")

            text = self.activity_text(typ, payload)
            if text:
                events.append({"time": ts.strftime("%H:%M") if ts else hhmm(), "text": text, "_sort": sort_key})
        return events

    def activity_text(self, typ: str, payload: dict[str, Any]) -> str:
        if typ == "event_msg":
            return truncate(str(payload.get("message") or payload.get("msg") or ""), 56)
        if typ == "response_item":
            item_type = str(payload.get("type") or "")
            name = payload.get("name") or payload.get("call_id") or ""
            if item_type:
                return truncate(f"{item_type} {name}".strip(), 56)
            text = self.extract_text(payload)
            return truncate(text, 56) if text else ""
        if typ in {"tool_call", "function_call"}:
            return truncate(f"tool: {payload.get('name') or payload.get('tool') or 'call'}", 56)
        if typ in {"turn_context", "session_meta"}:
            return ""
        text = self.extract_text(payload)
        return truncate(text or typ, 56)

    def extract_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = [self.extract_text(item) for item in value]
            return " ".join(part for part in parts if part)
        if isinstance(value, dict):
            for key in ("text", "message", "content", "output", "body"):
                if key in value:
                    text = self.extract_text(value[key])
                    if text:
                        return text
            # OpenAI response items often keep text inside content blocks.
            if value.get("type") in {"message", "output_text"}:
                for key in ("content", "text"):
                    text = self.extract_text(value.get(key))
                    if text:
                        return text
        return ""

    def empty_usage(self) -> dict[str, Any]:
        return {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}


class AgentBuddyHandler(SimpleHTTPRequestHandler):
    state_builder: AgentBuddyState

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        path = urlparse(self.path).path
        if path == "/api/state":
            self.handle_state()
            return
        if path == "/":
            self.path = "/index.html"
        elif path == "/portrait":
            self.path = "/portrait.html"
        elif path == "/kindle":
            self.path = "/kindle.html"
        super().do_GET()

    def handle_state(self) -> None:
        try:
            query = parse_qs(urlparse(self.path).query)
            agent = (query.get("agent") or ["codex"])[0]
            session_id = (query.get("session") or [None])[0]
            state = self.state_builder.build(agent, session_id)
            status = 200
        except Exception as exc:
            state = {
                "app": {"name": APP_NAME, "status": "error", "updated_at": hhmmss()},
                "error": html.escape(str(exc)),
            }
            status = 500

        body = json.dumps(state, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{hhmmss()} {self.address_string()} {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Buddy 960x480 local status screen")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--workspace", default=r"D:\Cursor-code")
    parser.add_argument("--context-limit", type=int, default=DEFAULT_CONTEXT_LIMIT)
    args = parser.parse_args()

    AgentBuddyHandler.state_builder = AgentBuddyState(
        workspace=Path(args.workspace),
        home=Path.home(),
        context_limit=args.context_limit,
    )

    server = ThreadingHTTPServer((args.host, args.port), AgentBuddyHandler)
    print(f"Agent Buddy Screen running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Agent Buddy Screen.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

