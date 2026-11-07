# -*- coding: utf-8 -*-
"""附件：把用户上传的文件变成「模型能读的一段话」。

为什么要转成文本：Pi 的 RPC prompt 只接受字符串，塞不进图片二进制。
所以在这里先把文件处理掉——图片让视觉模型描述一遍，文档抽正文——
最终只往 prompt 里注入一段文字。这样 Pi 那边完全不用改。

支持：图片（png/jpg/webp/gif）、文本（txt/md/csv）、Word（docx）、PDF（尽力而为）。
"""

import base64
import json
import os
import re
import urllib.request
import zipfile

import config

MAX_BYTES = 8 * 1024 * 1024          # 单文件 8MB
MAX_TEXT = 6000                      # 注入 prompt 的正文上限
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".json"}


class AttachError(Exception):
    pass


def upload_dir():
    d = os.path.join(config.DATA_DIR, "uploads")
    os.makedirs(d, exist_ok=True)
    return d


def _safe_name(name):
    base = os.path.basename(name or "file")
    base = re.sub(r"[^\w.\-\u4e00-\u9fa5]", "_", base)
    return base[:80] or "file"


def save(name, data_b64):
    """存盘并返回 (路径, 扩展名, 字节数)。"""
    try:
        raw = base64.b64decode(data_b64, validate=False)
    except Exception:
        raise AttachError("文件内容不是合法的 base64")
    if len(raw) > MAX_BYTES:
        raise AttachError("文件超过 8MB，先压缩一下")
    if not raw:
        raise AttachError("空文件")
    safe = _safe_name(name)
    path = os.path.join(upload_dir(), safe)
    with open(path, "wb") as f:
        f.write(raw)
    return path, os.path.splitext(safe)[1].lower(), len(raw)


# ---------------- 文本提取 ----------------

def _docx_text(path):
    """docx 就是个 zip，正文在 word/document.xml，段落用 <w:p>，文字在 <w:t>。"""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    paras = re.split(r"</w:p>", xml)
    lines = []
    for p in paras:
        # 注意 <w:tbl> 也以 <w:t 开头，所以 w:t 后面必须是空格或直接闭合，
        # 否则会把表格标签当成文本节点，抽出一堆 <w:tblPr> 之类的垃圾
        texts = re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", p, re.S)
        if texts:
            line = "".join(texts)
            line = (line.replace("&amp;", "&").replace("&lt;", "<")
                    .replace("&gt;", ">").replace("&quot;", '"'))
            if line.strip():
                lines.append(line.strip())
    return "\n".join(lines)


def _pdf_text(path):
    """PDF 没有标准库解析器，这里只做「尽力而为」：
    解压 FlateDecode 流，抓 BT/ET 之间的 Tj、TJ 文本。复杂排版会丢，够读行程单。"""
    import zlib
    with open(path, "rb") as f:
        raw = f.read()
    chunks = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        body = m.group(1)
        try:
            body = zlib.decompress(body)
        except Exception:
            continue
        if b"BT" not in body:
            continue
        for tm in re.finditer(rb"\((?:\\.|[^\\()])*\)\s*Tj", body):
            seg = tm.group(0)
            chunks.append(seg[1:seg.rfind(b")")])
        for tm in re.finditer(rb"\[(.*?)\]\s*TJ", body, re.S):
            for s in re.finditer(rb"\((?:\\.|[^\\()])*\)", tm.group(1)):
                chunks.append(s.group(0)[1:-1])
    text = b" ".join(chunks).decode("latin-1", "replace")
    text = text.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
    return re.sub(r"\s{2,}", " ", text).strip()


def extract_text(path, ext):
    if ext in TEXT_EXT:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    if ext == ".docx":
        try:
            return _docx_text(path)
        except Exception as e:  # noqa: BLE001
            raise AttachError("这个 docx 读不出来（%s），另存为 txt 再传试试" % type(e).__name__)
    if ext == ".pdf":
        try:
            txt = _pdf_text(path)
        except Exception:  # noqa: BLE001
            txt = ""
        if len(txt.strip()) < 20:
            raise AttachError("这个 PDF 是扫描件或加密的，抽不出文字；可以截图发我")
        return txt
    if ext == ".doc":
        raise AttachError("老版 .doc 解析不了，另存为 .docx 再传")
    return ""


# ---------------- 图片理解 ----------------

def describe_image(path, ext, question="这张图片里有什么？和出行规划有关的重点是什么？"):
    """让视觉模型描述图片。返回一段描述文本。

    注意：deepseek-v4.1-flash 是推理模型，思考会吃掉 token，
    max_tokens 给小了会出现「有 image_tokens 但没输出」的怪现象。
    """
    if not (config.LLM_API_KEY and config.LLM_BASE_URL):
        raise AttachError("没配模型 Key，图片看不了")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "image/png")

    body = json.dumps({
        "model": config.LLM_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mime, b64)}},
        ]}],
        "max_tokens": 900,
    }).encode("utf-8")

    req = urllib.request.Request(
        config.LLM_BASE_URL.rstrip("/") + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + config.LLM_API_KEY,
                 "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode())
    text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if not text.strip():
        raise AttachError("模型没看懂这张图，换一张或补充文字说明")
    return text.strip()


# ---------------- 汇总成一段可注入的文字 ----------------

def build_context(items):
    """items: [{name, kind, text}] -> 注入 prompt 的说明段。"""
    usable = [i for i in items if (i.get("text") or "").strip()]
    if not usable:
        return ""
    parts = ["[用户上传了 %d 个附件，内容如下]" % len(usable)]
    for it in usable:
        body = it["text"].strip()
        if len(body) > MAX_TEXT:
            body = body[:MAX_TEXT] + "……（内容过长，已截断）"
        parts.append("\n【%s（%s）】\n%s" % (it.get("name") or "附件", it.get("kind") or "文件", body))
    parts.append("\n[以上是附件内容。回答要基于它们；附件里没写的信息仍然要调工具查，不要凭空补。]")
    return "\n".join(parts)
