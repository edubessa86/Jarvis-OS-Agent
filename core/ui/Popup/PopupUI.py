import math
import os
import re
import json
import time

os.environ["QT_LOGGING_RULES"] = "qt.qpa.window=false;qt.text.font.db=false;default.warning=false"

from PyQt5.QtCore import (
    Qt, QTimer, QPropertyAnimation, QPoint, QPointF,
    QParallelAnimationGroup, QEasingCurve, QFileSystemWatcher,
    QRect, QRectF, QSize, qInstallMessageHandler
)
from PyQt5.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QFrame, QPushButton,
    QHBoxLayout, QApplication, QSizePolicy
)
from PyQt5.QtGui import (
    QFont, QColor, QFontDatabase, QPainter, QPainterPath,
    QLinearGradient, QRadialGradient, QPen
)

from AsyncBrowser import AsyncTextBrowser
from TextParser import ParserWorker

TYPING_STATUS_FILE = "Data/typing_status.json"


def _popup_qt_message_handler(mode, context, message):
    suppressed = (
        "OpenType support missing",
        "QColor",
        "Unable to set geometry",
        "Resulting geometry",
        "QWindowsWindow::setGeometry",
    )
    if any(s in message for s in suppressed):
        return


qInstallMessageHandler(_popup_qt_message_handler)


class StatusOrb(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.rgb = [191.0, 90.0, 242.0]
        self.phase = 0.0
        self.mode = "typing"

    def set_state(self, rgb, phase, mode):
        self.rgb = rgb
        self.phase = phase
        self.mode = mode
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        r, g, b = [max(0, min(255, int(c))) for c in self.rgb]
        pulse = (math.sin(self.phase * 2.2) + 1.0) * 0.5
        center = QPointF(12.0, 12.0)

        halo_radius = 8.0 + (pulse * 3.4)
        halo = QRadialGradient(center, halo_radius)
        halo.setColorAt(0.0, QColor(r, g, b, int(120 + pulse * 70)))
        halo.setColorAt(0.65, QColor(r, g, b, int(38 + pulse * 25)))
        halo.setColorAt(1.0, QColor(r, g, b, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(center, halo_radius, halo_radius)

        if self.mode == "completed":
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(50, 215, 75, 235))
            painter.drawEllipse(center, 6.6, 6.6)
            check_pen = QPen(QColor(255, 255, 255, 255), 1.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(check_pen)
            path = QPainterPath()
            path.moveTo(8.8, 12.2)
            path.lineTo(11.1, 14.5)
            path.lineTo(15.4, 9.6)
            painter.drawPath(path)
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(r, g, b, 245))
            core_r = 3.4 + pulse * 0.9
            painter.drawEllipse(center, core_r, core_r)

            orbit_pen = QPen(QColor(255, 255, 255, int(135 + pulse * 90)), 1.3)
            painter.setPen(orbit_pen)
            painter.setBrush(Qt.NoBrush)
            angle_deg = int((self.phase * 180.0 / math.pi) * 16)
            rect = QRectF(5.0, 5.0, 14.0, 14.0)
            painter.drawArc(rect, angle_deg, 195 * 16)


class IslandContainer(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rgb = [191.0, 90.0, 242.0]
        self.phase = 0.0
        self.corner_radius = 26.0
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(0, 0)

    def minimumSizeHint(self):
        return QSize(0, 0)

    def update_visuals(self, rgb, phase):
        self.rgb = rgb
        self.phase = phase
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = QRectF(self.rect())
        r, g, b = [max(0, min(255, int(c))) for c in self.rgb]

        shadow_steps = [
            (1.0, 4.0, 28.0, QColor(0, 0, 0, 18)),
            (2.5, 4.0, 27.5, QColor(0, 0, 0, 34)),
            (4.5, 3.0, 27.0, QColor(0, 0, 0, 52)),
            (6.5, 2.0, 26.5, QColor(r, g, b, 20)),
            (8.0, 1.0, 26.0, QColor(0, 0, 0, 82)),
        ]
        painter.setPen(Qt.NoPen)
        for inset, y_offset, rad, col in shadow_steps:
            s_rect = rect.adjusted(inset, inset + y_offset * 0.4, -inset, -inset + y_offset * 0.6)
            painter.setBrush(col)
            painter.drawRoundedRect(s_rect, rad, rad)

        island_rect = rect.adjusted(9.0, 7.0, -9.0, -11.0)
        radius = self.corner_radius

        bg_grad = QLinearGradient(island_rect.topLeft(), island_rect.bottomLeft())
        bg_grad.setColorAt(0.0, QColor(28, 29, 36, 248))
        bg_grad.setColorAt(0.45, QColor(17, 18, 23, 252))
        bg_grad.setColorAt(1.0, QColor(10, 10, 14, 254))

        painter.setBrush(bg_grad)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(island_rect, radius, radius)

        ambient_glow = QRadialGradient(
            QPointF(island_rect.left() + island_rect.width() * 0.2, island_rect.top()),
            island_rect.width() * 0.55
        )
        ambient_glow.setColorAt(0.0, QColor(r, g, b, 26))
        ambient_glow.setColorAt(1.0, QColor(r, g, b, 0))
        painter.setBrush(ambient_glow)
        painter.drawRoundedRect(island_rect, radius, radius)

        cx = island_rect.center().x()
        cy = island_rect.center().y()
        span = max(island_rect.width(), island_rect.height()) * 0.65
        dx = math.cos(self.phase) * span
        dy = math.sin(self.phase) * span

        border_grad = QLinearGradient(QPointF(cx - dx, cy - dy), QPointF(cx + dx, cy + dy))
        border_grad.setColorAt(0.0, QColor(255, 255, 255, 95))
        border_grad.setColorAt(0.35, QColor(255, 255, 255, 22))
        border_grad.setColorAt(0.72, QColor(r, g, b, 175))
        border_grad.setColorAt(1.0, QColor(255, 255, 255, 55))

        border_pen = QPen(border_grad, 1.25)
        painter.setPen(border_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(island_rect.adjusted(0.6, 0.6, -0.6, -0.6), radius, radius)

        shine_rect = island_rect.adjusted(16.0, 1.2, -16.0, -(island_rect.height() - 2.2))
        shine_grad = QLinearGradient(shine_rect.topLeft(), shine_rect.topRight())
        shine_grad.setColorAt(0.0, QColor(255, 255, 255, 0))
        shine_grad.setColorAt(0.5, QColor(255, 255, 255, 62))
        shine_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(shine_grad)
        painter.drawRect(shine_rect)


class PremiumCopyButton(QPushButton):
    def __init__(self, font_family="Segoe UI", parent=None):
        super().__init__(parent)
        self.font_family = font_family
        self.setFixedSize(66, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.hover_alpha = 0.0
        self.target_alpha = 0.0
        self.copied_state = False

        self.hover_timer = QTimer(self)
        self.hover_timer.setInterval(16)
        self.hover_timer.timeout.connect(self.animate_hover)

        self.reset_timer = QTimer(self)
        self.reset_timer.setSingleShot(True)
        self.reset_timer.timeout.connect(self._reset_copied)

    def show_copied(self):
        self.copied_state = True
        self.setFixedSize(74, 26)
        self.update()
        self.reset_timer.start(1600)

    def _reset_copied(self):
        self.copied_state = False
        self.setFixedSize(66, 26)
        self.update()

    def enterEvent(self, event):
        self.target_alpha = 1.0
        if not self.hover_timer.isActive():
            self.hover_timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.target_alpha = 0.0
        if not self.hover_timer.isActive():
            self.hover_timer.start()
        super().leaveEvent(event)

    def animate_hover(self):
        diff = self.target_alpha - self.hover_alpha
        if abs(diff) < 0.04:
            self.hover_alpha = self.target_alpha
            self.hover_timer.stop()
        else:
            self.hover_alpha += diff * 0.25
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        if self.copied_state:
            bg_col = QColor(50, 215, 75, 45)
            border_col = QColor(50, 215, 75, 130)
            text_col = QColor(120, 245, 140, 255)
            label = "Copied"
        else:
            bg_alpha = int(14 + self.hover_alpha * 26)
            border_alpha = int(22 + self.hover_alpha * 45)
            bg_col = QColor(255, 255, 255, bg_alpha)
            border_col = QColor(255, 255, 255, border_alpha)
            text_col = QColor(255, 255, 255, int(175 + self.hover_alpha * 75))
            label = "Copy"

        painter.setBrush(bg_col)
        painter.setPen(QPen(border_col, 1.0))
        painter.drawRoundedRect(rect, 8.0, 8.0)

        font = QFont(self.font_family, 8, QFont.Bold)
        painter.setFont(font)
        painter.setPen(text_col)
        painter.drawText(self.rect(), Qt.AlignCenter, label)


class PremiumCloseButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.PointingHandCursor)
        self.hover_alpha = 0.0
        self.target_alpha = 0.0

        self.hover_timer = QTimer(self)
        self.hover_timer.setInterval(16)
        self.hover_timer.timeout.connect(self.animate_hover)

    def enterEvent(self, event):
        self.target_alpha = 255.0
        if not self.hover_timer.isActive():
            self.hover_timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.target_alpha = 0.0
        if not self.hover_timer.isActive():
            self.hover_timer.start()
        super().leaveEvent(event)

    def animate_hover(self):
        diff = self.target_alpha - self.hover_alpha
        if abs(diff) < 3.0:
            self.hover_alpha = self.target_alpha
            self.hover_timer.stop()
        else:
            self.hover_alpha += diff * 0.25
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 13.5, 13.5)

        if self.hover_alpha > 1.0:
            painter.fillPath(path, QColor(255, 69, 58, int(self.hover_alpha * 0.82)))
            painter.setPen(QPen(QColor(255, 105, 97, int(self.hover_alpha * 0.9)), 1.0))
            painter.drawPath(path)
        else:
            painter.fillPath(path, QColor(255, 255, 255, 12))
            painter.setPen(QPen(QColor(255, 255, 255, 22), 1.0))
            painter.drawPath(path)

        icon_alpha = 185 + int((self.hover_alpha / 255.0) * 70)
        pen = QPen(QColor(255, 255, 255, icon_alpha), 1.6, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(pen)
        cx, cy = rect.center().x(), rect.center().y()
        d = 4.2
        painter.drawLine(QPointF(cx - d, cy - d), QPointF(cx + d, cy + d))
        painter.drawLine(QPointF(cx + d, cy - d), QPointF(cx - d, cy + d))


class TypingPopup(QWidget):
    def __init__(self, fallback_text="", max_width=760):
        super().__init__()
        qInstallMessageHandler(_popup_qt_message_handler)

        self.max_width = max_width
        self.padding_h = 165
        self.target_height = self.padding_h
        self.current_height = float(self.padding_h)
        self.max_allowed_height = 820

        self.glow_phase = 0.0
        self.current_rgb = [191.0, 90.0, 242.0]
        self.target_rgb = [191.0, 90.0, 242.0]
        self._last_mtime = 0.0
        self._poll_tick_counter = 0

        self.master_timer = QTimer(self)
        self.master_timer.timeout.connect(self.master_tick)

        self.parse_debounce_timer = QTimer(self)
        self.parse_debounce_timer.setSingleShot(True)
        self.parse_debounce_timer.timeout.connect(self._execute_parse)

        self.eng_font_id = QFontDatabase.addApplicationFont("Data/fonts/english.ttf")
        eng_fams = QFontDatabase.applicationFontFamilies(self.eng_font_id) if self.eng_font_id != -1 else []
        self.eng_font = eng_fams[0] if eng_fams else "Segoe UI"

        self.hin_font_id = QFontDatabase.addApplicationFont("Data/fonts/devangri.ttf")
        hin_fams = QFontDatabase.applicationFontFamilies(self.hin_font_id) if self.hin_font_id != -1 else []
        self.hin_font = hin_fams[0] if hin_fams else "Nirmala UI"

        fallback_chain = [self.hin_font, "Nirmala UI", "Segoe UI", "Mangal"]
        try:
            QFont.insertSubstitutions(self.eng_font, fallback_chain)
            QFont.insertSubstitutions("MS Shell Dlg 2", fallback_chain)
            QFont.insertSubstitutions("Sansation", fallback_chain)
        except Exception:
            pass

        self.current_raw_text = ""
        self.clean_copy_text = ""
        self.pending_raw_text = ""
        self.status = "typing"
        self.idle_counter = 0
        self.worker = None
        self.is_fading_out = False
        self.custom_pos = None
        self.oldPos = None
        self.auto_scroll_enabled = True

        self.init_ui()
        self.start_animations()

        self.file_watcher = QFileSystemWatcher(self)
        if os.path.exists(TYPING_STATUS_FILE):
            self.file_watcher.addPath(TYPING_STATUS_FILE)
        self.file_watcher.fileChanged.connect(self.on_status_file_changed)

        self.master_timer.start(20)
        self.check_status_file()

    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        return QSize(self.max_width, int(self.current_height))

    def init_ui(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(0, 0)
        self.setStyleSheet("background: transparent; border: none; outline: none;")

        self.outer_layout = QVBoxLayout(self)
        self.outer_layout.setContentsMargins(16, 12, 16, 14)
        self.outer_layout.setSizeConstraint(QVBoxLayout.SetNoConstraint)

        self.container = IslandContainer(self)
        self.container.setObjectName("IslandWrapper")
        self.container.setMinimumSize(0, 0)

        self.wrapper_layout = QVBoxLayout(self.container)
        self.wrapper_layout.setContentsMargins(9, 7, 9, 11)
        self.wrapper_layout.setSpacing(0)
        self.wrapper_layout.setSizeConstraint(QVBoxLayout.SetNoConstraint)

        self.inner_island = QFrame(self.container)
        self.inner_island.setObjectName("Island")
        self.inner_island.setMinimumSize(0, 0)
        self.inner_island.setStyleSheet("background: transparent; border: none;")
        self.wrapper_layout.addWidget(self.inner_island)

        self.container_layout = QVBoxLayout(self.inner_island)
        self.container_layout.setContentsMargins(26, 18, 26, 20)
        self.container_layout.setSpacing(12)
        self.container_layout.setSizeConstraint(QVBoxLayout.SetNoConstraint)

        self.header_layout = QHBoxLayout()
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.setSpacing(10)

        self.status_orb = StatusOrb(self.inner_island)

        self.status_tag = QLabel("Jarvis Responding")
        font = QFont(self.eng_font, 10, QFont.Bold)
        font.setLetterSpacing(QFont.PercentageSpacing, 103)
        self.status_tag.setFont(font)
        self.status_tag.setStyleSheet("color: rgba(255, 255, 255, 0.96); background: transparent; border: none;")

        self.mood_pill = QLabel("")
        self.mood_pill.setFont(QFont(self.eng_font, 8, QFont.Medium))
        self.mood_pill.setStyleSheet("""
            QLabel {
                color: rgba(235, 235, 245, 0.82);
                background-color: rgba(255, 255, 255, 0.065);
                border: 1px solid rgba(255, 255, 255, 0.095);
                border-radius: 8px;
                padding: 2px 9px;
            }
        """)
        self.mood_pill.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.mood_pill.hide()

        self.copy_btn = PremiumCopyButton(self.eng_font, self)
        self.copy_btn.clicked.connect(self.copy_response_text)

        self.close_btn = PremiumCloseButton(self)
        self.close_btn.clicked.connect(self.fade_out)

        self.header_layout.addWidget(self.status_orb)
        self.header_layout.addWidget(self.status_tag)
        self.header_layout.addWidget(self.mood_pill)
        self.header_layout.addStretch()
        self.header_layout.addWidget(self.copy_btn)
        self.header_layout.addWidget(self.close_btn)
        self.container_layout.addLayout(self.header_layout)

        self.separator = QFrame()
        self.separator.setMinimumSize(0, 1)
        self.separator.setMaximumHeight(1)
        self.separator.setStyleSheet("""
            background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 rgba(255,255,255,0),
            stop:0.2 rgba(255,255,255,0.05),
            stop:0.5 rgba(255,255,255,0.14),
            stop:0.8 rgba(255,255,255,0.05),
            stop:1 rgba(255,255,255,0));
            border: none;
        """)
        self.container_layout.addWidget(self.separator)

        self.text_browser = AsyncTextBrowser(self)
        self.container_layout.addWidget(self.text_browser, 1)
        self.outer_layout.addWidget(self.container)

        self.setWindowOpacity(0.0)
        self.setCursor(Qt.ArrowCursor)

        screen = QApplication.primaryScreen().availableGeometry()
        self.max_allowed_height = int(screen.height() * 0.82)
        self.x_pos = (screen.width() - self.max_width) // 2
        self.start_y = screen.top() + 36

        self.setGeometry(self.x_pos, self.start_y, self.max_width, int(self.current_height))

    def copy_response_text(self):
        text_to_copy = self.clean_copy_text.strip() or self.text_browser.toPlainText().strip()
        if text_to_copy:
            QApplication.clipboard().setText(text_to_copy)
            self.copy_btn.show_copied()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.fade_out()
            event.accept()
            return
        super().keyPressEvent(event)

    def start_animations(self):
        self.is_fading_out = False
        if not self.master_timer.isActive():
            self.master_timer.start(20)

        target_x = self.custom_pos.x() if self.custom_pos is not None else self.x_pos
        target_y = self.custom_pos.y() if self.custom_pos is not None else self.start_y

        self.setGeometry(target_x, target_y - 30, self.max_width, int(self.current_height))
        self.show()
        self.raise_()

        self.anim_group = QParallelAnimationGroup(self)

        fade_in = QPropertyAnimation(self, b"windowOpacity")
        fade_in.setDuration(280)
        fade_in.setStartValue(self.windowOpacity())
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.OutCubic)

        slide_down = QPropertyAnimation(self, b"pos")
        slide_down.setDuration(360)
        slide_down.setStartValue(QPoint(target_x, target_y - 30))
        slide_down.setEndValue(QPoint(target_x, target_y))
        slide_down.setEasingCurve(QEasingCurve.OutQuint)

        self.anim_group.addAnimation(fade_in)
        self.anim_group.addAnimation(slide_down)
        self.anim_group.start()

    def on_status_file_changed(self, path):
        QTimer.singleShot(8, self.check_status_file)
        if os.path.exists(TYPING_STATUS_FILE) and TYPING_STATUS_FILE not in self.file_watcher.files():
            self.file_watcher.addPath(TYPING_STATUS_FILE)

    def _extract_emotion_and_clean(self, raw_text):
        match = re.match(r"^\s*\[([a-zA-Z_ -]{2,20})\]\s*", str(raw_text or ""))
        if match:
            mood = match.group(1).strip().capitalize()
            cleaned = raw_text[match.end():]
            return mood, cleaned
        return "", str(raw_text or "")

    def check_status_file(self):
        if not os.path.exists(TYPING_STATUS_FILE):
            return

        data = None
        for _ in range(3):
            try:
                with open(TYPING_STATUS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                break
            except Exception:
                time.sleep(0.004)

        if not data:
            return

        new_text = data.get("text", "")
        new_status = data.get("status", "idle")

        if new_status in ("typing", "completed"):
            self.idle_counter = 0
            if not self.isVisible() or self.is_fading_out or self.windowOpacity() < 0.1:
                self.start_animations()

        if new_text != self.current_raw_text:
            self.current_raw_text = new_text
            mood, cleaned = self._extract_emotion_and_clean(new_text)
            self.clean_copy_text = cleaned
            if mood:
                self.mood_pill.setText(mood)
                self.mood_pill.show()
            else:
                self.mood_pill.clear()
                self.mood_pill.hide()
            self.render_markdown_async(new_text)

        if new_status != self.status:
            self.status = new_status
            self.idle_counter = 0
            if self.status == "typing":
                self.status_tag.setText("Jarvis Responding")
                self.target_rgb = [191.0, 90.0, 242.0]
            elif self.status == "completed":
                self.status_tag.setText("Response Ready")
                self.target_rgb = [48.0, 209.0, 88.0]

    def render_markdown_async(self, raw_text):
        self.pending_raw_text = raw_text
        self.parse_debounce_timer.start(35)

    def _execute_parse(self):
        if not self.pending_raw_text:
            return
        if self.worker is not None and self.worker.isRunning():
            self.worker.is_cancelled = True
            self.parse_debounce_timer.start(20)
            return

        self.worker = ParserWorker(self.pending_raw_text, self.eng_font, self.hin_font)
        self.worker.finished_signal.connect(self.on_markdown_parsed)
        self.worker.start()

    def on_markdown_parsed(self, tokens, final_html):
        scrollbar = self.text_browser.verticalScrollBar()
        prev_val = scrollbar.value()
        prev_max = scrollbar.maximum()
        was_at_bottom = (prev_val >= prev_max - 18) or (self.status == "typing" and self.auto_scroll_enabled)

        self.text_browser.setUpdatesEnabled(False)
        self.text_browser.setHtml(final_html)
        self.update_layout_height()

        if was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())
            self.last_scroll_value = scrollbar.maximum()
        else:
            scrollbar.setValue(min(prev_val, scrollbar.maximum()))
            self.last_scroll_value = scrollbar.value()
        self.text_browser.setUpdatesEnabled(True)

    def master_tick(self):
        self._poll_tick_counter += 1
        if self._poll_tick_counter >= 8:
            self._poll_tick_counter = 0
            try:
                if os.path.exists(TYPING_STATUS_FILE):
                    mtime = os.path.getmtime(TYPING_STATUS_FILE)
                    if mtime != self._last_mtime:
                        self._last_mtime = mtime
                        self.check_status_file()
            except Exception:
                pass

        scrollbar = self.text_browser.verticalScrollBar()
        cur_v = scrollbar.value()
        max_v = scrollbar.maximum()

        if hasattr(self, "last_scroll_value"):
            if cur_v < self.last_scroll_value - 6:
                self.auto_scroll_enabled = False
            elif cur_v >= max_v - 16:
                self.auto_scroll_enabled = True
        else:
            self.auto_scroll_enabled = True

        self.outer_layout.activate()
        min_req_h = self.outer_layout.totalMinimumSize().height()
        effective_target_h = max(self.target_height, min_req_h)

        if abs(effective_target_h - self.current_height) > 0.8:
            self.current_height += (effective_target_h - self.current_height) * 0.22
            new_h = max(min_req_h, int(round(self.current_height)))
            if self.height() != new_h:
                self.setGeometry(self.x(), self.y(), self.max_width, new_h)
        elif int(round(self.current_height)) != effective_target_h:
            self.current_height = float(effective_target_h)
            if self.height() != effective_target_h:
                self.setGeometry(self.x(), self.y(), self.max_width, int(effective_target_h))

        if self.auto_scroll_enabled and self.current_height >= self.max_allowed_height - 12:
            if cur_v < max_v:
                new_v = cur_v + (max_v - cur_v) * 0.22
                if (max_v - cur_v) <= 2:
                    new_v = max_v
                scrollbar.setValue(int(new_v))
                self.last_scroll_value = int(new_v)
            else:
                self.last_scroll_value = cur_v
        else:
            self.last_scroll_value = cur_v

        self.glow_phase += 0.045
        if self.glow_phase > math.pi * 2:
            self.glow_phase -= math.pi * 2

        for i in range(3):
            self.current_rgb[i] += (self.target_rgb[i] - self.current_rgb[i]) * 0.14

        self.status_orb.set_state(self.current_rgb, self.glow_phase, self.status)
        self.container.update_visuals(self.current_rgb, self.glow_phase)

        if self.status == "completed" and not self.underMouse():
            word_count = len(self.clean_copy_text.split())
            if 0 < word_count < 45:
                self.idle_counter += 1
                if self.idle_counter > 320:
                    self.fade_out()

    def update_layout_height(self):
        doc = self.text_browser.document()
        viewport_w = self.text_browser.viewport().width()
        if viewport_w <= 20:
            viewport_w = self.max_width - 102
        doc.setTextWidth(float(viewport_w))
        doc_height = int(doc.size().height())
        new_target = min(doc_height + 118, self.max_allowed_height)
        if abs(new_target - self.target_height) > 2:
            self.target_height = max(self.padding_h, new_target)

    def fade_out(self):
        if not self.isVisible() or self.is_fading_out:
            return
        self.is_fading_out = True
        self.master_timer.stop()
        self.parse_debounce_timer.stop()

        self.out_anim_group = QParallelAnimationGroup(self)

        fade_out = QPropertyAnimation(self, b"windowOpacity")
        fade_out.setDuration(260)
        fade_out.setStartValue(self.windowOpacity())
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.InOutQuad)

        slide_up = QPropertyAnimation(self, b"pos")
        slide_up.setDuration(300)
        slide_up.setStartValue(self.pos())
        slide_up.setEndValue(QPoint(self.x(), self.y() - 24))
        slide_up.setEasingCurve(QEasingCurve.InCubic)

        self.out_anim_group.addAnimation(fade_out)
        self.out_anim_group.addAnimation(slide_up)
        self.out_anim_group.finished.connect(lambda: QApplication.quit())
        self.out_anim_group.start()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setCursor(Qt.ClosedHandCursor)
            self.oldPos = event.globalPos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.oldPos is not None and (event.buttons() & Qt.LeftButton):
            delta = QPoint(event.globalPos() - self.oldPos)
            new_pos = QPoint(self.x() + delta.x(), self.y() + delta.y())
            self.move(new_pos)
            self.custom_pos = new_pos
            self.oldPos = event.globalPos()
            event.accept()

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.ArrowCursor)
        self.oldPos = None
        event.accept()