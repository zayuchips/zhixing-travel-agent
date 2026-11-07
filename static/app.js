/* 智行 · 出行规划智能体 —— 前端（原生 JS，无构建步骤）
 *
 * 安全约定：所有来自服务端/用户的文本先 escapeHtml 再拼进 HTML，
 * 只有我们自己生成的标签（<b>、<br>）才用 innerHTML。这是 V1.0 就定下的规矩，
 * 需求文档「非功能需求-安全性」一节对应这条。
 */
(function () {
  "use strict";

  var state = { user: null, sessionId: null, sessions: [], busy: false };

  var $ = function (id) { return document.getElementById(id); };

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /* 极简 Markdown：先转义，再把 **加粗** 变成 <b>，保留换行 */
  function md(text) {
    var safe = escapeHtml(text);
    safe = safe.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
    safe = safe.replace(/`([^`\n]+)`/g, '<code style="background:#0d1626;padding:1px 5px;border-radius:5px">$1</code>');
    return safe;
  }

  function api(path, opts) {
    opts = opts || {};
    return fetch(path, {
      method: opts.method || "GET",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: opts.body ? JSON.stringify(opts.body) : undefined
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.status === 401) { showAuth(); throw new Error(j.error || "未登录"); }
        if (!r.ok || j.ok === false) { throw new Error(j.error || ("请求失败 " + r.status)); }
        return j;
      });
    });
  }

  /* ---------------- 登录 ---------------- */
  var authMode = "login";

  function showAuth() {
    $("app").classList.add("hidden");
    $("auth").classList.remove("hidden");
  }
  function showApp() {
    $("auth").classList.add("hidden");
    $("app").classList.remove("hidden");
  }

  Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (t) {
    t.addEventListener("click", function () {
      authMode = t.dataset.tab;
      Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (x) { x.classList.remove("active"); });
      t.classList.add("active");
      $("auth-submit").textContent = authMode === "login" ? "登录" : "注册并登录";
      $("auth-msg").textContent = authMode === "login"
        ? "老师/助教可直接用体验账号 demo / demo1234"
        : "注册后会自动登录；用户名可用昵称";
      $("auth-msg").classList.remove("error");
    });
  });

  $("auth-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var u = $("username").value.trim(), p = $("password").value;
    var path = authMode === "login" ? "/api/login" : "/api/register";
    $("auth-submit").disabled = true;
    api(path, { method: "POST", body: { username: u, password: p } })
      .then(function () { return authMode === "login" ? null : api("/api/login", { method: "POST", body: { username: u, password: p } }); })
      .then(boot)
      .catch(function (err) {
        $("auth-msg").textContent = err.message;
        $("auth-msg").classList.add("error");
      })
      .then(function () { $("auth-submit").disabled = false; });
  });

  /* ---------------- 启动 ---------------- */
  function boot() {
    return api("/api/me").then(function (r) {
      state.user = r.user;
      showApp();
      loadStatus();
      loadSessions();
    });
  }

  function loadStatus() {
    api("/api/status").then(function (r) {
      $("engine").innerHTML = r.engine.llm_enabled
        ? "理解层：<b>大模型已接入</b>（" + escapeHtml(r.engine.model || "") + "）"
        : "理解层：<b>规则引擎</b>（无需密钥）";
      $("chip-tools").textContent = r.tools + " 个 MCP 工具";
      $("chip-status").textContent = "在线 · v" + r.version;
    }).catch(function () { $("chip-status").textContent = "离线"; });
  }

  function loadSessions() {
    return api("/api/sessions").then(function (r) {
      state.sessions = r.sessions || [];
      var box = $("sessions");
      box.innerHTML = "";
      state.sessions.forEach(function (s) {
        var d = document.createElement("div");
        d.className = "session" + (s.id === state.sessionId ? " active" : "");
        d.textContent = s.title || "未命名对话";
        d.title = s.updated_at;
        d.addEventListener("click", function () { openSession(s.id); });
        box.appendChild(d);
      });
      if (!state.sessions.length) { newChat(); }
      else if (!state.sessionId) { openSession(state.sessions[0].id); }
    });
  }

  function newChat() {
    state.sessionId = null;
    $("chat-title").textContent = "新对话";
    $("chat-sub").textContent = "说一句话，我去查天气、算路线、排行程";
    var box = $("messages");
    box.innerHTML = "";
    renderWelcome();
    Array.prototype.forEach.call(document.querySelectorAll(".session"), function (el) { el.classList.remove("active"); });
    $("input").focus();
  }

  function openSession(id) {
    api("/api/sessions/" + id + "/messages").then(function (r) {
      state.sessionId = id;
      var box = $("messages");
      box.innerHTML = "";
      var s = state.sessions.filter(function (x) { return x.id === id; })[0];
      $("chat-title").textContent = s ? s.title : "对话";
      $("chat-sub").textContent = "共 " + r.messages.length + " 条消息";
      (r.messages || []).forEach(function (m) { renderMessage(m.role, m.content, m.steps, m.cards); });
      Array.prototype.forEach.call(document.querySelectorAll(".session"), function (el) {
        el.classList.toggle("active", el.textContent === (s ? s.title : ""));
      });
      box.scrollTop = box.scrollHeight;
    });
  }

  $("new-chat").addEventListener("click", newChat);
  $("logout").addEventListener("click", function () {
    api("/api/logout", { method: "POST" }).then(function () { location.reload(); });
  });
  $("export").addEventListener("click", function () {
    if (!state.sessionId) { alert("先开始一段对话再导出"); return; }
    window.open("/api/sessions/" + state.sessionId + "/export", "_blank");
  });

  /* ---------------- 渲染 ---------------- */
  function renderWelcome() {
    var el = document.createElement("div");
    el.className = "msg";
    el.innerHTML =
      '<div class="avatar">智</div><div class="bubble">' +
      "我是<b>智行</b>。你说人话，我去调真实接口干活：<br>" +
      "• <b>查天气</b> —— 长沙未来三天会不会下雨<br>" +
      "• <b>算路线</b> —— 从广州到长沙多远、开车多久<br>" +
      "• <b>排行程</b> —— 帮我排长沙 3 天行程，预算 1500，想逛博物馆和吃小吃<br><br>" +
      '天气与路线是实时数据（open-meteo / OSRM），每次调用了哪个工具、花了多久，都能点开「思考过程」核对。' +
      "</div>";
    $("messages").appendChild(el);
  }

  function renderMessage(role, content, steps, cards) {
    var el = document.createElement("div");
    el.className = "msg " + role;
    var avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = role === "user" ? (state.user ? state.user.username.slice(0, 1).toUpperCase() : "我") : "智";
    el.appendChild(avatar);

    var bubble = document.createElement("div");
    bubble.className = "bubble" + (role === "user" ? " me" : "");
    bubble.innerHTML = md(content);
    el.appendChild(bubble);

    if (role === "assistant") {
      if (steps && steps.length) { bubble.appendChild(renderSteps(steps)); }
      if (cards && cards.length) { bubble.appendChild(renderCards(cards)); }
    }
    $("messages").appendChild(el);
    $("messages").scrollTop = $("messages").scrollHeight;
    return el;
  }

  function renderSteps(steps) {
    var wrap = document.createElement("details");
    wrap.className = "steps";
    var tools = steps.filter(function (s) { return s.kind === "tool"; });
    var summary = document.createElement("summary");
    summary.textContent = "思考过程 · " + steps.length + " 步" +
      (tools.length ? "（调用了 " + tools.map(function (s) { return s.tool; }).join("、") + "）" : "");
    wrap.appendChild(summary);

    steps.forEach(function (s) {
      var d = document.createElement("div");
      d.className = "step " + (s.kind || "");
      var t = document.createElement("div");
      t.className = "t";
      t.textContent = "› " + (s.title || "");
      if (s.elapsed_ms) {
        var ms = document.createElement("span");
        ms.className = "ms";
        ms.textContent = s.elapsed_ms + "ms" + (s.ok === false ? " · 失败" : "");
        t.appendChild(ms);
      }
      d.appendChild(t);
      var detail = s.detail || s.summary;
      if (s.arguments && Object.keys(s.arguments).length) {
        detail = "参数 " + JSON.stringify(s.arguments) + (detail ? "\n" + detail : "");
      }
      if (detail) {
        var dd = document.createElement("div");
        dd.className = "d";
        dd.textContent = detail;
        d.appendChild(dd);
      }
      wrap.appendChild(d);
    });
    return wrap;
  }

  function renderCards(cards) {
    var box = document.createElement("div");
    box.className = "cards";
    cards.forEach(function (c) {
      if (c.type === "weather") { box.appendChild(weatherCard(c.data)); }
      else if (c.type === "route") { box.appendChild(routeCard(c.data)); }
      else if (c.type === "itinerary") { box.appendChild(planCard(c.data)); }
    });
    return box;
  }

  function weatherCard(d) {
    var el = document.createElement("div");
    el.className = "card";
    var rows = (d.days || []).map(function (w) {
      return '<div class="wrow"><span>' + escapeHtml(w.date.slice(5)) + " " + escapeHtml(w.weekday) + "</span>" +
        '<span class="r">' + escapeHtml(w.weather) + "</span>" +
        '<span class="m">' + w.tmin + "~" + w.tmax + "℃ · 降水 " + (w.rain_prob == null ? "-" : w.rain_prob) + "%</span>" +
        "<span>" + escapeHtml(w.advice.split("；")[0]) + "</span></div>";
    }).join("");
    el.innerHTML = '<div class="card-head"><b>天气预报 · ' + escapeHtml(d.city) + "</b><span>" +
      escapeHtml(d.region || "") + "</span></div>" + '<div class="card-body">' + rows + "</div>";
    return el;
  }

  function routeCard(d) {
    var el = document.createElement("div");
    el.className = "card";
    el.innerHTML =
      '<div class="card-head"><b>路线规划</b><span>' + escapeHtml(d.source) + "</span></div>" +
      '<div class="card-body"><div class="route">' +
      '<span>' + escapeHtml(d.origin) + '</span><span class="arrow">→</span><span>' + escapeHtml(d.destination) + "</span>" +
      '<span class="big">' + d.distance_km + " km</span>" +
      '<span class="meta">' + escapeHtml(d.mode_label) + "约 " + fmtDur(d.duration_min) +
      (d.estimated ? "（估算）" : "（实时路网）") + "</span></div>" +
      '<div class="meta" style="margin-top:8px">' + escapeHtml(d.advice || "") + "</div></div>";
    return el;
  }

  function planCard(d) {
    var el = document.createElement("div");
    el.className = "card";
    var head = '<div class="card-head"><b>' + escapeHtml(d.destination) + " · " + d.days_count + " 天行程</b><span>" +
      escapeHtml(d.weather_source || "") + "</span></div>";
    var days = (d.days || []).map(function (day) {
      var items = (day.items || []).map(function (it) {
        return '<div class="item"><span class="tm">' + escapeHtml(it.time) + "</span>" +
          '<span class="nm">' + escapeHtml(it.name) + "<small>" + escapeHtml(it.cat) + " · 约" + it.hours + "h</small></span>" +
          '<span class="cost">' + (it.cost ? it.cost + " 元" : "免费") + "</span>" +
          (it.tip ? '<span class="tip">' + escapeHtml(it.tip) + "</span>" : "") + "</div>";
      }).join("");
      var w = day.tmax != null ? "　" + escapeHtml(day.weather) + " " + day.tmin + "~" + day.tmax + "℃" : "";
      return '<div class="day"><h4>D' + day.index + " · " + escapeHtml(day.date) + " " + escapeHtml(day.weekday) +
        '<span class="tag">' + escapeHtml(day.theme || "") + "</span>" + w + "</h4>" + items +
        (day.note ? '<div class="item"><span></span><span class="tip" style="grid-column:2/4">› ' + escapeHtml(day.note) + "</span></div>" : "") +
        "</div>";
    }).join("");
    var s = d.summary || {};
    var sum = '<div class="sum"><span>门票 <b>' + s.ticket_cost + "</b> 元</span><span>餐饮 <b>" + s.food_cost +
      "</b> 元</span><span>住宿 <b>" + s.hotel_cost + "</b> 元</span><span>合计 <b>" + s.total_cost + "</b> 元</span>" +
      (s.budget ? "<span>预算 " + s.budget + " 元 · " + (s.budget_ok ? "<b>够用</b>" : "<b>偏紧</b>") + "</span>" : "") +
      "</div>";
    el.innerHTML = head + days + sum;
    return el;
  }

  function fmtDur(min) {
    min = Math.round(min || 0);
    if (min < 60) { return min + " 分钟"; }
    var h = Math.floor(min / 60), m = min % 60;
    return h + " 小时" + (m ? " " + m + " 分" : "");
  }

  /* ---------------- 发送 ---------------- */
  function send() {
    var input = $("input");
    var text = input.value.trim();
    if (!text || state.busy) { return; }
    state.busy = true;
    $("send").disabled = true;
    input.value = "";
    input.style.height = "auto";

    if (!document.querySelector(".msg")) { $("messages").innerHTML = ""; }
    renderMessage("user", text);

    var thinking = document.createElement("div");
    thinking.className = "msg";
    thinking.innerHTML = '<div class="avatar">智</div><div class="bubble"><span class="typing"><i></i><i></i><i></i></span> 正在调用工具，请稍等…</div>';
    $("messages").appendChild(thinking);
    $("messages").scrollTop = $("messages").scrollHeight;

    api("/api/chat", { method: "POST", body: { session_id: state.sessionId, message: text } })
      .then(function (r) {
        thinking.remove();
        state.sessionId = r.session_id;
        renderMessage("assistant", r.reply, r.steps, r.cards);
        if ($("chat-title").textContent === "新对话") { $("chat-title").textContent = text.slice(0, 18); }
        loadSessions();
      })
      .catch(function (err) {
        thinking.remove();
        renderMessage("assistant", "出错了：" + err.message + "\n（如果是网络问题，检查一下网络或稍后再试）");
      })
      .then(function () {
        state.busy = false;
        $("send").disabled = false;
        input.focus();
      });
  }

  $("send").addEventListener("click", send);
  $("input").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });
  $("input").addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 140) + "px";
  });
  Array.prototype.forEach.call(document.querySelectorAll(".quick button"), function (b) {
    b.addEventListener("click", function () {
      $("input").value = b.dataset.q;
      send();
    });
  });

  /* 自动登录（有 Cookie 就直接进） */
  boot().catch(function () { showAuth(); });
})();
