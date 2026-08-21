"""测试：AI 阅读提问模块（manual 兜底 + 门控规则文本）。"""
import pytest

from docmd.ai import AiAsker, NoAiConfigured, RULES, _chunk_text


class TestAiAsker:
    def test_manual_mode_produces_questions(self):
        md = (
            "# 发票\n\n```yaml\ndocument_status: awaiting_confirmation\nprocessing_gate: user_confirmation_required\n```\n\n"
            "# 待确认清单\n\n- `金额`（amount）等级 C | 候选1: 100.00；候选2: 9999.00\n"
            "- `日期`（date）等级 D"
        )
        asker = AiAsker(config={})  # 未配置
        r = asker.ask(md, mode="manual")
        assert r.engine == "manual"
        assert "金额" in r.questions
        assert "候选" in r.questions

    def test_auto_without_config_uses_manual(self):
        asker = AiAsker(config={})
        r = asker.ask("whatever", mode="auto")
        assert r.engine == "manual"

    def test_openai_without_config_raises(self):
        asker = AiAsker(config={})
        with pytest.raises(NoAiConfigured):
            asker.ask("x", mode="openai")

    def test_rules_contain_gate(self):
        assert "用户确认之前" in RULES
        assert "业务处理" in RULES
        assert "待确认问题清单" in RULES

    def test_chunking_no_silent_truncation(self):
        """超长文档必须分块，且完整可重建，绝不静默截断。"""
        long = ("# 长文档\n\n" + "C 字段 待确认 " * 2000 + "\nD 字段 无法识别 " * 2000)
        assert len(long) > 6000  # 确保确实超长
        chunks = _chunk_text(long)
        assert len(chunks) >= 2  # 确实分块
        # 所有块长度均不超过阈值（无截断）
        assert all(len(c) <= 6000 for c in chunks)
        # 完整内容应能从分块中重建（重叠不会丢失）
        joined = "\n".join(chunks)
        assert len(joined) >= len(long)  # 不缺失

    def test_manual_forces_user_confirmation_flow(self):
        """manual 兜底仍生成待确认清单，不可直接产出最终文件。"""
        md = "# 待确认清单\n\n- `金额`（amount）等级 C | 候选1: 100\n- `编号`（number_id）等级 D"
        asker = AiAsker(config={})
        r = asker.ask(md, mode="manual")
        assert r.engine == "manual"
        assert "金额" in r.questions and "编号" in r.questions
        assert "等级" in r.questions
