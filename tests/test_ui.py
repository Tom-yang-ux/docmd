"""UI 冒烟测试：离屏实例化 main_window，验证 3 个标签页创建且不抛错。"""
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def win(qapp, tmp_path):
    from docmd.ui.main_window import MainWindow
    w = MainWindow(str(tmp_path / "data"))
    yield w
    w.pipeline.close()


def test_tabs_created(win):
    tabs = win.centralWidget()
    names = [tabs.tabText(i) for i in range(tabs.count())]
    assert {"开始", "待确认", "AI 设置"} <= set(names)
    assert not hasattr(win, "combo_vision")
    assert hasattr(win, "drop_zone")
    assert hasattr(win, "crop_preview")
    assert win.acceptDrops() is True
    assert win.width() == 560


# 冒烟：通过 pipeline 制造任务并刷新列表
def test_task_list_refresh(win):
    import pymupdf
    from conftest import make_cjk_pdf
    src = Path(tempfile.mkdtemp()) / "inv.pdf"
    make_cjk_pdf(src, ["发票", "金额: 100.00"])
    win.pipeline.import_files([str(src)])
    win._refresh_tasks()
    assert win.task_list.count() >= 1


def test_drop_import_helper(win, tmp_path):
    from conftest import make_cjk_pdf
    src = tmp_path / "dropped.pdf"
    make_cjk_pdf(src, ["金额: 100.00"])
    win._import_paths([str(src)], auto_process=False)
    assert win.task_list.count() >= 1
