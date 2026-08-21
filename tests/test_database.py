"""测试：任务状态机、数据库 CRUD 与确认日志。"""
from pathlib import Path
import json

import pytest

from docmd.core.constants import TaskState, can_transition
from docmd.core.document import ContentBlock, Document
from docmd.core.constants import BlockType
from docmd.storage.database import Database


class TestStateMachine:
    def test_valid_linear_flow(self):
        seq = [TaskState.IMPORTED, TaskState.PREPROCESSING, TaskState.RECOGNIZING,
               TaskState.GRADING, TaskState.AWAITING_CONFIRM, TaskState.CONFIRMED,
               TaskState.DONE]
        for a, b in zip(seq, seq[1:]):
            assert can_transition(a, b), f"{a} -> {b}"

    def test_illegal_skip(self):
        assert not can_transition(TaskState.IMPORTED, TaskState.CONFIRMED)
        assert not can_transition(TaskState.AWAITING_CONFIRM, TaskState.DONE)


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    yield d
    d.close()


class TestDatabase:
    def test_upsert_and_state(self, db):
        db.upsert_task("t1", "c:/x/a.pdf", "测试文档", "pdf", TaskState.IMPORTED)
        t = db.get_task("t1")
        assert t["state"] == "imported"
        assert t["title"] == "测试文档"

    def test_set_state_enforces_gate(self, db):
        db.upsert_task("t1", "c:/x/a.pdf", "t", "pdf", TaskState.IMPORTED)
        # 非法跳跃：imported -> confirmed 被拒
        after = db.set_state("t1", TaskState.CONFIRMED)
        assert after.value == "imported"
        # 合法推进
        assert db.set_state("t1", TaskState.PREPROCESSING).value == "preprocessing"

    def test_doc_roundtrip(self, db):
        doc = Document("t1", "c:/x/a.pdf", "标题")
        doc.blocks.append(ContentBlock(id="b1", type=BlockType.HEADING, text="第一章", page=1))
        db.upsert_task("t1", "c:/x/a.pdf", "标题", "pdf", TaskState.AWAITING_CONFIRM)
        db.save_doc("t1", doc)
        loaded = db.load_doc("t1")
        assert loaded is not None
        assert loaded.title == "标题"
        assert loaded.blocks[0].text == "第一章"
        assert loaded.blocks[0].type == BlockType.HEADING

    def test_confirm_log(self, db):
        db.upsert_task("t1", "c:/x/a.pdf", "t", "pdf", TaskState.AWAITING_CONFIRM)
        db.append_confirm("t1", "金额", "amount", "C", ["100", "IO0"], "100", "manual")
        logs = db.confirm_log("t1")
        assert len(logs) == 1
        assert logs[0]["field_key"] == "金额"

    def test_config(self, db):
        db.set_config("ai_config", "model", "deepseek")
        assert db.get_config("ai_config", "model") == "deepseek"
        assert db.all_config("ai_config") == {"model": "deepseek"}
