# -*- coding: utf-8 -*-
"""攻略产出：把行程数据渲染成可脱离本应用打开的 HTML，以及可编辑的 docx。

两个都不引第三方库：
    HTML —— 字符串模板，内联样式，双击就能看
    docx —— 手写 OOXML（zip 里塞 [Content_Types].xml / _rels/.rels / word/document.xml）

产物落在 static/guides/ 下，前端直接给下载链接。
"""

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
"""


def render_html(plan, title=None, highlight=None):
    """plan 是 itinerary.build_plan 的 structuredContent。"""
    d = plan or {}
    days = d.get("days") or []
    s = d.get("summary") or {}
    dest = d.get("destination") or "目的地"
    n_days = d.get("days_count") or len(days)
    spots = sum(len(x.get("attractions") or []) for x in days)

    head_title = title or "%s %d 天行程" % (dest, n_days)
    lede = (highlight or "").strip() or (
        "%s 天，%d 个点位，预算约 %s 元。按天排好时间、三餐与住宿，照着走就行。"
        % (n_days, spots, _money(s.get("total_cost"))))

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
        n_days, spots, _money(s.get("total_cost")), _money(s.get("per_day")))

    body = []
    for day in days:
        rainy = (day.get("rain_prob") or 0) >= 60
        w = ""
        if day.get("tmax") is not None:
            w = "%s %s~%s℃（降水 %s%%）" % (_e(day.get("weather")), day.get("tmin"),
                                            day.get("tmax"), day.get("rain_prob"))
        rows = []
        for a in day.get("attractions") or []:
            tag = "室内" if a.get("indoor") else "户外"
            rows.append(
                "<tr><td class='time'>%s</td><td><b>%s</b><br><span class='tag'>%s</span>"
                "<span class='tag'>%s</span>%s</td><td>%s</td><td>%s</td></tr>" % (
                    _e(a.get("time")), _e(a.get("name")), _e(tag),
                    _e((a.get("category") or "").split(";")[-1] or "点位"),
                    _e(("· " + a["address"]) if a.get("address") else ""),
                    ("¥%s" % _money(a.get("ticket_price"))) if a.get("ticket_price") else "免费/未标价",
                    _e("约 %s 小时" % (round((a.get("visit_duration") or 120) / 60, 1)))))
        table = ('<div class="tbl"><table><thead><tr>'
                 '<th>时间</th><th>点位</th><th>门票</th><th>建议时长</th>'
                 '</tr></thead><tbody>%s</tbody></table></div>') % "".join(rows) if rows else ""

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
                "，距首个点位 %s km" % h["distance_km"] if h.get("distance_km") is not None else "")
        elif day.get("accommodation"):
            hotel = '<div class="meals"><b>住宿</b>：%s</div>' % _e(day["accommodation"])

        note = ""
        if day.get("note"):
            note = '<div class="%s">%s</div>' % ("warn" if rainy else "note", _e(day["note"]))

        body.append(
            '<div class="day"><div class="day-hd">'
            '<span class="d%s">D%s</span><h3>%s %s</h3><span class="meta">%s</span></div>'
            '%s%s%s%s%s</div>' % (
                " b" if rainy else "", day.get("index"), _e(day.get("date")),
                _e(day.get("weekday") or ""), w, table, meals, hotel, note,
                '<div class="meals"><b>主题</b>：%s</div>' % _e(day.get("theme")) if day.get("theme") else ""))

    budget_rows = "".join(
        "<tr><td>%s</td><td>¥%s</td></tr>" % (k, _money(v)) for k, v in (
            ("门票", s.get("ticket_cost")), ("住宿", s.get("hotel_cost")),
            ("餐饮", s.get("food_cost")), ("市内交通", s.get("transport_cost")))
    ) + "<tr><td><b>合计</b></td><td><b>¥%s</b></td></tr>" % _money(s.get("total_cost"))
    budget = ('<h2>预算表</h2><div class="tbl"><table><thead><tr><th>项目</th><th>金额</th>'
              '</tr></thead><tbody>%s</tbody></table></div>') % budget_rows

    caveats = "".join('<div class="note">%s</div>' % _e(n) for n in (s.get("notes") or []))
    tips = "".join('<div class="note">%s</div>' % _e(t) for t in (d.get("tips") or []))

    src = {"amap": "高德地图实时检索", "local": "本地示例数据", "template": "通用框架",
           "empty": "未检索到点位"}.get(d.get("poi_source"), d.get("poi_source") or "")

    html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title><style>%(css)s</style></head><body>
<header><div class="wrap"><p class="eyebrow">智行 · 出行攻略</p>
<h1>%(title)s</h1><p class="lede">%(lede)s</p>
<div class="badges">%(badges)s</div></div></header>
<div class="wrap">%(stats)s
<h2>逐日安排</h2>%(body)s
%(budget)s
%(tips)s%(caveats)s
<footer>点位来源：%(src)s　｜　天气：%(weather)s　｜　生成时间：%(ts)s<br>
门票与房价为估算口径，出行前请以景区/酒店公示为准。</footer>
</div></body></html>""" % {
        "title": _e(head_title), "css": _CSS, "lede": _e(lede),
        "badges": "".join(badges), "stats": stats, "body": "".join(body),
        "budget": budget, "tips": tips, "caveats": caveats,
        "src": _e(src), "weather": _e(d.get("weather_source") or "未取到"),
        "ts": time.strftime("%Y-%m-%d %H:%M"),
    }
    return html


def save_html(plan, title=None, highlight=None):
    d = _ensure_dir()
    name = _slug(plan) + ".html"
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_html(plan, title, highlight))
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
