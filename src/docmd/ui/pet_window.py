"""DocMD 桌宠：文件拖到小熊即可转换，双击打开管理面板。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QPropertyAnimation, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QIcon, QMovie, QPixmap
from PySide6.QtWidgets import QLabel, QMainWindow, QMenu, QMessageBox, QSystemTrayIcon, QVBoxLayout, QWidget

from ..pipeline import Pipeline
from .main_window import MainWindow, ProcessWorker
from .pet_assets import ensure_pet_assets


class GirlPet(QLabel):
    """十次抚摸解锁的第二个可拖动桌宠。"""
    moved = Signal()

    def __init__(self, image_path: Path):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(132, 132)
        self.movie = QMovie(str(image_path))
        self.movie.setScaledSize(self.size())
        self.setMovie(self.movie)
        self.movie.start()
        self.movie.setPaused(True)
        self.frames = [0, 1, 2, 3, 4, 5, 6, 7, 8]
        self.frame_index = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._advance)
        self.timer.start(700)
        self.drag_origin: QPoint | None = None

    def _advance(self) -> None:
        self.movie.jumpToFrame(self.frames[self.frame_index % len(self.frames)])
        self.frame_index += 1

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self.drag_origin and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_origin)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self.drag_origin:
            moved = (event.globalPosition().toPoint() - self.frameGeometry().topLeft() - self.drag_origin).manhattanLength()
            self.drag_origin = None
            if moved > 8:
                self.moved.emit()
            else:
                self.frames, self.frame_index = [4, 5, 6], 0  # 抚摸：开心/惊讶
                QTimer.singleShot(2600, lambda: setattr(self, "frames", list(range(9))))

    def contextMenuEvent(self, event):  # noqa: N802
        self.close()
        event.accept()


class FinalEasterScene(QMainWindow):
    """完整童话插画彩蛋：固定画面，不叠加角色、星星或任何动画。"""
    def __init__(self, scene_path: Path):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.scene_path = scene_path
        self.setCursor(Qt.ArrowCursor)
        base = QWidget(self)
        self.setCentralWidget(base)
        self.background = QLabel(base)
        self.background.setPixmap(QPixmap(str(scene_path)))
        self.background.lower()
        self.tip = QLabel("星光已落在她手心 · 右键离开", base)
        self.tip.setStyleSheet("color:#fff1c7; font-size:12px; background:rgba(13,31,75,150); border-radius:10px; padding:5px 9px;")
        self.tip.move(18, 16)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "background"):
            self.background.setGeometry(self.rect())
            self.background.setPixmap(QPixmap(str(self.scene_path)).scaled(self.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation))

    def contextMenuEvent(self, event):  # noqa: N802
        self.close(); event.accept()


class PetWindow(QMainWindow):
    """始终置顶、可拖放的 DocMD 小熊窗口。"""
    SUPPORTED_SUFFIXES = MainWindow.SUPPORTED_SUFFIXES

    def __init__(self, data_dir: str):
        super().__init__()
        self.data_dir = data_dir
        self.pipeline = Pipeline(data_dir, vision_engine="paddle_ocr")
        self.assets = ensure_pet_assets(data_dir)
        self.pat_count = 0
        self.drag_origin: QPoint | None = None
        self.girl: GirlPet | None = None
        self.control: MainWindow | None = None
        self._build_ui()
        self._build_tray()

    def _build_tray(self) -> None:
        """设置与退出只放在 Windows 托盘右键菜单，桌宠左键只负责互动。"""
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.windowIcon() or QIcon())
        menu = QMenu(self)
        settings = QAction("打开设置", self)
        settings.triggered.connect(self._open_control_panel)
        show_pet = QAction("显示小熊", self)
        show_pet.triggered.connect(self._show_pet)
        quit_action = QAction("退出 DocMD", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(show_pet)
        menu.addAction(settings)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self._show_pet()
                                    if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def _show_pet(self) -> None:
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _build_ui(self) -> None:
        self.setWindowTitle("DocMD 小熊")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.setFixedSize(180, 212)
        self.bear = QLabel()
        self.bear.setFixedSize(180, 180)
        self.bear.setAlignment(Qt.AlignCenter)
        self.movie = QMovie(str(self.assets["bear"]))
        self.movie.setScaledSize(self.bear.size())
        self.bear.setMovie(self.movie)
        self.movie.start()
        self.movie.setPaused(True)
        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self._advance_bear_frame)
        self.bear.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hint = QLabel("把文件拖到小熊身上吧")
        self.hint.setAlignment(Qt.AlignCenter)
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
        self._set_animation("idle")

    def _set_animation(self, name: str) -> None:
        """逐帧状态机：待机、看文件、打字、眯眼开心、持续哭泣。"""
        profiles = {
            "idle": ([0], 1500, True), "inspect": ([2], 1200, False),
            "typing": ([0, 1, 2], 420, True), "happy": ([4, 5], 380, True),
            "sad": ([6, 7, 8], 650, True),
        }
        self.bear_frames, self.bear_delay, self.bear_loop = profiles[name]
        self.bear_frame_index = 0
        self.frame_timer.start(self.bear_delay)
        self._advance_bear_frame()

    def _advance_bear_frame(self) -> None:
        if not self.bear_frames:
            return
        self.movie.jumpToFrame(self.bear_frames[self.bear_frame_index])
        if self.bear_loop:
            self.bear_frame_index = (self.bear_frame_index + 1) % len(self.bear_frames)

    def dragEnterEvent(self, event):  # noqa: N802
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if any(url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in self.SUPPORTED_SUFFIXES for url in urls):
            event.acceptProposedAction()
            self.hint.setText("松手吧，我来认真转换！")
        else:
            event.ignore()

    def dragLeaveEvent(self, event):  # noqa: N802
        self.hint.setText("把文件拖到小熊身上吧")

    def dropEvent(self, event):  # noqa: N802
        files = [url.toLocalFile() for url in event.mimeData().urls()
                 if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in self.SUPPORTED_SUFFIXES]
        if files:
            self._start_processing(files)
            event.acceptProposedAction()

    def _start_processing(self, files: list[str]) -> None:
        try:
            task_id = self.pipeline.import_files(files)[-1]["task_id"]
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
            return
        self._set_animation("inspect")
        self.hint.setText("小熊正在看文件…")
        QTimer.singleShot(1200, lambda: (self._set_animation("typing"), self.hint.setText("小熊正在努力打字…")))
        self.worker = ProcessWorker(self.pipeline, task_id)
        self.worker.finished_task.connect(self._processing_finished)
        self.worker.start()

    def _processing_finished(self, task_id, doc, error) -> None:
        if error:
            self._show_error(error)
            return
        self.hint.setText("发现 C/D 项，双击我去确认吧" if doc.requires_confirmation() else "完成！最终 Markdown 已生成")
        self._set_animation("happy")
        QTimer.singleShot(4000, lambda: self._set_animation("idle"))

    def _show_error(self, message: str) -> None:
        self._set_animation("sad")
        self.hint.setText("呜…这份文件没有读成功")
        QMessageBox.warning(self, "DocMD", message)
        # 哭泣不会自动结束，必须由下一次抚摸安慰。

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self.drag_origin and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_origin)
            self._check_final_easter_egg()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() != Qt.LeftButton or self.drag_origin is None:
            return
        moved = (event.globalPosition().toPoint() - self.frameGeometry().topLeft() - self.drag_origin).manhattanLength()
        self.drag_origin = None
        if moved > 8:
            self._check_final_easter_egg()
        elif self._cookie_hit(event.position().toPoint()):
            self._drop_cookie()
        else:
            self._pat()

    def contextMenuEvent(self, event):  # noqa: N802
        """桌宠本体不提供设置入口；请在系统托盘右键菜单操作。"""
        event.ignore()

    @staticmethod
    def _cookie_hit(pos: QPoint) -> bool:
        return 48 <= pos.x() <= 132 and 108 <= pos.y() <= 172

    def _drop_cookie(self) -> None:
        cookie = QLabel("🍪")
        cookie.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        cookie.setAttribute(Qt.WA_TranslucentBackground)
        cookie.setStyleSheet("font-size:32px;")
        cookie.resize(48, 48)
        cookie.move(self.x() + 82, self.y() + 120)
        cookie.show()
        fall = QPropertyAnimation(cookie, b"pos", cookie)
        fall.setDuration(650)
        fall.setStartValue(cookie.pos())
        fall.setEndValue(cookie.pos() + QPoint(10, 170))
        fall.finished.connect(cookie.deleteLater)
        self.fall_animation = fall  # 保留 Python 引用，直到动画结束。
        fall.start()
        self._set_animation("sad")
        self.hint.setText("饼干掉了…小熊好难过")
        # 哭泣是一个持久状态；用户再次抚摸后才会恢复。

    def _pat(self) -> None:
        self.pat_count += 1
        self._set_animation("happy")
        if self.pat_count >= 10 and self.girl is None:
            self._spawn_girl()
            self.hint.setText("彩蛋！小女孩来陪小熊读书了")
        else:
            self.hint.setText(f"小熊很开心（{min(self.pat_count, 10)}/10）")
        QTimer.singleShot(1600, lambda: self._set_animation("idle"))

    def _spawn_girl(self) -> None:
        self.girl = GirlPet(self.assets["girl"])
        self.girl.move(self.x() + self.width() + 28, self.y() + 34)
        self.girl.moved.connect(self._check_final_easter_egg)
        self.girl.show()
        # 女孩先安静读书，十秒后主动走向并轻摸小熊。
        QTimer.singleShot(10000, self._girl_visits_bear)

    def _girl_visits_bear(self) -> None:
        if not self.girl or not self.girl.isVisible():
            return
        self.girl.timer.setInterval(420)  # 靠近前更活泼的读书动画
        target = QPoint(self.x() + 72, self.y() + 40)
        walk = QPropertyAnimation(self.girl, b"pos", self.girl)
        walk.setDuration(max(900, min(3500, self.girl.pos().manhattanLength() // 2)))
        walk.setStartValue(self.girl.pos()); walk.setEndValue(target)
        walk.finished.connect(self._check_final_easter_egg)
        self.girl_walk = walk
        walk.start()

    def _check_final_easter_egg(self) -> None:
        if self.girl and self.frameGeometry().intersects(self.girl.frameGeometry()):
            self.girl.close()
            self.girl = None
            self.hint.setText("终极彩蛋解锁！")
            self._show_final_easter_egg()

    def _show_final_easter_egg(self) -> None:
        self.final_egg = FinalEasterScene(self.assets["fairytale_final_scene"])
        self.final_egg.setFixedSize(760, 500)
        self.final_egg.move(max(30, self.x() - 280), max(30, self.y() - 160))
        self.final_egg.show()

    def _open_control_panel(self) -> None:
        if self.control is None:
            self.control = MainWindow(self.data_dir, pipeline=self.pipeline)
        self.control.showNormal()
        self.control.activateWindow()
        self.control.raise_()

    def closeEvent(self, event):  # noqa: N802
        if self.girl:
            self.girl.close()
        self.tray.hide()
        self.pipeline.close()
        event.accept()
