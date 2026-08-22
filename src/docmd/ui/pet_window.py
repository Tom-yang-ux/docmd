"""只有小熊的轻量桌宠入口。"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import QLabel, QMainWindow, QMenu, QMessageBox, QSystemTrayIcon, QVBoxLayout, QWidget

from ..pipeline import Pipeline
from .main_window import MainWindow, ProcessWorker
from .pet_assets import pet_asset_path


class PetWindow(QMainWindow):
    """始终置顶、可拖放的 DocMD 小熊窗口。"""

    SUPPORTED_SUFFIXES = MainWindow.SUPPORTED_SUFFIXES
    LOGICAL_BEAR_SIZE = 180

    def __init__(self, data_dir: str):
        super().__init__()
        self.data_dir = data_dir
        self.pipeline = Pipeline(data_dir, vision_engine="paddle_ocr")
        self.drag_origin: QPoint | None = None
        self._last_tap_at = 0.0
        self.control: MainWindow | None = None
        self.frames = self._load_high_resolution_frames(pet_asset_path())
        self._build_ui()
        self._build_tray()

    @staticmethod
    def _load_high_resolution_frames(sprite_path: Path) -> list[QPixmap]:
        """切分 3×3 高分辨率精灵图，并把绿色底色变为透明。"""
        image = QImage(str(sprite_path)).convertToFormat(QImage.Format_RGBA8888)
        if image.isNull():
            raise RuntimeError(f"无法加载小熊素材：{sprite_path}")
        cell_w, cell_h = image.width() // 3, image.height() // 3
        frames: list[QPixmap] = []
        for row in range(3):
            for col in range(3):
                frame = image.copy(col * cell_w, row * cell_h, cell_w, cell_h)
                for y in range(frame.height()):
                    for x in range(frame.width()):
                        color = QColor.fromRgba(frame.pixel(x, y))
                        # 仅剔除明显绿幕，保留小熊和桌面边缘的细节。
                        if color.green() > 150 and color.green() > color.red() * 1.55 and color.green() > color.blue() * 1.55:
                            color.setAlpha(0)
                            frame.setPixelColor(x, y, color)
                frames.append(QPixmap.fromImage(frame))
        return frames

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.windowIcon() or QIcon())
        menu = QMenu(self)
        show_pet = QAction("显示小熊", self)
        show_pet.triggered.connect(self._show_pet)
        settings = QAction("打开管理面板", self)
        settings.triggered.connect(self._open_control_panel)
        quit_action = QAction("退出 DocMD", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(show_pet)
        menu.addAction(settings)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self._show_pet() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def _build_ui(self) -> None:
        self.setWindowTitle("DocMD 小熊")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.setFixedSize(self.LOGICAL_BEAR_SIZE, 212)
        self.bear = QLabel(alignment=Qt.AlignCenter)
        self.bear.setFixedSize(self.LOGICAL_BEAR_SIZE, self.LOGICAL_BEAR_SIZE)
        self.bear.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hint = QLabel("把文件拖到小熊身上吧", alignment=Qt.AlignCenter)
        self.hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hint.setStyleSheet("color:#735b46; background:rgba(255,250,241,230); border:1px solid #eadcc7; border-radius:9px; padding:4px; font-size:10px;")
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.bear)
        layout.addWidget(self.hint)
        holder = QWidget()
        holder.setAttribute(Qt.WA_TransparentForMouseEvents)
        holder.setLayout(layout)
        self.setCentralWidget(holder)
        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self._advance_frame)
        self._set_animation("idle")

    def _set_animation(self, name: str) -> None:
        profiles = {
            "idle": ([0], 1500), "inspect": ([2], 1200),
            "typing": ([0, 1, 2], 420), "happy": ([4, 5], 380),
            "sad": ([6, 7, 8], 650),
        }
        self.frame_indices, delay = profiles[name]
        self.frame_index = 0
        self.frame_timer.start(delay)
        self._advance_frame()

    def _advance_frame(self) -> None:
        index = self.frame_indices[self.frame_index % len(self.frame_indices)]
        pixmap = self.frames[index]
        dpr = self.devicePixelRatioF()
        scaled = pixmap.scaled(int(self.LOGICAL_BEAR_SIZE * dpr), int(self.LOGICAL_BEAR_SIZE * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        self.bear.setPixmap(scaled)
        self.frame_index += 1

    def _show_pet(self) -> None:
        self.showNormal(); self.activateWindow(); self.raise_()

    def dragEnterEvent(self, event):  # noqa: N802
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if any(url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in self.SUPPORTED_SUFFIXES for url in urls):
            event.acceptProposedAction(); self.hint.setText("松手吧，我来认真转换！")
        else:
            event.ignore()

    def dragLeaveEvent(self, event):  # noqa: N802
        self.hint.setText("把文件拖到小熊身上吧")

    def dropEvent(self, event):  # noqa: N802
        files = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in self.SUPPORTED_SUFFIXES]
        if files:
            self._start_processing(files); event.acceptProposedAction()

    def _start_processing(self, files: list[str]) -> None:
        try:
            for path in files:
                self.pipeline.ensure_dual_engine_ready(self.pipeline.importer.detect_type(path), path)
            task_id = self.pipeline.import_files(files)[-1]["task_id"]
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc)); return
        self._set_animation("inspect"); self.hint.setText("小熊正在看文件…")
        QTimer.singleShot(1200, lambda: (self._set_animation("typing"), self.hint.setText("小熊正在努力处理…")))
        self.worker = ProcessWorker(self.pipeline, task_id)
        self.worker.finished_task.connect(self._processing_finished)
        self.worker.start()

    def _processing_finished(self, task_id, doc, error) -> None:
        if error:
            self._show_error(error); return
        self.hint.setText("发现 C/D 项，请打开管理面板确认")
        self._set_animation("happy")
        QTimer.singleShot(4000, lambda: self._set_animation("idle"))

    def _show_error(self, message: str) -> None:
        self._set_animation("sad"); self.hint.setText("呜…这份文件没有读成功")
        QMessageBox.warning(self, "DocMD", message)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self.drag_origin and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_origin)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() != Qt.LeftButton or self.drag_origin is None:
            return
        moved = (event.globalPosition().toPoint() - self.frameGeometry().topLeft() - self.drag_origin).manhattanLength()
        self.drag_origin = None
        if moved <= 8:
            now = time.monotonic()
            if now - self._last_tap_at <= 0.5:
                self._last_tap_at = 0.0
                self._open_control_panel()
                return
            self._last_tap_at = now
            self._set_animation("happy"); self.hint.setText("小熊很开心")
            QTimer.singleShot(1600, lambda: self._set_animation("idle"))

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._open_control_panel()

    def _open_control_panel(self) -> None:
        if self.control is None:
            self.control = MainWindow(self.data_dir, pipeline=self.pipeline)
        self.control.showNormal(); self.control.activateWindow(); self.control.raise_()

    def closeEvent(self, event):  # noqa: N802
        self.tray.hide(); self.pipeline.close(); event.accept()
