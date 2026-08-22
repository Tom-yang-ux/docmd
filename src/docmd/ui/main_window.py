"""DocMD 桌面主窗口。

标签页：任务（导入/处理）、确认、AI 设置。
MVP 界面以可用为主：任务列表、处理进度、待确认字段检查、AI 确认问题、设置持久化。
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QRect, QUrl
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMainWindow, QMessageBox, QPushButton, QPlainTextEdit,
    QTabWidget, QTextBrowser, QVBoxLayout, QWidget, QSplitter, QCheckBox,
    QSpinBox, QInputDialog, QDialog, QDialogButtonBox, QGroupBox, QApplication,
    QSystemTrayIcon, QMenu,
)
from PySide6.QtGui import QPixmap, QDesktopServices, QIcon, QAction

from ..ai import AiAsker
from ..pipeline import Pipeline
from ..extractors.vision import VisionUnavailable
from ..model_manager import ModelManager


class ProcessWorker(QThread):
    """在后台线程执行 process_one，避免阻塞 UI。"""
    step = Signal(str, str)          # task_id, message
    finished_task = Signal(str, object, str)  # task_id, doc_or_None, error

    def __init__(self, pipeline: Pipeline, task_id: str, zoom: float = 2.0):
        super().__init__()
        self.pipeline = pipeline
        self.task_id = task_id
        self.zoom = zoom

    def run(self):
        try:
            self.step.emit(self.task_id, "开始处理…")
            doc = self.pipeline.process_one(self.task_id, zoom=self.zoom)
            self.finished_task.emit(self.task_id, doc, "")
        except Exception as e:  # noqa: BLE001
            self.finished_task.emit(self.task_id, None, str(e))


class MainWindow(QMainWindow):
    SUPPORTED_SUFFIXES = {".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg"}

    def __init__(self, data_dir: str, db=None, pipeline: Pipeline | None = None):
        super().__init__()
        self.setWindowTitle("DocMD 高可信 Markdown 工具")
        self.resize(560, 430)
        self.setMinimumSize(520, 390)
        self.setAcceptDrops(True)
        self._quitting = False
        # 生产默认必须使用真实文档视觉模型；Mock 仅能由测试代码显式选择。
        self._owns_pipeline = pipeline is None
        self.pipeline = pipeline or Pipeline(data_dir, db=db, vision_engine="paddle_ocr")
        self._build_ui()
        self._build_tray()

    def _build_tray(self) -> None:
        """关闭窗口时常驻托盘，只有显式“退出”才释放任务与数据库。"""
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.windowIcon() or QIcon())
        menu = QMenu(self)
        show_action = QAction("打开 DocMD", self)
        show_action.triggered.connect(self._show_from_tray)
        quit_action = QAction("退出 DocMD", self)
        quit_action.triggered.connect(self._quit_from_tray)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self._show_from_tray()
                                    if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _quit_from_tray(self) -> None:
        self._quitting = True
        self.close()

    # ---------- UI 搭建 ----------
    def _build_ui(self):
        tabs = QTabWidget()
        tabs.addTab(self._build_tasks_tab(), "开始")
        tabs.addTab(self._build_confirm_tab(), "待确认")
        tabs.addTab(self._build_ai_tab(), "AI 设置")
        tabs.addTab(self._build_models_tab(), "模型与环境")
        self.setCentralWidget(tabs)

    # ---------- 任务页 ----------
    def _build_tasks_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        title = QLabel("高可信 Markdown")
        title.setObjectName("title")
        subtitle = QLabel("拖入文档后自动识别、分级并生成待确认 Markdown")
        subtitle.setObjectName("subtitle")
        lay.addWidget(title)
        lay.addWidget(subtitle)

        self.drop_zone = QPushButton("\n将 PDF、图片、Word、Excel 或 PPT 拖到这里\n\n也可以点击选择文件")
        self.drop_zone.setObjectName("dropZone")
        self.drop_zone.setMinimumHeight(170)
        self.drop_zone.clicked.connect(self._on_import)
        lay.addWidget(self.drop_zone)

        top = QHBoxLayout()
        self.btn_import = QPushButton("选择文件")
        self.btn_import.clicked.connect(self._on_import)
        self.btn_process = QPushButton("重新处理")
        self.btn_process.clicked.connect(self._on_process)
        self.btn_open_review = QPushButton("打开待确认 Markdown")
        self.btn_open_review.clicked.connect(self._open_review_markdown)
        self.btn_open_final = QPushButton("打开最终 Markdown")
        self.btn_open_final.clicked.connect(self._open_final_markdown)
        top.addWidget(self.btn_import)
        top.addWidget(self.btn_process)
        top.addWidget(self.btn_open_review)
        top.addWidget(self.btn_open_final)
        top.addStretch(1)
        lay.addLayout(top)

        # 任务列表
        self.task_list = QListWidget()
        self.task_list.itemSelectionChanged.connect(self._on_task_selected)
        lay.addWidget(self.task_list)

        # 处理状态
        self.status_label = QLabel("就绪")
        lay.addWidget(self.status_label)
        w.setStyleSheet("""
            QLabel#title { font-size: 22px; font-weight: 700; color: #1f2937; }
            QLabel#subtitle { color: #6b7280; padding-bottom: 8px; }
            QPushButton#dropZone { background: #edf7ef; border: 2px dashed #68a875;
              border-radius: 14px; color: #2c6a39; font-size: 16px; font-weight: 600; }
            QPushButton#dropZone:hover { background: #e2f2e5; }
        """)
        return w

    def _on_import(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择文档", "",
            "文档 (*.pdf *.docx *.xlsx *.pptx *.png *.jpg *.jpeg);;所有文件 (*.*)")
        if not files:
            return
        self._import_paths(files)

    def _import_paths(self, paths, auto_process: bool = True) -> None:
        files = [str(Path(path)) for path in paths if Path(path).suffix.lower() in self.SUPPORTED_SUFFIXES]
        if not files:
            QMessageBox.information(self, "不支持的文件", "请拖入 PDF、Word、Excel、PPT 或图片文件。")
            return
        try:
            result = self.pipeline.import_files(files)
            self._refresh_tasks()
            if result:
                self._current_task_id = result[-1]["task_id"]
                for index in range(self.task_list.count()):
                    if self.task_list.item(index).text().startswith(self._current_task_id):
                        self.task_list.setCurrentRow(index)
                        break
                if auto_process:
                    self.status_label.setText("已导入，正在自动识别…")
                    self._on_process()
                else:
                    self.status_label.setText(f"已导入 {len(result)} 个任务")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "导入失败", str(e))

    def dragEnterEvent(self, event):  # noqa: N802 - Qt 固定方法名
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if any(url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in self.SUPPORTED_SUFFIXES for url in urls):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):  # noqa: N802 - Qt 固定方法名
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        self._import_paths([url.toLocalFile() for url in urls if url.isLocalFile()])
        event.acceptProposedAction()

    def _refresh_tasks(self):
        self.task_list.clear()
        for t in self.pipeline.db.list_tasks():
            states = {0: "已导入", 1: "预处理", 2: "识别", 3: "分级",
                      4: "等待确认", 5: "已确认", 6: "已完成"}
            self.task_list.addItem(f"{t['task_id']}  [{t.get('state','?')}]  {t['title']}")

    def _on_task_selected(self):
        item = self.task_list.currentItem()
        if item:
            task_id = item.text().split("  [")[0]
            self._current_task_id = task_id

    def _open_markdown(self, final: bool = False) -> None:
        task_id = self._selected_task_id()
        if not task_id:
            QMessageBox.information(self, "提示", "请先选择一个任务。")
            return
        path = self.pipeline.final_path(task_id) if final else self.pipeline.review_path(task_id)
        if not Path(path).exists():
            label = "最终 Markdown" if final else "待确认 Markdown"
            QMessageBox.information(self, "文件尚未生成", f"{label} 尚未生成。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_review_markdown(self) -> None:
        self._open_markdown(final=False)

    def _open_final_markdown(self) -> None:
        self._open_markdown(final=True)

    def _on_process(self):
        if not getattr(self, "_current_task_id", None):
            QMessageBox.information(self, "提示", "请先在列表选择一个任务")
            return
        task_id = self._current_task_id
        vision = "paddle_ocr"
        # 默认主引擎固定为 PaddleOCR-VL；程序内部按需触发复核，用户无需选择模型。
        try:
            self.pipeline.set_vision_engine(vision)
            task = self.pipeline.db.get_task(task_id) or {}
            self.pipeline.ensure_dual_engine_ready(task.get("file_type", ""), task.get("source_path"))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "双引擎未就绪", str(e))
            return
        self.status_label.setText(f"处理中 {task_id}… (引擎:{vision})")
        self.worker = ProcessWorker(self.pipeline, task_id)
        self.worker.finished_task.connect(self._on_worker_done)
        self.worker.start()

    def _on_worker_done(self, task_id, doc, error):
        if error:
            self.status_label.setText(f"失败: {error}")
            QMessageBox.critical(self, "处理失败", error)
        else:
            metrics = self.pipeline.db.metrics(task_id) or {}
            self.status_label.setText(
                f"处理完成，字段已分级｜{metrics.get('page_count', 0)} 页｜"
                f"模型调用 {metrics.get('model_calls', 0)}｜缓存命中 {metrics.get('cache_hits', 0)}｜"
                f"{metrics.get('elapsed_seconds', 0)} 秒")
            self._refresh_tasks()

    # ---------- 确认页 ----------
    def _build_confirm_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.confirm_list = QListWidget()
        self.confirm_detail = QTextBrowser()
        self.crop_preview = QLabel("选择待确认字段后显示原图局部")
        self.crop_preview.setAlignment(Qt.AlignCenter)
        self.crop_preview.setMinimumHeight(220)
        self.crop_preview.setWordWrap(True)
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.confirm_list)
        split.addWidget(self.confirm_detail)
        split.addWidget(self.crop_preview)
        lay.addWidget(split)
        self.confirm_list.currentItemChanged.connect(self._on_confirm_item)

        btn_row = QHBoxLayout()
        self.btn_refresh = QPushButton("刷新待确认")
        self.btn_refresh.clicked.connect(self._on_refresh_confirm)
        self.btn_confirm = QPushButton("采用候选值/手动确认")
        self.btn_confirm.clicked.connect(self._on_confirm)
        self.btn_batch_confirm = QPushButton("批量确认…")
        self.btn_batch_confirm.clicked.connect(self._on_batch_confirm)
        self.btn_ask_ai = QPushButton("AI 汇总提问")
        self.btn_ask_ai.clicked.connect(self._on_ask_ai)
        self.btn_final = QPushButton("生成最终 Markdown")
        self.btn_final.clicked.connect(self._on_final)
        btn_row.addWidget(self.btn_refresh)
        btn_row.addWidget(self.btn_ask_ai)
        btn_row.addWidget(self.btn_confirm)
        btn_row.addWidget(self.btn_batch_confirm)
        btn_row.addWidget(self.btn_final)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)
        return w

    def _selected_task_id(self) -> str:
        item = self.task_list.currentItem()
        if not item:
            return ""
        return item.text().split("  [")[0]

    def _on_refresh_confirm(self):
        task_id = self._selected_task_id()
        if not task_id:
            QMessageBox.information(self, "提示", "请先选中一个任务")
            return
        doc = self.pipeline.db.load_doc(task_id)
        if doc is None:
            QMessageBox.information(self, "提示", "该任务尚未处理")
            return
        self.confirm_list.clear()
        self._confirm_cache = {"task_id": task_id, "doc": doc, "index": {}}
        pending = doc.pending_fields()
        if not pending:
            state = "AI 已提问" if self.pipeline.db.ai_questioned(task_id) else "AI 未提问"
            self.confirm_detail.setPlainText(
                "无待确认字段。\n\n" + state + "\n提示：仍需完成「AI 汇总提问」后才能生成最终文件。")
        for i, f in enumerate(pending):
            self.confirm_list.addItem(f"{f.key} [{f.grade.value}] 第{f.bbox.page if f.bbox and f.bbox.page else '?'}页")
            self._confirm_cache["index"][i] = f.id

    def _get_field(self, fid: str):
        doc = self._confirm_cache["doc"]
        for f in doc.all_fields():
            if f.id == fid:
                return f
        return None

    def _on_confirm_item(self, *args):
        if not hasattr(self, "_confirm_cache"):
            return
        fid = self._confirm_cache.get("index", {}).get(self.confirm_list.currentRow())
        f = self._get_field(fid) if fid else None
        if f is None:
            return
        cands = "；".join(
            f"候选{i+1}({c.engine}): {c.text} (conf={c.confidence or '?'})"
            for i, c in enumerate(f.candidates))
        page = f.bbox.page if f.bbox and f.bbox.page else "?"
        loc = f"页码: {page}\n坐标: {self._fmt_bbox(f.bbox)}" if f.bbox else "页码: 未知"
        # 来源原图区域
        src_img = ""
        block = next((b for b in self._confirm_cache["doc"].blocks if b.id == f.block_id), None)
        if block and block.meta.get("source_image"):
            src_img = f"\n来源原图: {block.meta['source_image']}"
        self.confirm_detail.setPlainText(
            f"【字段】{f.key}  （类型:{f.field_type}，等级:{f.grade.value}，关键字段:{'是' if f.is_key_field else '否'}）\n"
            f"{loc}\n"
            f"冲突原因: {f.conflict_reason or '无'}\n"
            f"候选结果:\n{cands}\n"
            f"{src_img}\n\n"
            f"请在下方选择候选值或手动输入，然后点「确认此字段」。")
        self._show_source_crop(f, block)

    def _show_source_crop(self, field, block=None) -> None:
        """显示字段 bbox 对应的原图局部，不要求用户手工翻找整页。"""
        source = field.source_image or (block.meta.get("source_image") if block else None)
        if not source or not Path(source).exists():
            self.crop_preview.setPixmap(QPixmap())
            self.crop_preview.setText("该字段没有可用的来源图片。")
            return
        pixmap = QPixmap(str(source))
        if pixmap.isNull():
            self.crop_preview.setPixmap(QPixmap())
            self.crop_preview.setText("来源图片无法加载。")
            return
        bbox = field.bbox
        if bbox and None not in (bbox.x0, bbox.y0, bbox.x1, bbox.y1):
            margin = 24
            left = max(0, int(bbox.x0) - margin)
            top = max(0, int(bbox.y0) - margin)
            right = min(pixmap.width(), int(bbox.x1) + margin)
            bottom = min(pixmap.height(), int(bbox.y1) + margin)
            pixmap = pixmap.copy(QRect(left, top, max(1, right - left), max(1, bottom - top)))
        self.crop_preview.setText("")
        self.crop_preview.setPixmap(pixmap.scaled(700, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    @staticmethod
    def _fmt_bbox(bbox) -> str:
        if bbox is None or bbox.x0 is None:
            return "未知"
        return f"({bbox.x0:.1f},{bbox.y0:.1f})-({bbox.x1:.1f},{bbox.y1:.1f})"

    def _on_confirm(self):
        """真实用户确认：必须给出来源 input（候选值/手动），禁止占位与自动采用。"""
        if not hasattr(self, "_confirm_cache"):
            QMessageBox.information(self, "提示", "请先刷新待确认清单")
            return
        task_id = self._confirm_cache["task_id"]
        fid = self._confirm_cache.get("index", {}).get(self.confirm_list.currentRow())
        f = self._get_field(fid) if fid else None
        if f is None:
            QMessageBox.information(self, "提示", "请选择一个待确认项")
            return

        # 选项：各候选 + 手动输入
        choices = [f"候选{i+1}: {c.text}" for i, c in enumerate(f.candidates)] + ["手动输入…"]
        chosen, ok = QInputDialog.getItem(self, "选择最终值", f"字段「{f.key}」采用哪个值？", choices, 0, False)
        if not ok:
            return
        if chosen.startswith("手动输入"):
            value, ok2 = QInputDialog.getText(self, "手动确认", f"为「{f.key}」输入确认值：")
            if not ok2 or not value.strip():
                QMessageBox.warning(self, "未确认", "未输入值，已取消确认。")
                return
            source = "manual"
        else:
            idx = int(chosen[len("候选"):chosen.index(":")]) - 1
            value = f.candidates[idx].text
            source = f"candidate_{['a','b'][idx]}" if idx < 2 else "manual"
        try:
            self.pipeline.confirm_field(task_id, fid, value, source)
        except ValueError as e:
            QMessageBox.warning(self, "确认被拒绝", str(e))
            self._on_refresh_confirm()
            return
        self.status_label.setText(f"已确认字段 {f.key}")
        self._on_refresh_confirm()

    def _on_batch_confirm(self):
        """在单个表单中完成多个 C/D 字段确认，避免用户在长文档中来回操作。"""
        if not hasattr(self, "_confirm_cache"):
            QMessageBox.information(self, "提示", "请先刷新待确认清单")
            return
        fields = self._confirm_cache["doc"].pending_fields()
        if not fields:
            QMessageBox.information(self, "提示", "当前没有待确认字段")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("批量确认待确认字段")
        dialog.resize(760, min(720, 120 + len(fields) * 85))
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("每项必须明确选择候选值，或填写“手动值”。留空的字段不会被确认。"))
        form = QFormLayout()
        inputs = []
        for field in fields:
            combo = QComboBox()
            combo.addItem("暂不确认", (None, None))
            for index, candidate in enumerate(field.candidates):
                source = f"candidate_{['a', 'b'][index]}" if index < 2 else "manual"
                combo.addItem(f"候选{index + 1}: {candidate.text}", (candidate.text, source))
            manual = QLineEdit()
            manual.setPlaceholderText("手动输入会覆盖候选选择")
            box = QWidget()
            box_lay = QVBoxLayout(box)
            box_lay.setContentsMargins(0, 0, 0, 0)
            box_lay.addWidget(combo)
            box_lay.addWidget(manual)
            form.addRow(f"第{field.page or '?'}页 · {field.key} [{field.grade.value}]", box)
            inputs.append((field, combo, manual))
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        task_id = self._confirm_cache["task_id"]
        confirmed = 0
        for field, combo, manual in inputs:
            if manual.text().strip():
                value, source = manual.text().strip(), "manual"
            else:
                value, source = combo.currentData()
            if not value:
                continue
            self.pipeline.confirm_field(task_id, field.id, value, source)
            confirmed += 1
        self.status_label.setText(f"已批量确认 {confirmed} 项；其余字段仍保持待确认。")
        self._on_refresh_confirm()

    def _on_ask_ai(self):
        task_id = self._selected_task_id()
        if not task_id:
            QMessageBox.information(self, "提示", "请先选中一个任务")
            return
        review_path = self.pipeline.review_path(task_id)
        if not Path(review_path).exists():
            QMessageBox.information(self, "提示", "该任务还没有 review_required.md，请先处理")
            return
        asker = AiAsker(db=self.pipeline.db)
        mode = "auto"
        try:
            if not asker.configured:
                mode = "manual"  # 未配置 AI → 用本地清单兜底（仍需用户确认，不可绕过）
            result = self.pipeline.ai_question(task_id, asker, mode=mode)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "AI 提问失败", f"{e}")
            return
        self.confirm_detail.setPlainText(result["questions"])
        self.status_label.setText(f"AI 已汇总提问（引擎:{result['engine']}），任务标记为已提问。")
        self._refresh_tasks()

    def _on_final(self):
        task_id = self._selected_task_id()
        if not task_id:
            QMessageBox.information(self, "提示", "请先选中一个任务")
            return
        try:
            path = self.pipeline.finalize(task_id)
            QMessageBox.information(self, "完成", f"已生成最终文件:\n{path}")
        except RuntimeError as e:
            QMessageBox.warning(self, "无法生成（门控拦截）", str(e))
            self._on_refresh_confirm()

    # ---------- AI 设置页 ----------
    def _build_ai_tab(self) -> QWidget:
        from ..core.secrets import SecretStore
        self._secret_store = SecretStore()
        w = QWidget()
        form = QFormLayout(w)
        self.ai_base = QLineEdit()
        self.ai_model = QLineEdit()
        self.ai_key = QLineEdit()
        self.ai_key.setEchoMode(QLineEdit.Password)
        self.ai_upload = QCheckBox("允许上传原图")
        self.ai_insecure = QCheckBox("允许关闭 TLS 校验（仅自签测试环境，默认关闭）")
        self.ai_ctx = QSpinBox()
        self.ai_ctx.setRange(1000, 512000)
        self.ai_ctx.setValue(8000)
        self.ai_secret_label = QLabel("")

        form.addRow("API 地址", self.ai_base)
        form.addRow("模型名称", self.ai_model)
        form.addRow("API 密钥（加密存储）", self.ai_key)
        form.addRow("", self.ai_upload)
        form.addRow("", self.ai_insecure)
        form.addRow("最大上下文", self.ai_ctx)
        form.addRow("密钥后端", self.ai_secret_label)

        btns = QHBoxLayout()
        b_load = QPushButton("读取")
        b_load.clicked.connect(self._load_ai_config)
        b_save = QPushButton("保存")
        b_save.clicked.connect(self._save_ai_config)
        btns.addWidget(b_load)
        btns.addWidget(b_save)
        btns.addStretch(1)
        form.addRow(btns)
        self._load_ai_config()
        return w

    def _load_ai_config(self):
        db = self.pipeline.db
        self.ai_base.setText(db.get_config("ai_config", "base_url") or "")
        self.ai_model.setText(db.get_config("ai_config", "model") or "")
        # 密钥从安全存储读取，绝不从 SQLite 明文读
        self.ai_key.setText(self._secret_store.get("ai", "api_key") or "")
        self.ai_upload.setChecked((db.get_config("ai_config", "allow_upload_image") or "0") == "1")
        self.ai_ctx.setValue(int(db.get_config("ai_config", "max_context") or 8000))
        self.ai_insecure.setChecked((db.get_config("ai_config", "allow_insecure_tls") or "0") == "1")
        self.ai_secret_label.setText(
            f"后端: {self._secret_store.backend}（{'可用' if self._secret_store.available() else '不可用'}）")

    def _save_ai_config(self):
        db = self.pipeline.db
        db.set_config("ai_config", "base_url", self.ai_base.text().strip())
        db.set_config("ai_config", "model", self.ai_model.text().strip())
        # 密钥仅写入 SecretStore（keyring 或加密文件），SQLite 不存明文
        key_val = self.ai_key.text().strip()
        if key_val:
            self._secret_store.set("ai", "api_key", key_val)
        db.set_config("ai_config", "allow_upload_image", "1" if self.ai_upload.isChecked() else "0")
        db.set_config("ai_config", "allow_insecure_tls", "1" if self.ai_insecure.isChecked() else "0")
        db.set_config("ai_config", "max_context", str(self.ai_ctx.value()))
        self.status_label.setText("AI 配置已保存（密钥已加密存储）")

    # ---------- 模型与环境 ----------
    def _build_models_tab(self) -> QWidget:
        self._model_manager = ModelManager()
        w = QWidget()
        lay = QVBoxLayout(w)
        self.model_gpu_label = QLabel()
        self.model_status = QPlainTextEdit()
        self.model_status.setReadOnly(True)
        row = QHBoxLayout()
        refresh = QPushButton("刷新环境")
        refresh.clicked.connect(self._refresh_models)
        install_paddle = QPushButton("安装 PaddleOCR-VL")
        install_paddle.clicked.connect(lambda: self._install_model("paddle_ocr"))
        install_mineru = QPushButton("安装 MinerU")
        install_mineru.clicked.connect(lambda: self._install_model("mineru"))
        row.addWidget(refresh)
        row.addWidget(install_paddle)
        row.addWidget(install_mineru)
        row.addStretch(1)
        lay.addWidget(QLabel("双引擎是开始处理的前提。OCR 环境按需安装到用户数据目录，不随 EXE 打包。"))
        lay.addWidget(self.model_gpu_label)
        lay.addWidget(self.model_status)
        lay.addLayout(row)
        self._refresh_models()
        return w

    def _refresh_models(self):
        ai_ready = bool((self.pipeline.db.get_config("ai_config", "base_url") or "") and
                        (self.pipeline.db.get_config("ai_config", "model") or ""))
        statuses = self._model_manager.all_status(ai_ready)
        self.model_gpu_label.setText("GPU：" + self._model_manager.gpu_summary())
        lines = [f"{s.title}: {'可用' if s.available else '不可用'} — {s.detail}" for s in statuses]
        lines.append("\n开始处理前必须具备主识别与 MinerU 独立复核；任一不可用时不会创建处理任务或输出文件。")
        self.model_status.setPlainText("\n".join(lines))

    def _install_model(self, key: str):
        title = self._model_manager.SPECS[key][0]
        answer = QMessageBox.question(
            self, "安装模型依赖", f"将安装 {title} 的 Python 依赖。模型权重会在首次使用时下载，是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.model_status.setPlainText(f"正在安装 {title}，请稍候…")
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        result = self._model_manager.install(key)
        if result.returncode == 0:
            QMessageBox.information(self, "安装完成", f"{title} 依赖已安装。首次识别会下载模型权重。")
        else:
            QMessageBox.warning(self, "安装失败", (result.stderr or result.stdout or "未知错误")[-3000:])
        self._refresh_models()

    def closeEvent(self, event):
        if not self._quitting and self.tray.isVisible():
            event.ignore()
            self.hide()
            self.tray.showMessage("DocMD 仍在运行", "程序已最小化到系统托盘；右键托盘图标可退出。")
            return
        self.tray.hide()
        if self._owns_pipeline:
            self.pipeline.close()
        super().closeEvent(event)
