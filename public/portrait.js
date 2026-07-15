(function () {
  var activeAgent = "codex";
  var selectedSessionId = "";

  function byId(id) {
    return document.getElementById(id);
  }

  function formatNumber(value) {
    var number = Number(value || 0);
    if (number >= 1000000) return (number / 1000000).toFixed(1) + "M";
    if (number >= 1000) return (number / 1000).toFixed(1) + "K";
    return String(number);
  }

  function setText(id, value) {
    var el = byId(id);
    if (el) el.textContent = value == null ? "Unavailable" : String(value);
  }

  function addClass(el, name) {
    if (!el) return;
    if (el.className.indexOf(name) === -1) el.className += " " + name;
  }

  function removeClass(el, name) {
    if (!el) return;
    el.className = el.className.replace(new RegExp("\\b" + name + "\\b", "g"), "").replace(/\s+/g, " ").replace(/^\s|\s$/g, "");
  }

  function renderSessions(sessions) {
    var root = byId("sessions");
    if (!root) return;
    root.innerHTML = "";
    sessions = sessions || [];
    for (var i = 0; i < sessions.length && i < 5; i++) {
      (function (session) {
        var item = document.createElement("button");
        var selected = Boolean(session.selected || (session.id && session.id === selectedSessionId));
        item.type = "button";
        item.className = "session" + (session.state === "active" ? " active" : "") + (selected ? " selected" : "");
        item.textContent = String(session.name || "Session") + " " + String(session.updated_at || "");
        item.onclick = function () {
          if (!session.id) return;
          selectedSessionId = session.id;
          refresh();
        };
        root.appendChild(item);
      }(sessions[i]));
    }
    if (!root.children.length) root.innerHTML = '<div class="session">No sessions</div>';
  }

  function renderActivity(activity) {
    var root = byId("activity");
    if (!root) return;
    root.innerHTML = "";
    activity = activity || [];
    for (var i = 0; i < activity.length && i < 10; i++) {
      var event = activity[i];
      var item = document.createElement("div");
      item.className = "activity-item";
      var time = document.createElement("span");
      time.className = "activity-time";
      time.textContent = String(event.time || "--:--") + " ";
      item.appendChild(time);
      item.appendChild(document.createTextNode(String(event.text || "")));
      root.appendChild(item);
    }
    if (!root.children.length) root.innerHTML = '<div class="activity-item"><span class="activity-time">--:--</span> No activity</div>';
  }

  function renderSources(state) {
    var sources = state.sources || {};
    var parts = [];
    for (var key in sources) {
      if (Object.prototype.hasOwnProperty.call(sources, key)) parts.push(key + " " + sources[key]);
    }
    var errors = state.errors || [];
    setText("sources", (parts.join(" | ") || "sources unavailable") + " | refresh 5s" + (errors.length ? " | " + errors.join(" | ") : ""));
  }

  function renderState(state) {
    var app = state.app || {};
    var project = state.project || {};
    var model = state.model || {};
    var context = state.context || {};
    var usage = state.usage_today || {};
    var labels = state.labels || {};
    var metrics = state.metrics || {};

    setText("project-label", labels.project || "PROJECT");
    setText("model-label", labels.model || "MODEL");
    setText("context-label", labels.context || "CONTEXT");
    setText("requests-label", labels.requests || "REQ");
    setText("tokens-label", labels.tokens || "TOKENS");
    setText("latest-reply-label", labels.latest_reply || "LATEST REPLY");

    setText("clock", app.updated_at || "--:--:--");
    var status = byId("status");
    if (status) {
      status.textContent = app.status || "error";
      status.className = "status " + (app.status || "error");
    }

    setText("project-path", project.path || "");
    setText("project-name", project.name || "Unavailable");
    setText("project-branch", project.detail || ("branch: " + (project.branch || "--")));
    setText("model-name", model.model || "Unavailable");
    setText("provider-name", (model.provider || "provider") + " | " + (model.app_type || "agent"));
    setText("context-text", context.display_text || (formatNumber(context.used_tokens || 0) + " / " + formatNumber(context.limit_tokens || 200000)));

    var bar = byId("context-bar");
    if (bar) {
      var pct = Math.max(0, Math.min(100, Number(context.percent || 0)));
      bar.style.width = pct + "%";
    }

    setText("requests", metrics.requests == null ? (usage.requests == null ? 0 : usage.requests) : metrics.requests);
    setText("tokens", metrics.tokens == null ? formatNumber((usage.input_tokens || 0) + (usage.output_tokens || 0)) : metrics.tokens);
    setText("latest-reply", state.latest_reply || "Unavailable");
    selectedSessionId = app.selected_session_id || "";
    renderSessions(state.sessions || []);
    renderActivity(state.activity || []);
    renderSources(state);
  }

  function renderError(message) {
    var status = byId("status");
    if (status) {
      status.textContent = "error";
      status.className = "status error";
    }
    setText("latest-reply", "Unable to refresh state: " + message);
  }

  function refresh() {
    var xhr = new XMLHttpRequest();
    var sessionQuery = selectedSessionId ? "&session=" + encodeURIComponent(selectedSessionId) : "";
    xhr.open("GET", "/api/state?agent=" + encodeURIComponent(activeAgent) + sessionQuery + "&t=" + Date.now(), true);
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) return;
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          renderState(JSON.parse(xhr.responseText));
        } catch (err) {
          renderError("JSON parse failed");
        }
      } else {
        renderError("HTTP " + xhr.status);
      }
    };
    xhr.onerror = function () { renderError("network error"); };
    xhr.send(null);
  }

  function initTabs() {
    var buttons = document.querySelectorAll(".agent-tab");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].onclick = function () {
        activeAgent = this.getAttribute("data-agent") || "codex";
        selectedSessionId = "";
        for (var j = 0; j < buttons.length; j++) removeClass(buttons[j], "active");
        addClass(this, "active");
        refresh();
      };
    }
  }

  initTabs();
  refresh();
  setInterval(refresh, 5000);
}());
