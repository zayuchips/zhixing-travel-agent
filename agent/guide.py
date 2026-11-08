# -*- coding: utf-8 -*-
"""攻略产出：把行程数据渲染成可脱离本应用打开的 HTML，以及可编辑的 docx。

两个都不引第三方库：
    HTML —— 字符串模板，内联样式，双击就能看
    docx —— 手写 OOXML（zip 里塞 [Content_Types].xml / _rels/.rels / word/document.xml）

产物落在 static/guides/ 下，前端直接给下载链接。
"""

import json
import math
import os
import re
import time
import zipfile

import config

GUIDE_DIR = os.path.join(config.STATIC_DIR, "guides")


def _ensure_dir():
    os.makedirs(GUIDE_DIR, exist_ok=True)
    return GUIDE_DIR


def _e(s):
    """转义。行程数据来自高德和模型，不能直接拼进 HTML。"""
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _slug(d):
    """文件名只用 ASCII：URL 里带中文会让不少 HTTP 客户端直接在编码处炸掉。
    中文目的地进不去 slug 也没关系，标题在 HTML/docx 里是完整的。"""
    base = re.sub(r"[^A-Za-z0-9_-]", "", d.get("destination") or "") or "trip"
    return "%s-%dday-%d" % (base.lower()[:24], d.get("days_count") or 1,
                            int(time.time()) % 100000)


def _money(v):
    try:
        return "{:,}".format(int(v or 0))
    except (TypeError, ValueError):
        return "0"


# ---------------- HTML ----------------

_CSS = """
:root{--brick:#B4472F;--gold:#C89B4A;--teal:#2E6E8E;--ink:#2B2724;--sub:#6E635A;
--paper:#FAF6EF;--card:#fff;--line:#E9E0D3;--soft:#F5EDE2}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);line-height:1.75;
font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
-webkit-font-smoothing:antialiased}
.wrap{max-width:1000px;margin:0 auto;padding:0 20px 70px}
header{background:linear-gradient(135deg,#A63D28 0%,#C25C3C 55%,#D98A54 100%);
color:#fff;padding:44px 0 38px;margin-bottom:26px}
header .wrap{padding-bottom:0}
.eyebrow{font-size:13px;letter-spacing:3px;opacity:.85;margin:0 0 10px}
h1{font-size:33px;line-height:1.32;margin:0 0 12px;letter-spacing:.5px}
.lede{font-size:15px;opacity:.94;margin:0 0 20px;max-width:720px}
.badges{display:flex;flex-wrap:wrap;gap:9px}
.badge{background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.34);
padding:5px 13px;border-radius:99px;font-size:13px}
h2{font-size:22px;margin:34px 0 14px;padding-left:12px;border-left:5px solid var(--brick);line-height:1.35}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:0 0 6px}
.stat{background:#fff;border:1px solid var(--line);border-radius:12px;padding:15px 12px;text-align:center}
.stat b{display:block;font-size:23px;color:var(--brick);line-height:1.25}
.stat span{font-size:12px;color:var(--sub)}
.day{background:#fff;border:1px solid var(--line);border-radius:14px;padding:20px 22px;
margin-bottom:16px;box-shadow:0 2px 14px rgba(86,60,38,.05)}
.day-hd{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin:0 0 14px}
.day-hd .d{font-size:13px;background:var(--brick);color:#fff;padding:3px 12px;border-radius:6px;letter-spacing:1px}
.day-hd .d.b{background:var(--teal)}
.day-hd h3{margin:0;font-size:19px}
.day-hd .meta{font-size:13px;color:var(--sub);margin-left:auto}
table{width:100%;border-collapse:collapse;font-size:14px;background:#fff}
th,td{border-bottom:1px solid var(--line);padding:10px;text-align:left;vertical-align:top}
th{background:var(--soft);color:#5A4A3C;font-weight:600;white-space:nowrap}
td.time{white-space:nowrap;color:var(--brick);font-weight:600;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
.tbl{border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-bottom:12px}
.tag{display:inline-block;font-size:11px;padding:1px 8px;border-radius:99px;
background:var(--soft);color:#7A5B45;margin-right:4px;white-space:nowrap}
.note{background:#FFF7E9;border-left:4px solid var(--gold);padding:12px 16px;
border-radius:0 8px 8px 0;font-size:14px;margin:12px 0}
.warn{background:#FDF0EC;border-left:4px solid var(--brick);padding:12px 16px;
border-radius:0 8px 8px 0;font-size:14px;margin:12px 0}
.meals{font-size:13px;color:var(--sub);margin-top:10px}
.meals b{color:var(--ink)}
footer{margin-top:34px;padding-top:16px;border-top:1px solid var(--line);
font-size:12px;color:var(--sub)}
@media(max-width:720px){.stats{grid-template-columns:repeat(2,1fr)}h1{font-size:26px}}

/* ---------- 点位地图 ---------- */
.map-wrap{background:#fff;border:1px solid var(--line);border-radius:14px;padding:14px;
margin-bottom:18px;box-shadow:0 2px 14px rgba(86,60,38,.05)}
.map-toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px;
font-size:13px;color:var(--sub)}
.map-toolbar .chip{background:var(--soft);border:1px solid var(--line);color:var(--sub);
border-radius:99px;padding:4px 12px;font-size:12.5px;cursor:pointer;font-family:inherit}
.map-toolbar .chip.on{background:var(--brick);border-color:var(--brick);color:#fff}
.map-toolbar .chip.blue.on{background:var(--teal);border-color:var(--teal)}
#map{height:350px;border-radius:10px;overflow:hidden;background:#EDE7DC;z-index:0}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--sub);margin-top:10px}
.legend i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}
.pin{width:24px;height:24px;border-radius:50%;color:#fff;font-size:12px;font-weight:600;
line-height:21px;text-align:center;border:2px solid #fff;box-shadow:0 1px 5px rgba(0,0,0,.35)}
.spot-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(214px,1fr));gap:10px;margin-top:12px}
.sc{background:var(--soft);border:1px solid var(--line);border-radius:10px;padding:10px 12px;
cursor:pointer;transition:.15s}
.sc:hover{border-color:var(--brick);transform:translateY(-1px)}
.sc .n{font-weight:600;font-size:14px;margin-bottom:3px;line-height:1.4}
.sc .m{font-size:12px;color:var(--sub);line-height:1.65}

/* ---------- 天气 ---------- */
.wx{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-bottom:14px}
.wx .d{background:#fff;border:1px solid var(--line);border-radius:11px;padding:11px 13px}
.wx .d.rain{background:#FDF5F2;border-color:#E8C9C1}
.wx .dt{font-size:12px;color:var(--sub);margin-bottom:3px}
.wx .wv{font-size:15px;font-weight:600;margin-bottom:3px}
.wx .tp{font-size:13px;color:var(--brick);font-variant-numeric:tabular-nums}
.wx .ad{font-size:12px;color:var(--sub);margin-top:5px;line-height:1.55}

/* ---------- 前置须知 ---------- */
prep{display:block}
.prep li{margin:6px 0;font-size:14px}
.prep b{color:var(--brick)}

/* ---------- 交通 ---------- */
.transit{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 16px;
margin-bottom:14px;font-size:14px}
.transit .k{color:var(--sub);font-size:13px}
.transit b{font-size:16px}
tr.total td{background:var(--soft);font-weight:600}
"""


def _dist_km(a, b):
    """两点直线距离（km）。用来标「上一站到这里多远」，不假装是路网距离。"""
    try:
        lng1, lat1, lng2, lat2 = float(a[0]), float(a[1]), float(b[0]), float(b[1])
    except (TypeError, ValueError, IndexError):
        return None
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(h)), 1)


def _day_colors():
    return ["#B4472F", "#2E6E8E", "#6B8E4E", "#8B5A9E", "#B8860B", "#3C7A6B", "#A0522D"]


def _cat_label(raw):
    """高德的类别是「风景名胜;风景名胜;寺庙道观」这种，取最有信息量的最后一段。"""
    parts = [p for p in (raw or "").split(";") if p.strip()]
    if not parts:
        return ""
    tail = parts[-1].strip()
    # 「风景名胜」重复三次时没有信息量，退回第一段
    if tail in ("风景名胜", "风景名胜相关", "旅游景点") and len(set(parts)) == 1:
        return ""
    return tail


_MAP_JS = """
var SPOTS = %(spots)s;
var COLORS = %(colors)s;
(function(){
  var el = document.getElementById('map');
  if (!el || !window.L) { if (el) el.innerHTML =
    '<div style="padding:24px;text-align:center;color:#6E635A;font-size:13px">'
    + '地图需要联网加载 Leaflet，当前没加载成功。下方点位清单仍然可用。</div>'; return; }
  if (!SPOTS.length) { el.innerHTML =
    '<div style="padding:24px;text-align:center;color:#6E635A;font-size:13px">'
    + '这版行程里的点位没有经纬度，画不出地图。</div>'; return; }

  var map = L.map('map', {scrollWheelZoom: false});
  L.tileLayer('https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
    {subdomains: '1234', maxZoom: 18, attribution: '&copy; 高德地图'}).addTo(map);

  var groups = {}, all = [], lines = [];
  SPOTS.forEach(function(s, i){
    var color = COLORS[(s.d - 1) %% COLORS.length];
    var icon = L.divIcon({className: '', iconSize: [24, 24], iconAnchor: [12, 12],
      html: '<div class="pin" style="background:' + color + '">' + s.i + '</div>'});
    var m = L.marker([s.lat, s.lng], {icon: icon, title: s.n});
    m.bindPopup('<div style="font-size:13px;line-height:1.7;min-width:150px">'
      + '<b>' + s.n + '</b><br>' + (s.t ? s.t + ' · ' : '') + 'D' + s.d
      + (s.a ? '<br>' + s.a : '')
      + (s.c ? '<br><span style="color:#6E635A">' + s.c + '</span>' : '')
      + (s.f ? '<br>门票 ' + s.f : '') + '</div>');
    if (!groups[s.d]) groups[s.d] = L.layerGroup().addTo(map);
    m.addTo(groups[s.d]);
    m.__day = s.d;
    all.push(m);
  });

  // 同一天按顺序连线（虚线，表示先后关系不是实际路径）
  Object.keys(groups).forEach(function(day){
    var pts = SPOTS.filter(function(s){ return String(s.d) === day; });
    if (pts.length > 1) {
      L.polyline(pts.map(function(s){ return [s.lat, s.lng]; }),
        {color: COLORS[(day - 1) %% COLORS.length], weight: 2, opacity: .55, dashArray: '6 6'}
      ).addTo(map);
    }
  });

  map.fitBounds(L.featureGroup(all).getBounds(), {padding: [40, 40]});

  // 筛选
  var chips = document.querySelectorAll('.map-toolbar .chip');
  Array.prototype.forEach.call(chips, function(b){
    b.addEventListener('click', function(){
      var f = b.getAttribute('data-filter');
      Array.prototype.forEach.call(chips, function(x){ x.classList.remove('on'); });
      b.classList.add('on');
      all.forEach(function(m){
        var show = (f === 'all') || (String(m.__day) === f);
        var g = groups[m.__day];
        if (!g) return;
        if (show) { if (!map.hasLayer(g)) map.addLayer(g); }
        else if (map.hasLayer(g)) map.removeLayer(g);
      });
      var vis = all.filter(function(m){ return f === 'all' || String(m.__day) === f; });
      if (vis.length) map.fitBounds(L.featureGroup(vis).getBounds(), {padding: [40, 40]});
    });
  });

  // 点下方清单 → 地图定位并弹窗
  window.__focusSpot = function(idx){
    var m = all[idx];
    if (!m) return;
    map.setView(m.getLatLng(), Math.max(map.getZoom(), 14));
    m.openPopup();
    el.scrollIntoView({behavior: 'smooth', block: 'center'});
  };
})();
"""


def render_html(plan, title=None, highlight=None, extras=None):
    """把行程渲染成一份能单独打开的攻略。

    extras 是同一轮里其它工具的结构化结果，用来把天气、酒店、交通一起编进文件：
        {"weather": {...}, "hotels": [{...}], "pois": [{...}], "route": {...}}

    设计原则：宁缺毋编。拿不到的数据不写，或者明确写「未取到」，
    绝不为了版面好看编造景点描述、房价或气温。
    """
    d = plan or {}
    extras = extras or {}
    days = d.get("days") or []
    s = d.get("summary") or {}
    dest = d.get("destination") or "目的地"
    n_days = d.get("days_count") or len(days)

    # ---- 把带坐标的点位收成一份，给地图和清单用 ----
    spots, seq = [], 0
    for day in days:
        for a in day.get("attractions") or []:
            if a.get("lng") is None or a.get("lat") is None:
                continue
            seq += 1
            spots.append({
                "d": day.get("index") or 1,
                "i": seq,
                "n": a.get("name") or "",
                "t": a.get("time") or "",
                "a": a.get("address") or "",
                "c": _cat_label(a.get("category")),
                "f": ("¥%s" % _money(a.get("ticket_price"))) if a.get("ticket_price") else "免费",
                "lat": a.get("lat"), "lng": a.get("lng"),
            })

    total_spots = sum(len(x.get("attractions") or []) for x in days)
    head_title = title or "%s %d 天行程" % (dest, n_days)

    # ---- 标题下的一句话：能说的都是算出来的 ----
    walk_km = None
    for day in days:
        pts = [(a.get("lng"), a.get("lat")) for a in day.get("attractions") or []
               if a.get("lng") is not None]
        for prev, cur in zip(pts, pts[1:]):
            km = _dist_km(prev, cur)
            if km:
                walk_km = (walk_km or 0) + km
    lede = (highlight or "").strip()
    if not lede:
        bits = ["%s 天" % n_days, "%d 个点位" % total_spots,
                "预算约 ¥%s" % _money(s.get("total_cost"))]
        if walk_km:
            bits.append("点位间直线合计约 %.0f 公里" % walk_km)
        lede = "，".join(bits) + "。时间、三餐、住宿都排好了，照着走。"

    badges = ['<span class="badge">%s</span>' % _e(dest),
              '<span class="badge">%d 天</span>' % n_days,
              '<span class="badge">预算 ¥%s</span>' % _money(s.get("budget") or s.get("total_cost")),
              '<span class="badge">出发 %s</span>' % _e(d.get("start_date") or "")]
    if d.get("interests"):
        badges.append('<span class="badge">%s</span>' % _e("、".join(d["interests"])))

    stats = ('<div class="stats">'
             '<div class="stat"><b>%d</b><span>天数</span></div>'
             '<div class="stat"><b>%d</b><span>点位</span></div>'
             '<div class="stat"><b>¥%s</b><span>预估总花费</span></div>'
             '<div class="stat"><b>¥%s</b><span>人均每日</span></div></div>') % (
        n_days, total_spots, _money(s.get("total_cost")), _money(s.get("per_day")))

    # ---- 出发前须知：每条都能在数据里找到依据 ----
    prep = []
    wx_days = extras.get("weather", {}).get("days") or days
    rainy = [x for x in wx_days if (x.get("rain_prob") or x.get("precip_prob") or 0) >= 60]
    temps = [(x.get("tmin"), x.get("tmax")) for x in wx_days
             if x.get("tmax") is not None and x.get("tmin") is not None]
    if temps:
        lo = min(t[0] for t in temps)
        hi = max(t[1] for t in temps)
        gap = max(t[1] - t[0] for t in temps)
        line = "这几天 <b>%s~%s℃</b>" % (round(lo), round(hi))
        if gap >= 8:
            line += "，昼夜温差最大 <b>%d℃</b>，带一件薄外套" % round(gap)
        if rainy:
            line += "；<b>%s</b> 降水概率 ≥60%%，户外点位建议挪到别的时段" % "、".join(
                _e(x.get("date") or "") for x in rainy[:2])
        prep.append(line)
    if s.get("ticket_cost"):
        prep.append("门票合计 <b>¥%s</b>，热门点位多数需要实名预约，出发前在官方公众号确认" %
                    _money(s.get("ticket_cost")))
    else:
        prep.append("这版点位门票合计 <b>¥0</b>，但热门寺庙多在 <b>17:00 前后停止入内</b>，"
                    "古建尽量排在白天")
    hotel0 = d.get("hotel") or next((x.get("hotel") for x in days if x.get("hotel")), None)
    if hotel0:
        prep.append("住宿建议 <b>%s</b>（%s档，约 ¥%s/晚，估算值），距首个点位 %s 公里" % (
            _e(hotel0.get("name")), _e(hotel0.get("level") or ""),
            _money(hotel0.get("price_night") or hotel0.get("price_per_night")),
            hotel0.get("distance_km") if hotel0.get("distance_km") is not None else "—"))
    elif d.get("days_count") and d.get("days_count") > 1:
        prep.append("住宿按 <b>¥%s/晚</b> 估算，具体酒店未取到——"
                    "高德只返回周边旅店，房价需在订房 App 确认" % _money(
                        round((s.get("hotel_cost") or 0) / max(1, n_days - 1))))
    if d.get("transportation") or True:
        prep.append("市内交通按 <b>%s</b> 估算，共 ¥%s；不含城际大交通" % (
            _e(next((x.get("transportation") for x in days if x.get("transportation")), "公交")),
            _money(s.get("transport_cost"))))
    prep_html = ('<h2>出发前，先记住这几件事</h2><ul class="prep">%s</ul>'
                 % "".join("<li>%s</li>" % p for p in prep)) if prep else ""

    # ---- 地图 ----
    if spots:
        chips = '<button class="chip on" data-filter="all">全部点位</button>'
        legend = []
        for i in range(n_days):
            color = _day_colors()[i % len(_day_colors())]
            chips += ('<button class="chip%s" data-filter="%d">Day %d</button>'
                      % (" blue" if i % 2 else "", i + 1, i + 1))
            legend.append('<span><i style="background:%s"></i>Day %d</span>' % (color, i + 1))
        grid = "".join(
            '<div class="sc" onclick="window.__focusSpot(%d)">'
            '<div class="n">%s</div>'
            '<div class="m">D%d · %s%s%s</div></div>' % (
                idx, _e(x["n"]), x["d"], _e(x["t"]),
                (" · " + _e(x["c"])) if x["c"] else "",
                (" · " + _e(x["a"])) if x["a"] else "")
            for idx, x in enumerate(spots))
        map_block = (
            '<h2>点位地图：这两天走哪儿，一眼看清</h2>'
            '<div class="map-wrap"><div class="map-toolbar">'
            '<span style="font-size:13px">筛选：</span>%s'
            '<span style="margin-left:auto;font-size:12px">点标记或下方清单可看详情</span>'
            '</div><div id="map"></div>'
            '<div class="legend">%s<span>坐标系 GCJ-02 · 底图：高德地图</span></div>'
            '<div class="spot-grid">%s</div></div>'
            '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>'
            '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">'
            '<script>%s</script>') % (
                chips, "".join(legend), grid,
                _MAP_JS % {"spots": json.dumps(spots, ensure_ascii=False),
                           "colors": json.dumps(_day_colors())})
    else:
        map_block = ('<h2>点位地图</h2><div class="note">'
                     '这版行程的点位没有经纬度，画不出地图（数据源未返回坐标）。</div>')

    # ---- 逐日 ----
    body = []
    for day in days:
        day_spots = day.get("attractions") or []
        rainy_day = (day.get("rain_prob") or 0) >= 60
        w = ""
        if day.get("tmax") is not None:
            w = "%s %s~%s℃（降水 %s%%）" % (_e(day.get("weather")), day.get("tmin"),
                                            day.get("tmax"), day.get("rain_prob"))
        # 当天的移动量与门票，用来写 header 右侧的 meta
        pts = [(a.get("lng"), a.get("lat")) for a in day_spots if a.get("lng") is not None]
        seg_km = sum(filter(None, (_dist_km(a, b) for a, b in zip(pts, pts[1:])))) if len(pts) > 1 else 0
        fee = sum(int(a.get("ticket_price") or 0) for a in day_spots)
        meta = "%d 个点位" % len(day_spots)
        if seg_km:
            meta += " · 直线约 %.1f 公里" % seg_km
        meta += " · 门票 ¥%s" % _money(fee)

        rows = []
        prev = None
        for a in day_spots:
            cur = (a.get("lng"), a.get("lat"))
            gap = ""
            if prev and cur[0] is not None and prev[0] is not None:
                km = _dist_km(prev, cur)
                gap = ("%.1f km" % km) if km is not None else ""
            prev = cur
            cat = _cat_label(a.get("category"))
            tags = ''.join('<span class="tag">%s</span>' % _e(t) for t in (
                "室内" if a.get("indoor") else "户外",
                cat or "点位",
                ("★" + str(a["rating"])) if a.get("rating") else ""))
            rows.append(
                "<tr><td class='time'>%s</td>"
                "<td><b>%s</b><br>%s%s</td>"
                "<td>%s</td><td>%s</td><td>%s</td></tr>" % (
                    _e(a.get("time")), _e(a.get("name")), tags,
                    ("<div style='color:#6E635A;font-size:12.5px;margin-top:3px'>%s</div>"
                     % _e(a["address"])) if a.get("address") else "",
                    ("¥%s" % _money(a.get("ticket_price"))) if a.get("ticket_price") else "免费",
                    _e("约 %s 小时" % (round((a.get("visit_duration") or 120) / 60, 1))),
                    _e(gap) or "—"))
        table = ('<div class="tbl"><table><thead><tr>'
                 '<th>时间</th><th>点位 / 看点</th><th>门票</th><th>建议停留</th><th>距上一站</th>'
                 '</tr></thead><tbody>%s</tbody></table></div>') % "".join(rows) if rows else ""

        # 当天路线一行：A → B → C，让人不用看地图也知道顺序
        route_line = ""
        if len(day_spots) > 1:
            route_line = ('<div class="meals"><b>当天顺序</b>：%s</div>'
                          % " → ".join(_e(a.get("name")) for a in day_spots))
        elif day_spots:
            route_line = ('<div class="meals"><b>当天安排</b>：%s</div>'
                          % _e(day_spots[0].get("name")))

        meals = ""
        if day.get("meals"):
            meals = '<div class="meals"><b>三餐</b>：' + "　".join(
                "%s %s（¥%s）" % (_e(m.get("slot")), _e(m.get("name")), _money(m.get("cost")))
                for m in day["meals"]) + "</div>"

        hotel = ""
        if day.get("hotel"):
            h = day["hotel"]
            hotel = '<div class="meals"><b>住宿</b>：%s（%s档 · 约 ¥%s/晚，估算%s）</div>' % (
                _e(h.get("name")), _e(h.get("level") or ""), _money(h.get("price_per_night")),
                "，距首个点位 %s 公里" % h["distance_km"] if h.get("distance_km") is not None else "")
        elif day.get("accommodation"):
            hotel = '<div class="meals"><b>住宿</b>：%s</div>' % _e(day["accommodation"])

        note = ""
        if day.get("note"):
            note = '<div class="%s">%s</div>' % ("warn" if rainy_day else "note", _e(day["note"]))
        desc = ""
        if day.get("description"):
            desc = '<p style="font-size:14px;color:#6E635A;margin:0 0 12px">%s</p>' % _e(day["description"])

        body.append(
            '<div class="day"><div class="day-hd">'
            '<span class="d%s">DAY %s</span><h3>%s %s</h3>'
            '<span class="meta">%s</span></div>'
            '%s%s%s%s%s%s%s</div>' % (
                " b" if rainy_day else "", day.get("index"),
                _e(day.get("date")), _e(day.get("weekday") or ""), _e(meta),
                desc,
                ('<div class="note" style="background:#F7F1E6"><b>主题</b>：%s</div>'
                 % _e(day["theme"])) if day.get("theme") else "",
                table, route_line, meals, hotel, note))
    days_block = '<h2>逐日安排</h2>%s' % "".join(body)

    # ---- 天气 ----
    wx_html = ""
    if wx_days:
        cards = []
        for x in wx_days:
            prob = x.get("rain_prob") if x.get("rain_prob") is not None else x.get("precip_prob")
            advice = x.get("advice") or ""
            cards.append(
                '<div class="d%s"><div class="dt">%s %s</div><div class="wv">%s</div>'
                '<div class="tp">%s~%s℃</div>'
                '<div class="ad">降水 %s%%%s</div></div>' % (
                    " rain" if (prob or 0) >= 60 else "",
                    _e(x.get("date") or ""), _e(x.get("weekday") or ""),
                    _e(x.get("weather") or "—"),
                    x.get("tmin") if x.get("tmin") is not None else "—",
                    x.get("tmax") if x.get("tmax") is not None else "—",
                    prob if prob is not None else "—",
                    ("<br>" + _e(advice)) if advice else ""))
        wx_html = ('<h2>天气：这几天怎么穿、哪天该躲雨</h2><div class="wx">%s</div>'
                   '<div class="note">预报来自 open-meteo，越往后越不准，出发前一天再看一眼。</div>'
                   % "".join(cards))

    # ---- 酒店 ----
    hotel_html = ""
    hotels = extras.get("hotels") or []
    if hotels:
        hrows = []
        seen = set()
        for h in hotels:
            nm = h.get("name") or ""
            if not nm or nm in seen:
                continue
            seen.add(nm)
            hrows.append(
                "<tr><td><b>%s</b></td><td>%s</td><td>%s</td><td>¥%s/晚</td><td>%s</td></tr>" % (
                    _e(nm), _e(h.get("address") or "—"),
                    ("★" + str(h["rating"])) if h.get("rating") else "—",
                    _money(h.get("price_per_night")),
                    ("%.1f km" % h["distance_km"]) if h.get("distance_km") is not None else "—"))
            if len(hrows) >= 6:
                break
        hotel_html = ('<h2>住宿：这一片住着最省事</h2>'
                      '<div class="tbl"><table><thead><tr><th>酒店</th><th>地址</th>'
                      '<th>评分</th><th>价格</th><th>距首个点位</th></tr></thead><tbody>%s</tbody>'
                      '</table></div>'
                      '<div class="note">价格是按档次估算的，不是实时房价——'
                      '高德不返回房价，下单前以订房 App 为准。</div>' % "".join(hrows))

    # ---- 预算 ----
    total = s.get("total_cost") or 0
    def pct(v):
        try:
            return round((v or 0) * 100.0 / total) if total else 0
        except ZeroDivisionError:
            return 0
    budget_rows = ""
    for label, key in (("门票", "ticket_cost"), ("住宿", "hotel_cost"),
                       ("餐饮", "food_cost"), ("市内交通", "transport_cost")):
        v = s.get(key) or 0
        budget_rows += ("<tr><td>%s</td><td>¥%s</td><td>%d%%</td></tr>"
                        % (label, _money(v), pct(v)))
    budget_rows += ("<tr class='total'><td>合计</td><td>¥%s</td><td>100%%</td></tr>"
                    % _money(total))
    budget = ('<h2>预算表：¥%s 怎么花</h2><div class="tbl"><table><thead><tr>'
              '<th>项目</th><th>金额</th><th>占比</th></tr></thead><tbody>%s</tbody></table></div>'
              % (_money(s.get("budget") or total), budget_rows))

    # ---- 交通 ----
    transit = ""
    route = extras.get("route") or {}
    if route.get("distance_km"):
        transit = ('<div class="transit"><div><span class="k">%s → %s</span>　'
                   '<b>%s 公里</b>　约 %s</div>'
                   '<div class="k" style="margin-top:6px">%s</div></div>' % (
                       _e(route.get("origin") or ""), _e(route.get("destination") or ""),
                       _money(route.get("distance_km")),
                       _e(route.get("duration_text") or "%s 分钟" % route.get("duration_min") or "—"),
                       _e(route.get("advice") or "")))
    transit_modes = sorted({x.get("transportation") for x in days if x.get("transportation")})
    if transit_modes or s.get("transport_cost"):
        transit += ('<div class="transit"><span class="k">市内交通</span>　'
                    '<b>%s</b>　共 ¥%s<div class="k" style="margin-top:6px">'
                    '按天数估算，不含打车和城际大交通。</div></div>'
                    % (_e("、".join(transit_modes) or "公交"), _money(s.get("transport_cost"))))
    transit_html = ('<h2>交通</h2>%s' % transit) if transit else ""

    # ---- 贴士 ----
    caveats = "".join('<div class="note">%s</div>' % _e(n) for n in (s.get("notes") or []))
    tips = "".join('<div class="note">%s</div>' % _e(t) for t in (d.get("tips") or []))
    tips_html = ('<h2>几个别踩的坑</h2>%s' % (tips + caveats)) if (tips or caveats) else ""

    src = {"amap": "高德地图实时检索", "local": "本地示例数据", "template": "通用框架",
           "empty": "未检索到点位"}.get(d.get("poi_source"), d.get("poi_source") or "")

    html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title><style>%(css)s</style></head><body>
<header><div class="wrap"><p class="eyebrow">智行 · 出行攻略</p>
<h1>%(title)s</h1><p class="lede">%(lede)s</p>
<div class="badges">%(badges)s</div></div></header>
<div class="wrap">%(stats)s
%(prep)s
%(map)s
%(days)s
%(weather)s
%(hotel)s
%(budget)s
%(transit)s
%(tips)s
<footer>点位来源：%(src)s　｜　天气：%(weather_src)s　｜　生成时间：%(ts)s<br>
门票与房价为估算口径，出行前请以景区/酒店公示为准；点位间距离为直线距离，非实际路程。</footer>
</div></body></html>""" % {
        "title": _e(head_title), "css": _CSS, "lede": _e(lede),
        "badges": "".join(badges), "stats": stats,
        "prep": prep_html, "map": map_block, "days": days_block,
        "weather": wx_html, "hotel": hotel_html, "budget": budget,
        "transit": transit_html, "tips": tips_html,
        "src": _e(src), "weather_src": _e(d.get("weather_source") or "未取到"),
        "ts": time.strftime("%Y-%m-%d %H:%M"),
    }
    return html


def save_html(plan, title=None, highlight=None, extras=None):
    d = _ensure_dir()
    name = _slug(plan) + ".html"
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_html(plan, title, highlight, extras))
    return "/static/guides/" + name, path


# ---------------- docx ----------------

def _docx_esc(s):
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _p(text, bold=False, size=None, color=None, space=0):
    run = '<w:r><w:rPr><w:rFonts w:eastAsia="微软雅黑"/><w:sz w:val="%d"/>%s%s</w:rPr><w:t xml:space="preserve">%s</w:t></w:r>' % (
        (size or 21), "<w:b/>" if bold else "",
        '<w:color w:val="%s"/>' % color if color else "", _docx_esc(text))
    return '<w:p><w:pPr><w:spacing w:after="%d"/></w:pPr>%s</w:p>' % (space or 80, run)


def _table(rows, widths=None):
    grid = "".join('<w:gridCol w:w="%d"/>' % (w or 2000) for w in (widths or [2000] * 3))
    trs = []
    for i, row in enumerate(rows):
        tcs = []
        for c in row:
            tcs.append('<w:tc><w:tcPr><w:tcW w:w="1800" w:type="dxa"/></w:tcPr>%s</w:tc>'
                       % _p(c, bold=(i == 0)))
        trs.append("<w:tr>%s</w:tr>" % "".join(tcs))
    return ('<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/>'
            '<w:tblBorders><w:top w:val="single" w:sz="4"/><w:left w:val="single" w:sz="4"/>'
            '<w:bottom w:val="single" w:sz="4"/><w:right w:val="single" w:sz="4"/>'
            '<w:insideH w:val="single" w:sz="4"/><w:insideV w:val="single" w:sz="4"/>'
            '</w:tblBorders></w:tblPr><w:tblGrid>%s</w:tblGrid>%s</w:tbl>') % (grid, "".join(trs))


def render_docx(plan, title=None):
    d = plan or {}
    s = d.get("summary") or {}
    parts = [
        _p(title or ("%s %d 天行程" % (d.get("destination"), d.get("days_count") or 1)),
           bold=True, size=36, color="B4472F", space=160),
        _p("目的地：%s　天数：%s　出发：%s　预算：¥%s" % (
            d.get("destination"), d.get("days_count"), d.get("start_date") or "—",
            _money(s.get("budget") or s.get("total_cost"))), size=20, color="6E635A"),
        _p(""),
    ]

    for day in d.get("days") or []:
        head = "D%s　%s %s" % (day.get("index"), day.get("date"), day.get("weekday") or "")
        if day.get("tmax") is not None:
            head += "　%s %s~%s℃" % (day.get("weather"), day.get("tmin"), day.get("tmax"))
        parts.append(_p(head, bold=True, size=26, space=120))

        rows = [["时间", "点位", "门票", "时长"]]
        for a in day.get("attractions") or []:
            rows.append([a.get("time") or "", a.get("name") or "",
                         ("¥%s" % _money(a.get("ticket_price"))) if a.get("ticket_price") else "免费",
                         "%s 小时" % round((a.get("visit_duration") or 120) / 60, 1)])
        if len(rows) > 1:
            parts.append(_table(rows, [1200, 3600, 1200, 1200]))

        if day.get("meals"):
            parts.append(_p("三餐：" + "　".join(
                "%s %s（¥%s）" % (m.get("slot"), m.get("name"), _money(m.get("cost")))
                for m in day["meals"]), size=19))
        if day.get("hotel"):
            h = day["hotel"]
            parts.append(_p("住宿：%s（%s档，约 ¥%s/晚，估算）" % (
                h.get("name"), h.get("level") or "", _money(h.get("price_per_night"))), size=19))
        elif day.get("accommodation"):
            parts.append(_p("住宿：%s" % day["accommodation"], size=19))
        if day.get("note"):
            parts.append(_p("注：" + day["note"], size=19, color="6E635A"))
        parts.append(_p(""))

    parts.append(_p("预算", bold=True, size=26, space=120))
    parts.append(_table([
        ["项目", "金额"],
        ["门票", "¥" + _money(s.get("ticket_cost"))],
        ["住宿", "¥" + _money(s.get("hotel_cost"))],
        ["餐饮", "¥" + _money(s.get("food_cost"))],
        ["市内交通", "¥" + _money(s.get("transport_cost"))],
        ["合计", "¥" + _money(s.get("total_cost"))],
    ], [2400, 2400]))

    parts.append(_p(""))
    for note in (s.get("notes") or []) + (d.get("tips") or []):
        parts.append(_p("· " + note, size=18, color="6E635A"))

    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           "<w:body>%s<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/>"
           "<w:pgMar w:top=\"1134\" w:right=\"1134\" w:bottom=\"1134\" w:left=\"1134\"/>"
           "</w:sectPr></w:body></w:document>") % "".join(parts)

    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     "</Types>")
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>")
    return doc, content_types, rels


def save_docx(plan, title=None):
    d = _ensure_dir()
    name = _slug(plan) + ".docx"
    path = os.path.join(d, name)
    doc, ct, rels = render_docx(plan, title)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
    return "/static/guides/" + name, path
