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
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


APP_NAME = "Agent Buddy"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
DEFAULT_CONTEXT_LIMIT = 200_000
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
        self._state_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._project_cache: tuple[float, dict[str, Any]] | None = None

    def build(self, agent: str = "codex") -> dict[str, Any]:
        agent = agent if agent in {"codex", "claude", "hermes"} else "codex"
        now = time.time()
        cached = self._state_cache.get(agent)
        if cached and now - cached[0] < 5:
            return cached[1]
        state = self._build_uncached(agent)
        self._state_cache[agent] = (now, state)
        return state

    def _build_uncached(self, agent: str = "codex") -> dict[str, Any]:
        agent = agent if agent in {"codex", "claude", "hermes"} else "codex"
        sources: dict[str, str] = {}
        errors: list[str] = []

        project = self.project_state()
        cc_state = self.cc_switch_state()
        sources["cc-switch"] = cc_state.pop("_source_status")
        if cc_state.get("_error"):
            errors.append(f"cc-switch: {cc_state['_error']}")

        if agent == "claude":
            agent_state = self.claude_state()
            sources["claude"] = agent_state.pop("_source_status")
            project = agent_state.get("project") or project
        elif agent == "hermes":
            agent_state = self.hermes_state()
            sources["hermes"] = agent_state.pop("_source_status")
            project = agent_state.get("project") or {"name": "Hermes", "path": str(self.hermes_root), "branch": "gateway"}
        else:
            agent_state = self.codex_state()
            sources["codex"] = agent_state.pop("_source_status")

        if agent_state.get("_error"):
            errors.append(f"{agent}: {agent_state['_error']}")

        usage = agent_state.get("usage_today") or cc_state.get("usage_today") or self.empty_usage()
        used_tokens = safe_int(agent_state.get("used_tokens"))
        if not used_tokens:
            used_tokens = safe_int(usage.get("input_tokens")) + safe_int(usage.get("output_tokens"))
        percent = min(100, round((used_tokens / max(1, self.context_limit)) * 100))

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
            },
            "project": project,
            "model": model,
            "context": {
                "used_tokens": used_tokens,
                "limit_tokens": self.context_limit,
                "percent": percent,
                "estimated": True,
            },
            "usage_today": usage,
            "sessions": agent_state.get("sessions") or [
                {"name": agent.title(), "state": "unavailable", "updated_at": "--:--"}
            ],
            "counters": {"approved": 0, "denied": 0},
            "latest_reply": latest_reply,
            "activity": activity,
            "sources": sources,
            "errors": errors[:3],
        }

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

    def claude_state(self) -> dict[str, Any]:
        files = sorted(self.claude_project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if self.claude_project_dir.exists() else []
        if not files:
            return {"_source_status": "error", "_error": f"missing {self.claude_project_dir}"}
        latest = files[0]
        rows = read_jsonl_tail(latest, max_lines=350)
        sessions = []
        for path in files[:3]:
            updated = dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone()
            sessions.append({"name": f"Claude {path.stem[:6]}", "state": "active" if (now_local() - updated).total_seconds() < 7200 else "idle", "updated_at": updated.strftime("%H:%M")})
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
            role = msg.get("role") or row.get("type")
            text = self.extract_text(msg.get("content"))
            if row.get("type") == "assistant" and latest_reply == "Unavailable" and text:
                latest_reply = truncate(text, 260)
            if len(activity) < 8:
                label = "assistant" if row.get("type") == "assistant" else "user" if row.get("type") == "user" else str(row.get("type") or "event")
                content = truncate(text, 46) if text else label
                activity.append({"time": ts.strftime("%H:%M") if ts else hhmm(), "text": f"{label}: {content}", "_sort": sort_key})
        return {
            "_source_status": "ok",
            "project": project,
            "model": {"provider": "Claude", "model": model_name, "app_type": "claude"},
            "used_tokens": used_tokens,
            "usage_today": self.empty_usage(),
            "sessions": sessions,
            "latest_reply": latest_reply,
            "activity": activity,
        }

    def hermes_state(self) -> dict[str, Any]:
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
            active_rows = con.execute(
                """
                select id, source, model, title, started_at, ended_at, message_count,
                       tool_call_count, input_tokens, output_tokens, cache_read_tokens,
                       reasoning_tokens, cwd, git_branch, git_repo_root, session_key
                from sessions
                where ended_at is null and coalesce(archived, 0) = 0
                order by started_at desc
                limit 5
                """
            ).fetchall()
            latest_sessions = con.execute(
                """
                select id, source, model, title, started_at, ended_at, message_count,
                       tool_call_count, input_tokens, output_tokens, cache_read_tokens,
                       reasoning_tokens, cwd, git_branch, git_repo_root, session_key
                from sessions
                where coalesce(archived, 0) = 0
                order by started_at desc
                limit 5
                """
            ).fetchall()
            session_rows = active_rows or latest_sessions
            latest_messages = con.execute(
                """
                select session_id, role, content, timestamp, tool_name
                from messages
                order by timestamp desc
                limit 12
                """
            ).fetchall()
            latest_reply_row = con.execute(
                """
                select session_id, content, timestamp
                from messages
                where role = 'assistant' and content is not null and length(content) > 0
                order by timestamp desc
                limit 1
                """
            ).fetchone()
        except sqlite3.Error as exc:
            return {"_source_status": "error", "_error": str(exc)}
        finally:
            try:
                con.close()
            except Exception:
                pass

        active_count = len(active_rows)
        primary = session_rows[0] if session_rows else None
        model = primary["model"] if primary and primary["model"] else "Hermes Agent"
        title = primary["title"] if primary and primary["title"] else "Hermes"
        cwd = primary["cwd"] if primary and primary["cwd"] else str(self.hermes_root)
        branch = primary["git_branch"] if primary and primary["git_branch"] else gateway_state
        input_tokens = sum(safe_int(row["input_tokens"]) for row in session_rows)
        output_tokens = sum(safe_int(row["output_tokens"]) for row in session_rows)
        cache_tokens = sum(safe_int(row["cache_read_tokens"]) for row in session_rows)
        api_calls = sum(safe_int(row["message_count"]) for row in session_rows)
        tool_calls = sum(safe_int(row["tool_call_count"]) for row in session_rows)

        sessions = []
        for row in session_rows[:4]:
            started = dt.datetime.fromtimestamp(float(row["started_at"] or time.time())).astimezone()
            state = "active" if row["ended_at"] is None else "idle"
            name = row["title"] or row["source"] or row["id"]
            sessions.append({"name": truncate(str(name), 22), "state": state, "updated_at": started.strftime("%H:%M")})

        latest_reply = "Unavailable"
        if latest_reply_row and latest_reply_row["content"]:
            latest_reply = truncate(str(latest_reply_row["content"]), 260)

        activity = []
        for row in latest_messages:
            ts = dt.datetime.fromtimestamp(float(row["timestamp"] or time.time())).astimezone()
            role = row["role"] or "event"
            content = row["content"] or row["tool_name"] or ""
            if row["tool_name"]:
                label = f"tool {row['tool_name']}"
            else:
                label = str(role)
            text = f"{label}: {truncate(str(content), 46)}" if content else label
            activity.append({"time": ts.strftime("%H:%M"), "text": text, "_sort": ts.timestamp()})

        status = "ok" if gateway_state == "running" or session_rows else "stale"
        return {
            "_source_status": status,
            "project": {"name": truncate(str(title), 24), "path": str(cwd), "branch": str(branch)},
            "model": {"provider": "Hermes", "model": str(model), "app_type": "hermes"},
            "used_tokens": input_tokens + output_tokens,
            "usage_today": {
                "requests": active_count,
                "input_tokens": input_tokens + cache_tokens,
                "output_tokens": output_tokens,
                "cost_usd": "0",
            },
            "sessions": sessions,
            "latest_reply": latest_reply,
            "activity": activity[:8] or [{"time": hhmm(), "text": f"Gateway {gateway_state}", "_sort": time.time()}],
        }

    def codex_state(self) -> dict[str, Any]:
        try:
            sessions = self.codex_sessions()
            session_file = self.latest_codex_session_file()
            latest_reply = "Unavailable"
            activity: list[dict[str, Any]] = []
            if session_file:
                rows = read_jsonl_tail(session_file)
                latest_reply = self.latest_codex_reply(rows)
                activity = self.codex_activity(rows)

            status = "ok" if session_file or sessions else "stale"
            return {
                "_source_status": status,
                "sessions": sessions,
                "latest_reply": latest_reply,
                "activity": activity,
            }
        except Exception as exc:
            return {
                "_source_status": "error",
                "_error": str(exc),
                "sessions": [{"name": "Codex", "state": "error", "updated_at": "--:--"}],
                "latest_reply": "Unavailable",
                "activity": [],
            }

    def codex_sessions(self) -> list[dict[str, str]]:
        rows = read_jsonl_tail(self.codex_session_index, max_lines=12)
        sessions = []
        for row in rows[-4:]:
            updated = parse_iso(row.get("updated_at"))
            name = truncate(str(row.get("thread_name") or row.get("id") or "Codex"), 28)
            sessions.append(
                {
                    "name": name,
                    "state": "active" if updated and (now_local() - updated).total_seconds() < 7200 else "idle",
                    "updated_at": updated.strftime("%H:%M") if updated else "--:--",
                }
            )
        return list(reversed(sessions))

    def latest_codex_session_file(self) -> Path | None:
        pattern = str(self.codex_sessions_root / "**" / "*.jsonl")
        files = [Path(p) for p in glob.glob(pattern, recursive=True)]
        if not files:
            return None
        return max(files, key=lambda p: p.stat().st_mtime)

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
        super().do_GET()

    def handle_state(self) -> None:
        try:
            query = parse_qs(urlparse(self.path).query)
            agent = (query.get("agent") or ["codex"])[0]
            state = self.state_builder.build(agent)
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

