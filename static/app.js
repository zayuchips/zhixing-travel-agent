/* 智行 · 出行规划智能体 —— 前端（原生 JS，无构建步骤）
 *
 * 安全约定：所有来自服务端/用户的文本先 escapeHtml 再拼进 HTML，
 * 只有我们自己生成的标签（<b>、<br>）才用 innerHTML。这是 V1.0 就定下的规矩，
 * 需求文档「非功能需求-安全性」一节对应这条。
 */
(function () {
  "use strict";

  var state = { user: null, sessionId: null, sessions: [], busy: false, attachments: [] };

  var $ = function (id) { return document.getElementById(id); };

  /* ---------------- 主题 ---------------- */
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("zx-theme", t); } catch (e) { /* 隐私模式忽略 */ }
  }

  (function initTheme() {
    var saved = null;
    try { saved = localStorage.getItem("zx-theme"); } catch (e) { saved = null; }
    if (!saved) {
      saved = (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches)
        ? "light" : "dark";
    }
    applyTheme(saved);
  })();

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /* ---------------- Markdown 渲染 ----------------
   * 服务端只返回纯文本，这里自己渲染。原则：**先转义再拼标签**，
   * 任何来自模型的文本都过 escapeHtml，只有我们生成的标签才保留。
   * 支持：标题、有序/无序列表、表格、代码块、行内代码、加粗、斜体、引用、分隔线、链接。
   */
  function inlineMd(s) {
    s = escapeHtml(s);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
    s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    return s;
  }

  function splitRow(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "")
      .split("|").map(function (c) { return c.trim(); });
  }

  function md(text) {
    var src = String(text == null ? "" : text).replace(/\r\n/g, "\n");
    var blocks = [];

    // 代码块先摘出来，免得里面的符号被当语法解析
    src = src.replace(/```[A-Za-z0-9_+-]*\n?([\s\S]*?)```/g, function (_m, code) {
      blocks.push('<pre class="md-code"><code>' +
        escapeHtml(code.replace(/\n$/, "")) + "</code></pre>");
      return "\u0000" + (blocks.length - 1) + "\u0000";
    });

    var lines = src.split("\n"), out = [], list = null, para = [];

    function flushPara() {
      if (para.length) { out.push("<p>" + inlineMd(para.join(" ")) + "</p>"); para = []; }
    }
    function flushList() {
      if (list) { out.push("<" + list.tag + ">" + list.items.join("") + "</" + list.tag + ">"); list = null; }
    }

    for (var i = 0; i < lines.length; i++) {
      var t = lines[i].trim();

      if (/^\u0000\d+\u0000$/.test(t)) { flushPara(); flushList(); out.push(t); continue; }
      if (!t) { flushPara(); flushList(); continue; }
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) { flushPara(); flushList(); out.push("<hr>"); continue; }

      var h = t.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        flushPara(); flushList();
        // 气泡里 h1/h2 太抢眼，统一从 h3 起
        var lv = Math.min(Math.max(h[1].length, 3), 5);
        out.push("<h" + lv + ">" + inlineMd(h[2]) + "</h" + lv + ">");
        continue;
      }

      if (/^>\s?/.test(t)) {
        flushPara(); flushList();
        out.push("<blockquote>" + inlineMd(t.replace(/^>\s?/, "")) + "</blockquote>");
        continue;
      }

      // 表格：本行 |a|b|，下一行是 |---|---|
      if (t.charAt(0) === "|" && i + 1 < lines.length &&
          /^\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1].trim())) {
        flushPara(); flushList();
        var head = splitRow(t), rows = [];
        i += 2;
        while (i < lines.length && lines[i].trim().charAt(0) === "|") {
          rows.push(splitRow(lines[i]));
          i++;
        }
        i--;
        var html = "<table class='md-table'><thead><tr>";
        head.forEach(function (c) { html += "<th>" + inlineMd(c) + "</th>"; });
        html += "</tr></thead><tbody>";
        rows.forEach(function (r) {
          html += "<tr>";
          head.forEach(function (_x, k) { html += "<td>" + inlineMd(r[k] || "") + "</td>"; });
          html += "</tr>";
        });
        out.push(html + "</tbody></table>");
        continue;
      }

      var ul = t.match(/^[-*+]\s+(.*)$/);
      if (ul) {
        flushPara();
        if (!list || list.tag !== "ul") { flushList(); list = { tag: "ul", items: [] }; }
        list.items.push("<li>" + inlineMd(ul[1]) + "</li>");
        continue;
      }

      var ol = t.match(/^\d+[.)]\s+(.*)$/);
      if (ol) {
        flushPara();
        if (!list || list.tag !== "ol") { flushList(); list = { tag: "ol", items: [] }; }
        list.items.push("<li>" + inlineMd(ol[1]) + "</li>");
        continue;
      }

      flushList();
      para.push(t);
    }
    flushPara(); flushList();

    return out.join("\n").replace(/\u0000(\d+)\u0000/g, function (_m, n) {
      return blocks[+n] || "";
    });
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
      loadSessions();
    });
  }

  function markActive() {
    Array.prototype.forEach.call(document.querySelectorAll(".session"), function (el) {
      el.classList.toggle("active", Number(el.dataset.sid) === Number(state.sessionId));
    });
  }

  function loadSessions() {
    return api("/api/sessions").then(function (r) {
      state.sessions = r.sessions || [];
      var box = $("sessions");
      box.innerHTML = "";
      if (!state.sessions.length) {
        box.innerHTML = '<div class="sessions-empty">还没有对话</div>';
      }
      state.sessions.forEach(function (s) {
        var row = document.createElement("div");
        row.className = "session";
        row.dataset.sid = s.id;

        var title = document.createElement("span");
        title.className = "session-title";
        title.textContent = s.title || "未命名对话";
        title.title = (s.title || "") + "　" + (s.updated_at || "");

        var more = document.createElement("button");
        more.className = "session-more";
        more.textContent = "⋯";
        more.title = "更多操作";
        more.setAttribute("aria-label", "更多操作");
        more.addEventListener("click", function (e) {
          e.stopPropagation();
          openSessionMenu(s, more);
        });

        row.appendChild(title);
        row.appendChild(more);
        row.addEventListener("click", function () { openSession(s.id); });
        box.appendChild(row);
      });

      if (!state.sessions.length) { newChat(); }
      else if (!state.sessionId) { openSession(state.sessions[0].id); }
      else { markActive(); }
    });
  }

  function newChat() {
    state.sessionId = null;
    $("chat-title").textContent = "新对话";
    $("chat-sub").textContent = "说一句话，我去查天气、算路线、排行程";
    var box = $("messages");
    box.innerHTML = "";
    renderWelcome();
    markActive();
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
      markActive();
      box.scrollTop = box.scrollHeight;
    });
  }

  /* ---------------- 会话操作浮层 ---------------- */
  var ctxMenu = null;

  function closeCtxMenu() {
    if (ctxMenu) { ctxMenu.remove(); ctxMenu = null; }
  }

  function openSessionMenu(s, anchor) {
    // 再点同一个 ⋯ 就收起（toggle）
    if (ctxMenu && ctxMenu.dataset.for === String(s.id)) { closeCtxMenu(); return; }
    closeCtxMenu();

    var menu = document.createElement("div");
    menu.className = "ctx-menu";
    menu.dataset.for = s.id;
    menu.innerHTML =
      '<button data-act="rename">重命名</button>' +
      '<button data-act="export">导出 Markdown</button>' +
      '<div class="sep"></div>' +
      '<button data-act="delete" class="danger">删除对话</button>';

    menu.addEventListener("click", function (e) {
      var act = e.target.dataset && e.target.dataset.act;
      if (!act) { return; }
      closeCtxMenu();

      if (act === "rename") {
        var name = prompt("新的对话名称", s.title || "");
        if (!name || !name.trim()) { return; }
        api("/api/sessions/" + s.id + "/rename", { method: "POST", body: { title: name.trim() } })
          .then(function () {
            if (Number(state.sessionId) === Number(s.id)) {
              $("chat-title").textContent = name.trim();
            }
            loadSessions();
          })
          .catch(function (err) { alert(err.message); });
        return;
      }

      if (act === "export") {
        window.open("/api/sessions/" + s.id + "/export", "_blank");
        return;
      }

      if (act === "delete") {
        if (!confirm("删除对话「" + (s.title || "未命名") + "」？删了找不回来。")) { return; }
        api("/api/sessions/" + s.id, { method: "DELETE" }).then(function () {
          if (Number(state.sessionId) === Number(s.id)) { state.sessionId = null; }
          loadSessions();
        }).catch(function (err) { alert(err.message); });
      }
    });

    document.body.appendChild(menu);
    var r = anchor.getBoundingClientRect();
    var w = menu.offsetWidth || 140;
    // 默认贴在会话项右侧；右边放不下就翻到左侧
    var left = r.right + 8;
    if (left + w > window.innerWidth - 8) { left = Math.max(8, r.left - w - 8); }
    menu.style.top = Math.round(r.top) + "px";
    menu.style.left = Math.round(left) + "px";
    menu.style.right = "auto";
    ctxMenu = menu;

    // 用 mousedown + capture 判断“点别处”：点菜单内部、点 ⋯ 自己都不算。
    // 用 click + once 会被触发本次点击的冒泡链立刻关掉，这就是之前“一点就消失”的原因。
    function onDocDown(ev) {
      if (menu.contains(ev.target)) { return; }
      if (ev.target.closest && ev.target.closest(".session-more")) { return; }
      document.removeEventListener("mousedown", onDocDown, true);
      closeCtxMenu();
    }
    setTimeout(function () {
      document.addEventListener("mousedown", onDocDown, true);
    }, 0);
  }

  $("theme-toggle").addEventListener("click", function () {
    var cur = document.documentElement.getAttribute("data-theme");
    applyTheme(cur === "dark" ? "light" : "dark");
  });

  $("new-chat").addEventListener("click", newChat);
  $("logout").addEventListener("click", function () {
    api("/api/logout", { method: "POST" }).then(function () { location.reload(); });
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
      "• <b>排行程</b> —— 帮我排长沙 3 天行程，预算 1500，想逛博物馆和吃小吃" +
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

  /* ---------------- 地图 ---------------- */
  // 高德 POI 是 GCJ-02（火星坐标），OSM 瓦片是 WGS-84。
  // 直接用会整体偏移 100-600 米，所以渲染前必须转换。这里用迭代反解，误差 < 1 米。
  var GCJ = (function () {
    var PI = 3.1415926535897932384626, A = 6378245.0, EE = 0.00669342162296594323;
    function outOfChina(lng, lat) {
      return lng < 72.004 || lng > 137.8347 || lat < 0.8293 || lat > 55.8271;
    }
    function tLat(x, y) {
      var r = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
      r += (20 * Math.sin(6 * x * PI) + 20 * Math.sin(2 * x * PI)) * 2 / 3;
      r += (20 * Math.sin(y * PI) + 40 * Math.sin(y / 3 * PI)) * 2 / 3;
      r += (160 * Math.sin(y / 12 * PI) + 320 * Math.sin(y * PI / 30)) * 2 / 3;
      return r;
    }
    function tLng(x, y) {
      var r = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
      r += (20 * Math.sin(6 * x * PI) + 20 * Math.sin(2 * x * PI)) * 2 / 3;
      r += (20 * Math.sin(x * PI) + 40 * Math.sin(x / 3 * PI)) * 2 / 3;
      r += (150 * Math.sin(x / 12 * PI) + 300 * Math.sin(x / 30 * PI)) * 2 / 3;
      return r;
    }
    function delta(lng, lat) {
      var dLat = tLat(lng - 105.0, lat - 35.0), dLng = tLng(lng - 105.0, lat - 35.0);
      var rad = lat / 180.0 * PI, magic = Math.sin(rad);
      magic = 1 - EE * magic * magic;
      var sq = Math.sqrt(magic);
      dLat = (dLat * 180.0) / ((A * (1 - EE)) / (magic * sq) * PI);
      dLng = (dLng * 180.0) / (A / sq * Math.cos(rad) * PI);
      return [dLng, dLat];
    }
    return {
      toWgs84: function (lng, lat) {
        if (outOfChina(lng, lat)) { return [lng, lat]; }
        var wLng = lng, wLat = lat;
        for (var i = 0; i < 3; i++) {
          var d = delta(wLng, wLat);
          wLng = lng - d[0];
          wLat = lat - d[1];
        }
        return [wLng, wLat];
      },
      toGcj: function (lng, lat) {
        if (outOfChina(lng, lat)) { return [lng, lat]; }
        var d = delta(lng, lat);
        return [lng + d[0], lat + d[1]];
      }
    };
  })();

  var DAY_COLORS = ["#2b6cb0", "#c05621", "#2f855a", "#6b46c1", "#b7791f", "#c53030", "#0987a0"];

  function buildMap(d) {
    var pts = [];
    (d.days || []).forEach(function (day) {
      (day.attractions || []).forEach(function (a) {
        if (a.lng == null || a.lat == null) { return; }
        // 瓦片用高德（国内可达、有中文标注），它和 POI 一样是 GCJ-02，直接画不必转换；
        // 换成 WGS-84 数据时才需要补一次正变换，否则会整体偏移几百米
        var ll = a.coord === "gcj02" ? [a.lng, a.lat] : GCJ.toGcj(a.lng, a.lat);
        pts.push({ lat: ll[1], lng: ll[0], name: a.name, day: day.index, time: a.time, addr: a.address || "" });
      });
    });
    if (!pts.length) {
      // 本地示例点位没有经纬度，画不出地图。说明白原因，别让人以为功能坏了
      if (d.poi_source === "local" || d.poi_source === "template") {
        var tip = document.createElement("div");
        tip.className = "map-tip";
        tip.textContent = "这些点位来自本地示例数据、没有经纬度，所以画不出地图。配置 AMAP_KEY 后，这里会显示按天连线、可点开的景点地图。";
        return tip;
      }
      return null;
    }

    var wrap = document.createElement("div");
    wrap.className = "map-wrap";
    if (typeof L === "undefined") {
      wrap.className = "map-tip";
      wrap.textContent = "地图组件未加载（Leaflet 走 CDN，需要联网）；行程本身不受影响。";
      return wrap;
    }

    var canvas = document.createElement("div");
    canvas.className = "map-canvas";
    wrap.appendChild(canvas);

    // 卡片刚插进 DOM 时还没有尺寸，等一帧再初始化，否则 Leaflet 算不出视图
    setTimeout(function () {
      var map = L.map(canvas, { scrollWheelZoom: false, attributionControl: true });
      // 高德瓦片：国内访问稳定，且自带中文标注；它和我们的 POI 同为 GCJ-02，坐标天然对齐
      L.tileLayer(
        "https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}",
        { subdomains: "1234", maxZoom: 18, attribution: "© 高德地图" }
      ).addTo(map);

      var bounds = [], byDay = {};
      pts.forEach(function (p) { (byDay[p.day] = byDay[p.day] || []).push(p); });

      Object.keys(byDay).forEach(function (day) {
        var color = DAY_COLORS[(parseInt(day, 10) - 1) % DAY_COLORS.length];
        var line = [];
        byDay[day].forEach(function (p, i) {
          var ll = [p.lat, p.lng];
          line.push(ll);
          bounds.push(ll);
          L.marker(ll, {
            icon: L.divIcon({
              className: "pin", iconSize: [24, 24], iconAnchor: [12, 12],
              html: '<span style="background:' + color + '">' + (i + 1) + "</span>"
            })
          }).addTo(map).bindPopup(
            "<b>D" + day + " · " + escapeHtml(p.name) + "</b><br>" +
            escapeHtml(p.time) + "　" + escapeHtml(p.addr)
          );
        });
        if (line.length > 1) {
          L.polyline(line, { color: color, weight: 3, opacity: 0.75, dashArray: "6 6" }).addTo(map);
        }
      });

      if (bounds.length) { map.fitBounds(bounds, { padding: [26, 26] }); }
      setTimeout(function () { map.invalidateSize(); }, 140);
    }, 0);
    return wrap;
  }

  function renderCards(cards) {
    var box = document.createElement("div");
    box.className = "cards";
    cards.forEach(function (c) {
      if (c.type === "weather") { box.appendChild(weatherCard(c.data)); }
      else if (c.type === "route") { box.appendChild(routeCard(c.data)); }
      else if (c.type === "itinerary") { box.appendChild(planCard(c.data)); }
      else if (c.type === "hotel") { box.appendChild(hotelCard(c.data)); }
      else if (c.type === "poi") { box.appendChild(poiCard(c.data)); }
      else if (c.type === "files") { box.appendChild(filesCard(c.data)); }
    });
    return box;
  }

  /* 交付物：排完行程自动生成的文件，放在卡片最下面 */
  function filesCard(d) {
    var el = document.createElement("div");
    el.className = "card files-card";
    var icons = {
      html: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" ' +
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
        '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"></path>' +
        '<path d="M14 3v5h5M9 13h6M9 17h4"></path></svg>',
      docx: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" ' +
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
        '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"></path>' +
        '<path d="M14 3v5h5M8.5 12l1.2 4 1.3-3 1.3 3 1.2-4"></path></svg>'
    };
    var rows = (d.items || []).map(function (f) {
      var kb = f.size ? Math.max(1, Math.round(f.size / 1024)) + " KB" : "";
      return '<a class="file-row" href="' + escapeHtml(f.url) + '" target="_blank" rel="noopener">' +
        '<span class="ficon ' + escapeHtml(f.kind) + '">' + (icons[f.kind] || "") + "</span>" +
        '<span class="fmeta"><b>' + escapeHtml(f.label || f.name) + "</b>" +
        "<small>" + escapeHtml(f.name) + (kb ? " · " + kb : "") + "</small></span>" +
        '<span class="fgo">打开</span></a>';
    }).join("");
    el.innerHTML =
      '<div class="card-head"><b>攻略文件 · ' + escapeHtml(d.destination || "") +
      (d.days ? " " + d.days + " 天" : "") + "</b><span>已生成</span></div>" +
      '<div class="card-body">' + rows +
      (d.note ? '<div class="meta">' + escapeHtml(d.note) + "</div>" : "") + "</div>";
    return el;
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
    var s = d.summary || {};
    var srcMap = { amap: "高德实时地点", local: "本地示例数据", template: "通用框架", empty: "未检索到地点" };
    var head = '<div class="card-head"><b>' + escapeHtml(d.destination) + " · " + d.days_count + " 天行程</b><span>" +
      escapeHtml(srcMap[d.poi_source] || "") + " · " + escapeHtml(d.weather_source || "") + "</span></div>" +
      '<div class="day-nav">' +
      (d.days || []).map(function (day) {
        return '<button data-jump="' + day.index + '">D' + day.index + "</button>";
      }).join("") +
      '<button data-act="export-menu" title="导出图片 / PDF / 完整攻略">导出</button>' +
      "</div>";

    var days = (d.days || []).map(function (day) {
      // 优先用结构化景点；拿不到就退回 items，兼容旧数据
      var body;
      if ((day.attractions || []).length) {
        body = day.attractions.map(function (a) {
          var img = (a.photos && a.photos.length)
            ? '<img class="spot-thumb" src="' + escapeHtml(a.photos[0]) + '" alt="" data-i="' +
              encodeURIComponent(JSON.stringify(a.photos.slice(0, 6))) +
              '" data-t="' + escapeHtml(a.name) + '">'
            : "";
          var meta = [];
          if (a.rating) { meta.push("★" + a.rating); }
          meta.push(a.ticket_price ? "门票 " + a.ticket_price + " 元" : "免费/未标价");
          if (a.address) { meta.push(a.address); }
          return '<div class="item has-edit"><span class="tm">' + escapeHtml(a.time) + "</span>" +
            '<span class="nm">' + img + escapeHtml(a.name) + "<small>" + escapeHtml(a.category || "") +
            " · 约 " + (Math.round((a.visit_duration || 120) / 6) / 10) + "h</small></span>" +
            '<span class="cost">' + escapeHtml(meta.join(" · ")) + "</span>" +
            '<span class="edit">' +
            '<button data-move="up" title="上移">↑</button>' +
            '<button data-move="down" title="下移">↓</button>' +
            '<button data-move="del" title="删除">×</button>' +
            "</span></div>";
        }).join("");
      } else {
        body = (day.items || []).map(function (it) {
          return '<div class="item"><span class="tm">' + escapeHtml(it.time) + "</span>" +
            '<span class="nm">' + escapeHtml(it.name) + "<small>" + escapeHtml(it.cat) + " · 约" + it.hours + "h</small></span>" +
            '<span class="cost">' + (it.cost ? it.cost + " 元" : "免费") + "</span>" +
            (it.tip ? '<span class="tip">' + escapeHtml(it.tip) + "</span>" : "") + "</div>";
        }).join("");
      }

      var meals = (day.meals || []).length
        ? '<div class="item"><span class="tm">三餐</span><span class="nm" style="font-size:13px">' +
          day.meals.map(function (m) {
            return escapeHtml(m.slot) + "：" + escapeHtml(m.name) + "（" + m.cost + " 元）";
          }).join("　") + "</span></div>"
        : "";

      var hotelHtml = "";
      if (day.hotel) {
        hotelHtml = '<div class="item"><span class="tm">住宿</span><span class="nm">' + escapeHtml(day.hotel.name) +
          "<small>" + escapeHtml(day.hotel.level || "") + " · 约 " + day.hotel.price_per_night + " 元/晚（估算）" +
          (day.hotel.distance_km != null ? " · 距首个景点 " + day.hotel.distance_km + " km" : "") + "</small></span></div>";
      } else if (day.accommodation) {
        hotelHtml = '<div class="item"><span class="tm">住宿</span><span class="nm"><small>' +
          escapeHtml(day.accommodation) + "</small></span></div>";
      }

      var w = day.tmax != null ? "　" + escapeHtml(day.weather) + " " + day.tmin + "~" + day.tmax + "℃" : "";
      return '<div class="day" data-day="' + day.index + '"><h4>D' + day.index + " · " + escapeHtml(day.date) + " " + escapeHtml(day.weekday) +
        '<span class="tag">' + escapeHtml(day.theme || "") + "</span>" + w +
        '<button class="day-edit" data-edit-day="' + day.index + '">调整</button></h4>' +
        body + meals + hotelHtml +
        (day.note ? '<div class="item"><span></span><span class="tip" style="grid-column:2/4">› ' + escapeHtml(day.note) + "</span></div>" : "") +
        "</div>";
    }).join("");

    var sum = '<div class="sum">' +
      "<span>门票 <b>" + (s.ticket_cost || 0) + "</b> 元</span>" +
      "<span>住宿 <b>" + (s.hotel_cost || 0) + "</b> 元</span>" +
      "<span>餐饮 <b>" + (s.food_cost || 0) + "</b> 元</span>" +
      "<span>交通 <b>" + (s.transport_cost || 0) + "</b> 元</span>" +
      "<span>合计 <b>" + (s.total_cost || 0) + "</b> 元</span>" +
      (s.budget ? "<span>预算 " + s.budget + " 元 · " + (s.budget_ok ? "<b>够用</b>" : "<b>偏紧</b>") + "</span>" : "") +
      "</div>";
    var notes = (s.notes || []).map(function (n) {
      return '<div class="meta" style="margin-top:4px;font-size:12px;opacity:.72">· ' + escapeHtml(n) + "</div>";
    }).join("");

    el.innerHTML = head + days + sum + notes;
    var map = buildMap(d);
    if (map) { el.appendChild(map); }
    bindPlanActions(el, d);
    return el;
  }

  /* 行程卡片的交互：天数跳转、导出、逐条调整。都用事件委托，卡片重建也不会丢监听 */
  function bindPlanActions(el, d) {
    var name = d.destination + "_" + d.days_count + "天行程";
    el.addEventListener("click", function (e) {
      var t = e.target;
      if (!t.dataset) { return; }

      if (t.dataset.jump) {
        var day = el.querySelector('[data-day="' + t.dataset.jump + '"]');
        if (day) { day.scrollIntoView({ behavior: "smooth", block: "start" }); }
        return;
      }
      if (t.dataset.act === "export-menu") {
        openExportMenu(el, d, t, name);
        return;
      }
      if (t.classList && t.classList.contains("spot-thumb")) {
        openGallery(t.dataset.i, t.dataset.t);
        return;
      }
      if (t.dataset.editDay) {
        var box = el.querySelector('[data-day="' + t.dataset.editDay + '"]');
        if (box) {
          box.classList.toggle("editing");
          t.textContent = box.classList.contains("editing") ? "完成" : "调整";
        }
        return;
      }
      if (t.dataset.move) {
        var row = t.closest(".item");
        if (!row) { return; }
        if (t.dataset.move === "del") { row.remove(); return; }
        var sib = t.dataset.move === "up" ? row.previousElementSibling : row.nextElementSibling;
        if (sib && sib.classList.contains("has-edit")) {
          if (t.dataset.move === "up") { row.parentNode.insertBefore(row, sib); }
          else { row.parentNode.insertBefore(sib, row); }
        }
      }
    });
  }

  function exportShot(el, name, kind) {
    if (typeof html2canvas === "undefined") {
      alert("导出组件没加载成功（走 CDN，需要联网）。可以先用浏览器自带的打印功能存成 PDF。");
      return;
    }
    // 地图是 canvas 瓦片，截图多半是空白，导出时先藏起来
    var maps = el.querySelectorAll(".map-wrap");
    Array.prototype.forEach.call(maps, function (m) { m.style.display = "none"; });

    html2canvas(el, { backgroundColor: "#121b2d", scale: 2, useCORS: true }).then(function (canvas) {
      Array.prototype.forEach.call(maps, function (m) { m.style.display = ""; });

      if (kind === "png") {
        var a = document.createElement("a");
        a.download = name + ".png";
        a.href = canvas.toDataURL("image/png");
        a.click();
        return;
      }
      if (!window.jspdf) {
        alert("PDF 组件没加载成功（走 CDN，需要联网）。");
        return;
      }
      var pdf = new window.jspdf.jsPDF({ orientation: "p", unit: "pt", format: "a4" });
      var pw = pdf.internal.pageSize.getWidth();
      var ph = pdf.internal.pageSize.getHeight();
      var margin = 18;
      var w = pw - margin * 2;
      var h = canvas.height * w / canvas.width;
      if (h > ph - margin * 2) {   // 长图缩到一页，不硬切
        h = ph - margin * 2;
        w = canvas.width * h / canvas.height;
      }
      pdf.addImage(canvas.toDataURL("image/jpeg", 0.92), "JPEG", (pw - w) / 2, margin, w, h);
      pdf.save(name + ".pdf");
    }).catch(function () {
      Array.prototype.forEach.call(maps, function (m) { m.style.display = ""; });
      alert("导出失败，可以试试浏览器自带的打印功能。");
    });
  }

  /* 行程卡片的导出菜单：图片 / PDF 走本地截图，攻略走服务端渲染成独立文件 */
  function openExportMenu(el, d, anchor, name) {
    var key = "export-" + (d.start_date || "") + d.days_count;
    if (ctxMenu && ctxMenu.dataset.for === key) { closeCtxMenu(); return; }
    closeCtxMenu();

    var menu = document.createElement("div");
    menu.className = "ctx-menu";
    menu.dataset.for = key;
    menu.innerHTML =
      '<button data-exp="png">导出图片 PNG</button>' +
      '<button data-exp="pdf">导出 PDF</button>' +
      '<div class="sep"></div>' +
      '<button data-exp="html">生成完整攻略 · HTML</button>' +
      '<button data-exp="docx">生成完整攻略 · docx</button>';

    menu.addEventListener("click", function (e) {
      var k = e.target.dataset && e.target.dataset.exp;
      if (!k) { return; }
      closeCtxMenu();
      if (k === "png" || k === "pdf") { exportShot(el, name, k); return; }
      genGuide(d, k);
    });

    document.body.appendChild(menu);
    var r = anchor.getBoundingClientRect();
    var w = menu.offsetWidth || 170;
    var h = menu.offsetHeight || 160;
    var left = r.right + 8;
    if (left + w > window.innerWidth - 8) { left = Math.max(8, r.left - w - 8); }
    menu.style.top = Math.round(Math.min(r.top, window.innerHeight - h - 8)) + "px";
    menu.style.left = Math.round(left) + "px";
    ctxMenu = menu;

    function onDocDown(ev) {
      if (menu.contains(ev.target)) { return; }
      if (ev.target.closest && ev.target.closest('[data-act="export-menu"]')) { return; }
      document.removeEventListener("mousedown", onDocDown, true);
      closeCtxMenu();
    }
    setTimeout(function () {
      document.addEventListener("mousedown", onDocDown, true);
    }, 0);
  }

  function genGuide(d, fmt) {
    var tip = document.createElement("div");
    tip.className = "guide-tip";
    tip.textContent = fmt === "docx" ? "正在生成 Word 文档…" : "正在生成攻略页…";
    document.body.appendChild(tip);
    api("/api/guide", { method: "POST", body: { plan: d, format: fmt } }).then(function (r) {
      tip.remove();
      if (fmt === "docx") {
        var a = document.createElement("a");
        a.href = r.url;
        a.download = "";
        document.body.appendChild(a);
        a.click();
        a.remove();
      } else {
        window.open(r.url, "_blank");
      }
    }).catch(function (e) {
      tip.remove();
      alert("生成失败：" + e.message);
    });
  }

  /* 景点图集：点缩略图看大图，可左右翻。照片都来自高德 POI 的 photos 字段。 */
  function openGallery(payload, title) {
    var list = [];
    try { list = JSON.parse(decodeURIComponent(payload || "[]")); } catch (e) { list = []; }
    if (!list.length) { return; }
    var idx = 0;

    var mask = document.createElement("div");
    mask.className = "gallery-mask";
    mask.innerHTML =
      '<div class="gallery"><div class="g-title">' + escapeHtml(title || "") + "</div>" +
      '<img class="g-img" alt="">' +
      '<button class="g-prev" aria-label="上一张">‹</button>' +
      '<button class="g-next" aria-label="下一张">›</button>' +
      '<div class="g-count"></div></div>';

    var img = mask.querySelector(".g-img");
    var count = mask.querySelector(".g-count");
    var prev = mask.querySelector(".g-prev");
    var next = mask.querySelector(".g-next");

    function paint() {
      img.src = list[idx];
      count.textContent = (idx + 1) + " / " + list.length;
      prev.style.visibility = idx > 0 ? "visible" : "hidden";
      next.style.visibility = idx < list.length - 1 ? "visible" : "hidden";
    }
    function close() { mask.remove(); document.removeEventListener("keydown", onKey); }
    function onKey(e) {
      if (e.key === "Escape") { close(); }
      if (e.key === "ArrowLeft" && idx > 0) { idx--; paint(); }
      if (e.key === "ArrowRight" && idx < list.length - 1) { idx++; paint(); }
    }

    prev.addEventListener("click", function (e) { e.stopPropagation(); if (idx > 0) { idx--; paint(); } });
    next.addEventListener("click", function (e) { e.stopPropagation(); if (idx < list.length - 1) { idx++; paint(); } });
    mask.addEventListener("click", function (e) { if (e.target === mask) { close(); } });
    document.addEventListener("keydown", onKey);

    paint();
    document.body.appendChild(mask);
  }

  function hotelCard(d) {
    var el = document.createElement("div");
    el.className = "card";
    var items = d.items || [];
    var shown = items.slice(0, 3);   // 列太多没意义，先给 3 家
    var rows = shown.length ? shown.map(function (h) {
      return '<div class="wrow"><span>' + escapeHtml(h.name) + "</span>" +
        '<span class="r">' + (h.rating ? "★" + h.rating : "无评分") + "</span>" +
        '<span class="m">' + h.price_per_night + " 元/晚（估算）</span>" +
        "<span>" + (h.distance_km != null ? h.distance_km + " km" : "") + "</span></div>";
    }).join("") : '<div class="meta">没有检索到酒店（可能未配置 AMAP_KEY）</div>';
    var more = items.length > shown.length
      ? '<div class="meta" style="margin-top:6px;font-size:12px;opacity:.7">另有 ' +
        (items.length - shown.length) + " 家未显示</div>"
      : "";
    el.innerHTML = '<div class="card-head"><b>酒店 · ' + escapeHtml(d.city) + "</b><span>" +
      escapeHtml(d.level || "") + " · " + escapeHtml(d.price_note || "") + "</span></div>" +
      '<div class="card-body">' + rows + more + "</div>";
    return el;
  }

  function poiCard(d) {
    var el = document.createElement("div");
    el.className = "card";
    var items = d.items || [];
    var rows = items.length ? items.map(function (p) {
      return '<div class="wrow"><span>' + escapeHtml(p.name) + "</span>" +
        '<span class="r">' + (p.rating ? "★" + p.rating : "") + "</span>" +
        '<span class="m">' + escapeHtml(p.address || "") + "</span>" +
        "<span>" + escapeHtml((p.type || "").split(";").pop()) + "</span></div>";
    }).join("") : '<div class="meta">没有找到地点</div>';
    el.innerHTML = '<div class="card-head"><b>地点 · ' + escapeHtml(d.city) + " " + escapeHtml(d.keywords) + "</b><span>" +
      (d.source === "amap" ? "高德实时" : "示例数据") + "</span></div>" +
      '<div class="card-body">' + rows + "</div>";
    return el;
  }

  function fmtDur(min) {
    min = Math.round(min || 0);
    if (min < 60) { return min + " 分钟"; }
    var h = Math.floor(min / 60), m = min % 60;
    return h + " 小时" + (m ? " " + m + " 分" : "");
  }

  /* ---------------- 发送 ---------------- */
  /* ---------------- 附件 ---------------- */
  function renderAttachList() {
    var box = $("attach-list");
    box.innerHTML = "";
    state.attachments.forEach(function (a, i) {
      var chip = document.createElement("span");
      chip.className = "attach-chip" + (a.error ? " bad" : "");
      var nm = document.createElement("span");
      nm.className = "nm";
      nm.textContent = a.name;
      var kd = document.createElement("span");
      kd.className = "kd";
      kd.textContent = a.kind || "";
      var del = document.createElement("button");
      del.textContent = "×";
      del.title = "移除";
      del.addEventListener("click", function () {
        state.attachments.splice(i, 1);
        renderAttachList();
      });
      chip.appendChild(nm);
      chip.appendChild(kd);
      chip.appendChild(del);
      box.appendChild(chip);
    });
  }

  function uploadOne(file) {
    if (file.size > 8 * 1024 * 1024) {
      state.attachments.push({ name: file.name, kind: "超过 8MB", error: true });
      renderAttachList();
      return;
    }
    var slot = { name: file.name, kind: "处理中…" };
    state.attachments.push(slot);
    renderAttachList();

    var reader = new FileReader();
    reader.onload = function () {
      var b64 = String(reader.result).split(",")[1] || "";
      api("/api/upload", { method: "POST", body: { name: file.name, data: b64 } })
        .then(function (r) {
          slot.kind = r.kind;
          slot.text = r.text;
          slot.name = r.name || file.name;
          renderAttachList();
        })
        .catch(function (e) {
          slot.kind = e.message;
          slot.error = true;
          renderAttachList();
        });
    };
    reader.onerror = function () {
      slot.kind = "读取失败";
      slot.error = true;
      renderAttachList();
    };
    reader.readAsDataURL(file);
  }

  $("attach-btn").addEventListener("click", function () { $("file-input").click(); });
  $("file-input").addEventListener("change", function () {
    Array.prototype.forEach.call(this.files, uploadOne);
    this.value = "";
  });

  /* ---------------- 发送（流式） ---------------- */
  function send() {
    var input = $("input");
    var text = input.value.trim();
    if (state.busy) { return; }
    if (state.attachments.some(function (a) { return a.kind === "处理中…"; })) {
      alert("附件还在处理，稍等一下再发");
      return;
    }
    var files = state.attachments.filter(function (a) { return a.text; })
      .map(function (a) { return { name: a.name, kind: a.kind, text: a.text }; });
    if (!text && !files.length) { return; }
    if (!text) { text = "帮我看看这些附件，结合它们给点建议"; }
    state.busy = true;
    $("send").disabled = true;
    input.value = "";
    input.style.height = "auto";

    if (!document.querySelector(".msg")) { $("messages").innerHTML = ""; }
    renderMessage("user", text);

    var box = document.createElement("div");
    box.className = "msg";
    box.innerHTML = '<div class="avatar">智</div><div class="bubble">' +
      '<div class="stage"><span class="typing"><i></i><i></i><i></i></span>' +
      '<span class="stage-text">正在理解你的需求…</span></div>' +
      '<div class="stream"></div></div>';
    $("messages").appendChild(box);
    $("messages").scrollTop = $("messages").scrollHeight;

    var stageEl = box.querySelector(".stage-text");
    var streamEl = box.querySelector(".stream");
    var buf = "", tools = 0;
    state.stageEl = stageEl;

    function append(v) {
      buf += v;
      streamEl.innerHTML = md(buf);
      $("messages").scrollTop = $("messages").scrollHeight;
    }

    function handle(ev) {
      if (ev.t === "hello") { state.sessionId = ev.session_id; return; }
      if (ev.t === "stage") { stageEl.textContent = ev.v; return; }
      if (ev.t === "delta") { append(ev.v); return; }
      if (ev.t === "tool") {
        if (ev.phase === "start") {
          tools += 1;
          stageEl.textContent = "正在调用 " + ev.name + "（第 " + tools + " 个工具）…";
        } else {
          stageEl.textContent = ev.ok
            ? ev.name + " 完成（" + ev.ms + "ms），继续整理…"
            : ev.name + " 没成功，换个方式再试…";
        }
        return;
      }
      if (ev.t === "error") { stageEl.textContent = "出错了：" + ev.message; return; }
      if (ev.t === "done") {
        var res = ev.result || {};
        box.remove();
        state.sessionId = ev.session_id || state.sessionId;
        state.attachments = [];
        renderAttachList();
        renderMessage("assistant", res.reply || "", res.steps, res.cards);
        if ($("chat-title").textContent === "新对话") { $("chat-title").textContent = text.slice(0, 18); }
        loadSessions();
        // 标题由模型后台概括，几秒后才写库，回来再刷一次
        if (res.title_pending) {
          setTimeout(function () {
            loadSessions().then(function () {
              var cur = (state.sessions || []).filter(function (s) { return s.id === state.sessionId; })[0];
              if (cur && cur.title) { $("chat-title").textContent = cur.title; }
            });
          }, 6000);
        }
      }
    }

    fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ session_id: state.sessionId, message: text, attachments: files })
    }).then(function (resp) {
      if (resp.status === 401) { showAuth(); throw new Error("未登录"); }
      if (!resp.ok || !resp.body) { throw new Error("请求失败 " + resp.status); }
      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var carry = "";
      function pump() {
        return reader.read().then(function (r) {
          if (r.done) { return; }
          carry += decoder.decode(r.value, { stream: true });
          var parts = carry.split("\n\n");
          carry = parts.pop();
          parts.forEach(function (chunk) {
            var line = chunk.replace(/^data: ?/, "").trim();
            if (!line) { return; }
            try { handle(JSON.parse(line)); } catch (e) { /* 坏行跳过 */ }
          });
          return pump();
        });
      }
      return pump();
    }).catch(function (err) {
      if (box.parentNode) { box.remove(); }
      renderMessage("assistant", "出错了：" + err.message + "\n（如果是网络问题，检查一下网络或稍后再试）");
    }).then(function () {
      state.busy = false;
      state.stageEl = null;
      $("send").disabled = false;
      input.focus();
    });
  }

  /* ---------------- 双击 ESC 打断 ---------------- */
  var lastEsc = 0;
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") { return; }
    var now = Date.now();
    if (state.busy && now - lastEsc < 800) {
      lastEsc = 0;
      if (state.stageEl) { state.stageEl.textContent = "正在打断…"; }
      api("/api/chat/abort", { method: "POST", body: { session_id: state.sessionId } })
        .catch(function () { /* 没开跑就算了 */ });
    } else {
      lastEsc = now;
      if (state.busy && state.stageEl) {
        state.stageEl.textContent = "再按一次 ESC 可以打断这一轮";
      }
    }
  });

  /* ---------------- 设置页 ---------------- */
  function switchPane(name) {
    Array.prototype.forEach.call(document.querySelectorAll(".mtab"), function (b) {
      b.classList.toggle("active", b.dataset.pane === name);
    });
    Array.prototype.forEach.call(document.querySelectorAll(".modal-body .pane"), function (p) {
      p.classList.toggle("active", p.id === "pane-" + name);
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll(".mtab"), function (b) {
    b.addEventListener("click", function () { switchPane(b.dataset.pane); });
  });

  function openSettings() {
    $("settings").classList.remove("hidden");
    switchPane("api");
    $("account-name").textContent = state.user ? state.user.username : "";
    api("/api/settings").then(function (r) {
      $("set-model-hint").textContent = r.model_set ? "已配置 " + r.model_key : "未配置";
      $("set-amap-hint").textContent = r.amap_set ? "已配置 " + r.amap_key : "未配置（POI 走示例数据）";
      $("set-pi-model").value = r.pi_model || "";
      $("set-llm-model").value = r.llm_model || "";
      $("set-model-key").value = "";
      $("set-amap-key").value = "";
    }).catch(function (e) { alert(e.message); });
    loadMemories();
  }

  function closeSettings() { $("settings").classList.add("hidden"); }

  function saveSettings() {
    var body = {
      model_key: $("set-model-key").value.trim(),
      amap_key: $("set-amap-key").value.trim(),
      pi_model: $("set-pi-model").value.trim(),
      llm_model: $("set-llm-model").value.trim()
    };
    if (!body.model_key && !body.amap_key && !body.pi_model && !body.llm_model) {
      $("set-model-hint").textContent = "没有填任何要改的内容";
      return;
    }
    $("settings-save").disabled = true;
    api("/api/settings", { method: "POST", body: body }).then(function () {
      $("settings-save").disabled = false;
      $("settings-save").textContent = "已保存";
      setTimeout(function () { $("settings-save").textContent = "保存"; }, 1500);
      // v2 删掉了顶部状态条，loadStatus 已不存在；这里做存在性检查，
      // 否则保存成功后会抛 ReferenceError，前端误报「保存失败」。
      if (typeof loadStatus === "function") { loadStatus(); }
      openSettings();
    }).catch(function (e) {
      $("settings-save").disabled = false;
      alert("保存失败：" + e.message);
    });
  }

  function loadMemories() {
    api("/api/memories").then(function (r) {
      var list = r.memories || [];
      $("mem-count").textContent = list.length ? "共 " + list.length + " 条" : "";
      var box = $("mem-list");
      box.innerHTML = "";
      if (!list.length) {
        box.innerHTML = '<div class="hint">还没有记忆。试着说「我常住广州，预算一般 1500」，或者在这里手动加。</div>';
        return;
      }
      list.forEach(function (m) {
        var row = document.createElement("div");
        row.className = "mem-row";
        row.innerHTML = '<span class="k">' + escapeHtml(m.key) + "</span>" +
          "<span>" + escapeHtml(m.value) + "</span>";
        var btn = document.createElement("button");
        btn.className = "ghost";
        btn.textContent = "删除";
        btn.addEventListener("click", function () {
          api("/api/memories/delete", { method: "POST", body: { key: m.key } })
            .then(loadMemories).catch(function () {});
        });
        row.appendChild(btn);
        box.appendChild(row);
      });
    }).catch(function () { /* 未登录等，忽略 */ });
  }

  $("settings-open").addEventListener("click", openSettings);
  $("settings-close").addEventListener("click", closeSettings);
  $("settings-save").addEventListener("click", saveSettings);
  $("settings").addEventListener("click", function (e) {
    if (e.target === $("settings")) { closeSettings(); }
  });
  $("mem-add").addEventListener("click", function () {
    var k = $("mem-key").value.trim(), v = $("mem-value").value.trim();
    if (!k || !v) { return; }
    api("/api/memories", { method: "POST", body: { key: k, value: v } }).then(function () {
      $("mem-key").value = "";
      $("mem-value").value = "";
      loadMemories();
    }).catch(function (e) { alert(e.message); });
  });
  $("mem-clear").addEventListener("click", function () {
    if (!confirm("清空全部长期记忆？这个操作不能撤销。")) { return; }
    api("/api/memories/clear", { method: "POST" }).then(loadMemories).catch(function () {});
  });

  $("send").addEventListener("click", send);
  $("input").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });
  $("input").addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 140) + "px";
  });

  /* 自动登录（有 Cookie 就直接进） */
  boot().catch(function () { showAuth(); });
})();
