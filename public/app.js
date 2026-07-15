const $ = (id) => document.getElementById(id);
let activeAgent = "codex";
let selectedSessionId = "";


const resizeScreen = () => {
  const screen = document.querySelector(".screen");
  if (!screen) return;
  const baseWidth = 240;
  const scale = window.innerWidth / baseWidth;
  const logicalHeight = Math.max(120, window.innerHeight / scale);
  screen.style.height = `${logicalHeight}px`;
  screen.style.transform = `scale(${scale})`;
};

const formatNumber = (value) => {
  const number = Number(value || 0);
  if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(1)}M`;
  if (number >= 1_000) return `${(number / 1_000).toFixed(1)}K`;
  return `${number}`;
};

const setText = (id, value) => {
  const el = $(id);
  if (el) el.textContent = value ?? "Unavailable";
};

const renderSessions = (sessions = []) => {
  const root = $("sessions");
  if (!root) return;
  root.innerHTML = "";
  if (!sessions.length) {
    root.innerHTML = '<div class="session">Codex unavailable</div>';
    return;
  }
  for (const session of sessions.slice(0, 3)) {
    const item = document.createElement("button");
    const selected = Boolean(session.selected || (session.id && session.id === selectedSessionId));
    item.type = "button";
    item.className = `session ${session.state === "active" ? "active" : ""} ${selected ? "selected" : ""}`;
    item.textContent = `${session.name} ${session.updated_at || ""}`;
    item.title = `Show session: ${session.name || "Session"}`;
    item.addEventListener("click", () => {
      if (!session.id) return;
      selectedSessionId = session.id;
      refresh();
    });
    root.appendChild(item);
  }
};

const renderActivity = (activity = []) => {
  const root = $("activity");
  if (!root) return;
  root.innerHTML = "";
  if (!activity.length) {
    root.innerHTML = '<div class="activity-item"><span class="activity-time">--:--</span> No activity</div>';
    return;
  }
  for (const event of activity.slice(0, 4)) {
    const item = document.createElement("div");
    item.className = "activity-item";
    const time = document.createElement("span");
    time.className = "activity-time";
    time.textContent = `${event.time || "--:--"} `;
    item.appendChild(time);
    item.append(document.createTextNode(event.text || ""));
    root.appendChild(item);
  }
};

const renderSources = (state) => {
  const sources = state.sources || {};
  const sourceText = Object.entries(sources)
    .map(([name, status]) => `${name} ${status}`)
    .join(" | ");
  const errors = (state.errors || []).join(" | ");
  setText("sources", `${sourceText || "sources unavailable"} | refresh 5s${errors ? ` | ${errors}` : ""}`);
};

const renderState = (state) => {
  const app = state.app || {};
  const project = state.project || {};
  const model = state.model || {};
  const context = state.context || {};
  const usage = state.usage_today || {};
  const labels = state.labels || {};
  const metrics = state.metrics || {};

  setText("project-label", labels.project || "PROJECT");
  setText("model-label", labels.model || "MODEL");
  setText("context-label", labels.context || "CONTEXT");
  setText("requests-label", labels.requests || "REQ");
  setText("tokens-label", labels.tokens || "TOKENS");
  setText("latest-reply-label", labels.latest_reply || "LATEST REPLY");

  setText("clock", app.updated_at || "--:--:--");
  const status = $("status");
  if (status) {
    status.textContent = app.status || "error";
    status.className = `status ${app.status || "error"}`;
  }

  setText("project-path", project.path || "");
  setText("project-name", project.name || "Unavailable");
  setText("project-branch", project.detail || `branch: ${project.branch || "--"}`);

  setText("model-name", model.model || "Unavailable");
  setText("provider-name", `${model.provider || "provider"} | ${model.app_type || "agent"}`);

  const used = Number(context.used_tokens || 0);
  const limit = Number(context.limit_tokens || 200000);
  setText("context-text", context.display_text || `${formatNumber(used)} / ${formatNumber(limit)}`);
  const bar = $("context-bar");
  if (bar) bar.style.width = `${Math.max(0, Math.min(100, Number(context.percent || 0)))}%`;

  selectedSessionId = app.selected_session_id || "";
  renderSessions(state.sessions || []);
  setText("approved", metrics.requests ?? usage.requests ?? 0);
  setText("denied", metrics.tokens ?? formatNumber((usage.input_tokens || 0) + (usage.output_tokens || 0)));
  setText("latest-reply", state.latest_reply || "Unavailable");
  renderActivity(state.activity || []);
  renderSources(state);
};

const renderError = (error) => {
  const status = $("status");
  if (status) {
    status.textContent = "error";
    status.className = "status error";
  }
  setText("latest-reply", `Unable to refresh state: ${error}`);
};


const initAgentTabs = () => {
  document.querySelectorAll(".agent-tab").forEach((button) => {
    button.addEventListener("click", () => {
      activeAgent = button.dataset.agent || "codex";
      selectedSessionId = "";
      document.querySelectorAll(".agent-tab").forEach((tab) => tab.classList.toggle("active", tab === button));
      refresh();
    });
  });
};

const refresh = async () => {
  try {
    const sessionQuery = selectedSessionId ? `&session=${encodeURIComponent(selectedSessionId)}` : "";
    const response = await fetch(`/api/state?agent=${activeAgent}${sessionQuery}`, { cache: "no-store" });
    const state = await response.json();
    renderState(state);
  } catch (error) {
    renderError(error.message || String(error));
  }
};

resizeScreen();
window.addEventListener("resize", resizeScreen);
initAgentTabs();
refresh();
setInterval(refresh, 5000);
