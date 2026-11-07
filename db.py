# -*- coding: utf-8 -*-
"""SQLite 数据层：账号、会话、消息。

设计要点：
1. 单文件 SQLite，随项目走，不需要额外安装数据库；
2. 所有写操作走同一把锁，ThreadingHTTPServer 多线程下不会出现
   「database is locked」；
3. 开启 WAL + busy_timeout，读写并发更稳。
"""
import hashlib
import os
import secrets
import sqlite3
import threading
import time

from config import DB_PATH

_LOCK = threading.RLock()
_CONN = None


def _connect():
    global _CONN
    if _CONN is not None:
        return _CONN
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt          TEXT NOT NULL,
            created_at    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            title      TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            role       TEXT NOT NULL,
            content    TEXT NOT NULL,
            steps_json TEXT NOT NULL DEFAULT '[]',
            cards_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
        CREATE TABLE IF NOT EXISTS tokens (
            token      TEXT PRIMARY KEY,
            user_id    INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    _CONN = conn
    return conn


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------------- 账号 ----------------
def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 120000).hex()


def create_user(username, password):
    """注册。用户名重复返回 (None, '用户名已存在')。"""
    username = (username or "").strip()
    if not username or len(password or "") < 4:
        return None, "用户名不能为空，密码至少 4 位"
    salt = secrets.token_hex(16)
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute(
                "INSERT INTO users(username, password_hash, salt, created_at) VALUES (?,?,?,?)",
                (username, _hash(password, salt), salt, now()),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return None, "用户名已存在"
    return cur.lastrowid, None


def verify_user(username, password):
    with _LOCK:
        conn = _connect()
        row = conn.execute("SELECT * FROM users WHERE username = ?", ((username or "").strip(),)).fetchone()
    if not row:
        return None
    if _hash(password or "", row["salt"]) != row["password_hash"]:
        return None
    return {"id": row["id"], "username": row["username"]}


def issue_token(user_id):
    token = secrets.token_urlsafe(24)
    with _LOCK:
        conn = _connect()
        conn.execute("INSERT INTO tokens(token, user_id, created_at) VALUES (?,?,?)", (token, user_id, now()))
        conn.commit()
    return token


def user_by_token(token):
    if not token:
        return None
    with _LOCK:
        conn = _connect()
        row = conn.execute(
            "SELECT u.id, u.username FROM tokens t JOIN users u ON u.id = t.user_id WHERE t.token = ?", (token,)
        ).fetchone()
    return {"id": row["id"], "username": row["username"]} if row else None


# ---------------- 会话 / 消息 ----------------
def create_session(user_id, title="新对话"):
    with _LOCK:
        conn = _connect()
        cur = conn.execute(
            "INSERT INTO sessions(user_id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (user_id, title, now(), now()),
        )
        conn.commit()
    return cur.lastrowid


def list_sessions(user_id):
    with _LOCK:
        conn = _connect()
        rows = conn.execute(
            "SELECT id, title, updated_at FROM sessions WHERE user_id = ? ORDER BY id DESC", (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def rename_session(session_id, user_id, title):
    with _LOCK:
        conn = _connect()
        conn.execute("UPDATE sessions SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                     (title[:60], now(), session_id, user_id))
        conn.commit()


def delete_session(session_id, user_id):
    with _LOCK:
        conn = _connect()
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id))
        conn.commit()


def session_owner(session_id):
    with _LOCK:
        conn = _connect()
        row = conn.execute("SELECT user_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return row["user_id"] if row else None


def add_message(session_id, role, content, steps=None, cards=None):
    import json
    with _LOCK:
        conn = _connect()
        cur = conn.execute(
            "INSERT INTO messages(session_id, role, content, steps_json, cards_json, created_at) VALUES (?,?,?,?,?,?)",
            (session_id, role, content, json.dumps(steps or [], ensure_ascii=False),
             json.dumps(cards or [], ensure_ascii=False), now()),
        )
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now(), session_id))
        conn.commit()
    return cur.lastrowid


def list_messages(session_id, limit=200):
    import json
    with _LOCK:
        conn = _connect()
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC LIMIT ?", (session_id, limit)
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "role": r["role"], "content": r["content"],
            "steps": json.loads(r["steps_json"] or "[]"),
            "cards": json.loads(r["cards_json"] or "[]"),
            "created_at": r["created_at"],
        })
    return out


def stats():
    with _LOCK:
        conn = _connect()
        u = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        s = conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
        m = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
    return {"users": u, "sessions": s, "messages": m}
