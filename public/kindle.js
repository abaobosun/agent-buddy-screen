(function () {
  var activeAgent = "codex";
  var selectedSessionId = "";
  var sessionsFingerprint = "";
  var activityFingerprint = "";

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
    var text = value == null ? "Unavailable" : String(value);
    if (el && el.textContent !== text) el.textContent = text;
  }

  function addClass(el, name) {
    if (el && el.className.indexOf(name) === -1) el.className += " " + name;
  }

  function removeClass(el, name) {
    if (!el) return;
    el.className = el.className.replace(new RegExp("\b" + name + "\b", "g"), "").replace(/s+/g, " ").replace(/^s|s$/g, "");
  }

  function renderSessions(sessions) {
    var root = byId("sessions");
    var visible = (sessions || []).slice(0, 3);
    var fingerprint = "";
    var i;
    if (!root) return;

    for (i = 0; i < visible.length; i++) {
      fingerprint += [
        visible[i].id,
        visible[i].name,
        visible[i].updated_at,
        visible[i].state,
        visible[i].selected
      ].join("|") + ";";
    }
    if (fingerprint === sessionsFingerprint) return;

    sessionsFingerprint = fingerprint;
    root.innerHTML = "";
    for (i = 0; i < visible.length; i++) {
      (function (session) {
        var item = document.createElement("button");
        var selected = Boolean(session.selected || (session.id && session.id === selectedSessionId));
        item.type = "button";
        item.className = "session" +
          (session.state === "active" ? " active" : "") +
          (selected ? " selected" : "");
        item.textContent = String(session.name || "Session") + "  " + String(session.updated_at || "");
        item.onclick = function () {
          if (!session.id) return;
          selectedSessionId = session.id;
          sessionsFingerprint = "";
          refresh();
        };
        root.appendChild(item);
      }(visible[i]));
    }
    if (!visible.length) root.innerHTML = '<div class="session">No sessions</div>';
  }

  function renderActivity(activity) {
    var root = byId("activity");
    var visible = (activity || []).slice(0, 3);
    var fingerprint = "";
    var i;
    if (!root) return;

    for (i = 0; i < visible.length; i++) {
      fingerprint += visible[i].time + "|" + visible[i].text + ";";
    }
    if (fingerprint === activityFingerprint) return;

    activityFingerprint = fingerprint;
    root.innerHTML = "";
    for (i = 0; i < visible.length; i++) {
      var item = document.createElement("div");
      var time = document.createElement("span");
      item.className = "activity-item";
      time.className = "activity-time";
      time.textContent = String(visible[i].time || "--:--") + " ";
      item.appendChild(time);
      item.appendChild(document.createTextNode(String(visible[i].text || "")));
      root.appendChild(item);
    }
    if (!visible.length) root.innerHTML = '<div class="activity-item">No activity</div>';
  }

  function renderSources(state) {
    var sources = state.sources || {};
    var parts = [];
    var key;
    for (key in sources) {
      if (Object.prototype.hasOwnProperty.call(sources, key)) {
        parts.push(key + " " + sources[key]);
      }
    }
    setText("sources", (parts.join(" | ") || "sources unavailable") + " | refresh 15s");
  }

  function renderState(state) {
    var app = state.app || {};
    var project = state.project || {};
    var model = state.model || {};
    var context = state.context || {};
    var usage = state.usage_today || {};
    var labels = state.labels || {};
    var metrics = state.metrics || {};
    var bar;
    var status;

    setText("project-label", labels.project || "PROJECT");
    setText("model-label", labels.model || "MODEL");
    setText("context-label", labels.context || "CONTEXT");
    setText("requests-label", labels.requests || "REQ");
    setText("tokens-label", labels.tokens || "TOKENS");
    setText("latest-reply-label", labels.latest_reply || "LATEST REPLY");
    setText("clock", String(app.updated_at || "--:--").slice(0, 5));

    status = byId("status");
    if (status) {
      setText("status", app.status || "error");
      status.className = "status " + (app.status || "error");
    }

    setText("project-path", project.path || "");
    setText("project-name", project.name || "Unavailable");
    setText("project-branch", project.detail || ("branch: " + (project.branch || "--")));
    setText("model-name", model.model || "Unavailable");
    setText("provider-name", (model.provider || "provider") + " | " + (model.app_type || "agent"));
    setText(
      "context-text",
      context.display_text ||
        (formatNumber(context.used_tokens || 0) + " / " +
          formatNumber(context.limit_tokens || 200000))
    );
    setText(
      "requests",
      metrics.requests == null ?
        (usage.requests == null ? 0 : usage.requests) :
        metrics.requests
    );
    setText(
      "tokens",
      metrics.tokens == null ?
        formatNumber((usage.input_tokens || 0) + (usage.output_tokens || 0)) :
        metrics.tokens
    );
    setText("latest-reply", state.latest_reply || "Unavailable");

    bar = byId("context-bar");
    if (bar) {
      bar.style.width =
        Math.max(0, Math.min(100, Number(context.percent || 0))) + "%";
    }

    selectedSessionId = app.selected_session_id || "";
    renderSessions(state.sessions || []);
    renderActivity(state.activity || []);
    renderSources(state);
  }

  function renderError(message) {
    var status = byId("status");
    if (status) status.className = "status error";
    setText("status", "error");
    setText("latest-reply", "Unable to refresh state: " + message);
  }

  function refresh() {
    var xhr = new XMLHttpRequest();
    var sessionQuery = selectedSessionId ?
      "&session=" + encodeURIComponent(selectedSessionId) :
      "";
    xhr.open(
      "GET",
      "/api/state?agent=" + encodeURIComponent(activeAgent) +
        sessionQuery + "&t=" + new Date().getTime(),
      true
    );
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) return;
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          renderState(JSON.parse(xhr.responseText));
        } catch (error) {
          renderError("JSON parse failed");
        }
      } else {
        renderError("HTTP " + xhr.status);
      }
    };
    xhr.onerror = function () {
      renderError("network error");
    };
    xhr.send(null);
  }

  function initTabs() {
    var buttons = document.querySelectorAll(".agent-tab");
    var i;
    for (i = 0; i < buttons.length; i++) {
      buttons[i].onclick = function () {
        var j;
        activeAgent = this.getAttribute("data-agent") || "codex";
        selectedSessionId = "";
        sessionsFingerprint = "";
        activityFingerprint = "";
        for (j = 0; j < buttons.length; j++) {
          removeClass(buttons[j], "active");
        }
        addClass(this, "active");
        refresh();
      };
    }
  }

  initTabs();
  refresh();
  setInterval(refresh, 15000);
}());