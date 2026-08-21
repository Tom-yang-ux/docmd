"""SQLite 数据库访问层。

保存：任务、识别结果（中间结构 Document JSON）、确认记录、模型配置、AI 配置。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..core.constants import TaskState
from ..core.document import Document

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id        TEXT PRIMARY KEY,
    source_path    TEXT NOT NULL,
    title          TEXT,
    file_type      TEXT,
    state          TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    doc_json       TEXT,            -- Document 中间结构序列化
    preproc_meta   TEXT,            -- 页数、是否文本/视觉、增强信息
    engine_version TEXT,
    ai_questioned  INTEGER NOT NULL DEFAULT 0,   -- AI 是否已完成汇总提问（门控）
    ai_answered_at TEXT
);

CREATE TABLE IF NOT EXISTS confirm_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    field_key   TEXT NOT NULL,
    field_type  TEXT,
    grade       TEXT,
    candidate_a TEXT,
    candidate_b TEXT,   -- 或更多候选
    user_value  TEXT,
    source      TEXT,
    confirmed_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS model_config (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_config (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processing_metrics (
    task_id TEXT PRIMARY KEY,
    page_count INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds REAL NOT NULL DEFAULT 0,
    model_calls INTEGER NOT NULL DEFAULT 0,
    cache_hits INTEGER NOT NULL DEFAULT 0,
    engine TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """线程安全的 SQLite 封装。"""

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._local = threading.local()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "_conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            self._local._conn = conn
        return conn

    def _init_schema(self) -> None:
        conn = self._conn()
        conn.executescript(_SCHEMA)
        self._migrate(conn)
        conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """为旧库补全新增列（CREATE IF NOT EXISTS 不会改已有表）。"""
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        if "ai_questioned" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN ai_questioned INTEGER NOT NULL DEFAULT 0")
        if "ai_answered_at" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN ai_answered_at TEXT")

    # ---------- 任务 ----------
    def upsert_task(self, task_id: str, source_path: str, title: str,
                    file_type: str, state: TaskState) -> None:
        now = _now()
        conn = self._conn()
        conn.execute(
            """INSERT INTO tasks (task_id, source_path, title, file_type, state, created_at, updated_at)
               VALUES (:id, :sp, :t, :ft, :st, :now, :now)
               ON CONFLICT(task_id) DO UPDATE SET
                 state=excluded.state, updated_at=excluded.updated_at,
                 source_path=excluded.source_path, title=excluded.title, file_type=excluded.file_type
            """,
            {"id": task_id, "sp": source_path, "t": title, "ft": file_type,
             "st": state.value if isinstance(state, TaskState) else state, "now": now},
        )
        conn.commit()

    def set_state(self, task_id: str, state: TaskState) -> Optional[TaskState]:
        """更新任务状态，校验状态机合法性。返回更新后的状态；非法返回 None。"""
        prev = self.get_task(task_id)
        if prev is None:
            return None
        from ..core.constants import can_transition
        if not can_transition(TaskState(prev["state"]), state):
            return TaskState(prev["state"])
        # 门控：仅允许 AWAITING_CONFIRM -> CONFIRMED（前提所有待确认已解决）
        self._conn().execute(
            "UPDATE tasks SET state=:s, updated_at=:n WHERE task_id=:id",
            {"s": state.value, "n": _now(), "id": task_id},
        )
        self._conn().commit()
        return state

    def get_task(self, task_id: str) -> Optional[dict]:
        row = self._conn().execute(
            "SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list_tasks(self) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def save_doc(self, task_id: str, doc: Document) -> None:
        conn = self._conn()
        conn.execute(
            "UPDATE tasks SET doc_json=:d, updated_at=:n WHERE task_id=:id",
            {"d": json.dumps(doc.to_dict(), ensure_ascii=False), "n": _now(), "id": task_id},
        )
        conn.commit()

    def load_doc(self, task_id: str) -> Optional[Document]:
        row = self._conn().execute(
            "SELECT doc_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None or not row["doc_json"]:
            return None
        return Document.from_dict(json.loads(row["doc_json"]))

    def save_metrics(self, task_id: str, *, page_count: int, elapsed_seconds: float,
                     model_calls: int, cache_hits: int, engine: str) -> None:
        self._conn().execute(
            """INSERT INTO processing_metrics
               (task_id,page_count,elapsed_seconds,model_calls,cache_hits,engine,updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(task_id) DO UPDATE SET
                 page_count=excluded.page_count, elapsed_seconds=excluded.elapsed_seconds,
                 model_calls=excluded.model_calls, cache_hits=excluded.cache_hits,
                 engine=excluded.engine, updated_at=excluded.updated_at""",
            (task_id, page_count, elapsed_seconds, model_calls, cache_hits, engine, _now()),
        )
        self._conn().commit()

    def metrics(self, task_id: str) -> Optional[dict]:
        row = self._conn().execute("SELECT * FROM processing_metrics WHERE task_id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    # ---------- 确认日志 ----------
    def append_confirm(self, task_id: str, field_key: str, field_type: str,
                       grade: str, candidates: list[str], user_value: str, source: str) -> None:
        conn = self._conn()
        conn.execute(
            """INSERT INTO confirm_log
               (task_id, field_key, field_type, grade, candidate_a, candidate_b, user_value, source, confirmed_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (task_id, field_key, field_type, grade,
             candidates[0] if candidates else None,
             candidates[1] if len(candidates) > 1 else None,
             user_value, source, _now()),
        )
        conn.commit()

    def confirm_log(self, task_id: str) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM confirm_log WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
        return [dict(r) for r in rows]

    # ---------- 配置 ----------
    def get_config(self, table: str, key: str) -> Optional[str]:
        row = self._conn().execute(
            f"SELECT value FROM {table} WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_config(self, table: str, key: str, value: str) -> None:
        conn = self._conn()
        conn.execute(
            f"""INSERT INTO {table} (key, value, updated_at) VALUES (?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, value, _now()),
        )
        conn.commit()

    def all_config(self, table: str) -> dict[str, str]:
        rows = self._conn().execute(f"SELECT key, value FROM {table}").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ---------- AI 提问门控 ----------
    def set_ai_questioned(self, task_id: str, value: bool = True) -> None:
        conn = self._conn()
        conn.execute(
            "UPDATE tasks SET ai_questioned=:v, ai_answered_at=:t, updated_at=:n WHERE task_id=:id",
            {"v": 1 if value else 0, "t": _now() if value else None, "n": _now(), "id": task_id},
        )
        conn.commit()

    def ai_questioned(self, task_id: str) -> bool:
        row = self._conn().execute(
            "SELECT ai_questioned FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return bool(row and row["ai_questioned"])

    def close(self) -> None:
        conn = getattr(self._local, "_conn", None)
        if conn is not None:
            conn.close()
            self._local._conn = None
