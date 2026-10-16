import sys
import os
import random
import json
import math
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QHBoxLayout,
    QVBoxLayout, QFrame, QSizePolicy
)
from PyQt5.QtCore import (
    Qt, QTimer, QPropertyAnimation, QParallelAnimationGroup,
    QSequentialAnimationGroup, QEasingCurve, QPoint, QPointF,
    QRect, QRectF, pyqtProperty, QSize, qInstallMessageHandler
)
from PyQt5.QtGui import (
    QColor, QPainter, QFont, QFontDatabase, QLinearGradient,
    QRadialGradient, QPen, QPainterPath, QFontMetrics
)
from PyQt5.QtNetwork import QUdpSocket, QHostAddress

os.environ["QT_LOGGING_RULES"] = "qt.qpa.window=false;qt.text.font.db=false;default.warning=false"


def _stt_qt_message_handler(mode, context, message):
    suppressed = (
        "OpenType support missing",
        "QColor",
        "Unable to set geometry",
        "Resulting geometry",
        "QWindowsWindow::setGeometry",
    )
    if any(s in message for s in suppressed):
        return


qInstallMessageHandler(_stt_qt_message_handler)


# ==================================================================
#                       WAVEFORM INDICATOR
# ==================================================================

class WaveformIndicator(QWidget):
    """Animated 5-bar waveform. Bigger, smoother, with distinct
    visual identity per state (listening / understanding / transcribed)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(46, 26)
        self.bars = [5.0, 7.0, 12.0, 7.0, 5.0]
        self.target_bars = [5.0, 7.0, 12.0, 7.0, 5.0]
        self.is_animating = False
        self.mode = "listening"
        self.phase = 0.0

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.update_bars)

    def start_animation(self, mode):
        self.mode = mode
        self.is_animating = True
        if not self.anim_timer.isActive():
            self.anim_timer.start(16)

    def stop_animation(self, mode):
        self.mode = mode
        self.is_animating = False
        self.target_bars = [5.0, 5.0, 5.0, 5.0, 5.0]

    def update_bars(self):
        self.phase += 0.12
        if self.is_animating:
            if self.mode == "understanding":
                # Smooth mathematical pulse — feels "thinking"
                for i in range(5):
                    wave = math.sin(self.phase * 2.5 + i * 0.8)
                    self.target_bars[i] = 5.0 + abs(wave) * 16.0
            else:
                # Organic random — feels "hearing voice"
                for i in range(5):
                    self.target_bars[i] = random.uniform(4.5, 20.0)

        needs_update = False
        for i in range(5):
            diff = self.target_bars[i] - self.bars[i]
            if abs(diff) > 0.05:
                self.bars[i] += diff * 0.22
                needs_update = True
        if needs_update or self.is_animating:
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        cx, cy = self.width() / 2.0, self.height() / 2.0

        # --- Transcribed: green check ---
        if self.mode == "transcribed":
            painter.setPen(Qt.NoPen)
            # Soft outer ring
            painter.setBrush(QColor(50, 215, 75, 55))
            painter.drawEllipse(QPointF(cx, cy), 11.5, 11.5)
            # Solid green circle
            painter.setBrush(QColor(50, 215, 75, 240))
            painter.drawEllipse(QPointF(cx, cy), 9.0, 9.0)
            # Check mark
            check_pen = QPen(QColor(255, 255, 255, 255), 2.2,
                             Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(check_pen)
            path = QPainterPath()
            path.moveTo(cx - 4.0, cy + 0.4)
            path.lineTo(cx - 1.2, cy + 3.2)
            path.lineTo(cx + 4.4, cy - 2.8)
            painter.drawPath(path)
            return

        # --- Listening / Understanding: bars ---
        base_col = QColor(10, 132, 255, 245) if self.mode == "listening" \
            else QColor(191, 90, 242, 245)

        bar_width = 3.8
        spacing = 3.4
        total_w = 5 * bar_width + 4 * spacing
        start_x = (self.width() - total_w) / 2.0
        max_h = self.height() - 4.0

        painter.setPen(Qt.NoPen)
        for i in range(5):
            x = start_x + i * (bar_width + spacing)
            height = max(4.0, min(self.bars[i], max_h))
            y = (self.height() - height) / 2.0

            # Glow under bar
            glow = QColor(base_col)
            glow.setAlpha(55)
            painter.setBrush(glow)
            painter.drawRoundedRect(
                QRectF(x - 1.0, y - 1.0, bar_width + 2.0, height + 2.0),
                (bar_width + 2.0) / 2.0, (bar_width + 2.0) / 2.0
            )

            # Main bar with vertical gradient
            grad = QLinearGradient(x, y, x, y + height)
            grad.setColorAt(0.0, QColor(base_col.red(), base_col.green(),
                                        base_col.blue(), 255))
            grad.setColorAt(1.0, QColor(base_col.red(), base_col.green(),
                                        base_col.blue(), 165))
            painter.setBrush(grad)
            painter.drawRoundedRect(
                QRectF(x, y, bar_width, height),
                bar_width / 2.0, bar_width / 2.0
            )


# ==================================================================
#                          ISLAND FRAME
# ==================================================================

class IslandFrame(QFrame):
    """Rounded dark island. No external shadow — only internal
    ambient glow and an animated gradient border."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rgb = [10.0, 132.0, 255.0]
        self.phase = 0.0
        self.corner_radius = 32.0
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

        # Small inset so the animated border has breathing room
        island_rect = rect.adjusted(5.0, 3.0, -5.0, -5.0)
        radius = self.corner_radius

        # --- Main background ---
        bg_grad = QLinearGradient(island_rect.topLeft(), island_rect.bottomLeft())
        bg_grad.setColorAt(0.0, QColor(31, 32, 42, 252))
        bg_grad.setColorAt(0.5, QColor(18, 19, 26, 254))
        bg_grad.setColorAt(1.0, QColor(9, 10, 15, 255))

        painter.setBrush(bg_grad)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(island_rect, radius, radius)

        # --- Ambient glow (top-left, color-tinted) ---
        ambient_glow = QRadialGradient(
            QPointF(island_rect.left() + island_rect.width() * 0.22,
                    island_rect.top()),
            island_rect.width() * 0.6
        )
        ambient_glow.setColorAt(0.0, QColor(r, g, b, 36))
        ambient_glow.setColorAt(0.55, QColor(r, g, b, 12))
        ambient_glow.setColorAt(1.0, QColor(r, g, b, 0))
        painter.setBrush(ambient_glow)
        painter.drawRoundedRect(island_rect, radius, radius)

        # --- Bottom color bloom ---
        bloom = QRadialGradient(
            QPointF(island_rect.center().x(), island_rect.bottom()),
            island_rect.width() * 0.55
        )
        bloom.setColorAt(0.0, QColor(r, g, b, 26))
        bloom.setColorAt(1.0, QColor(r, g, b, 0))
        painter.setBrush(bloom)
        painter.drawRoundedRect(island_rect, radius, radius)

        # --- Animated gradient border (rotates around island) ---
        cx = island_rect.center().x()
        cy = island_rect.center().y()
        span = max(island_rect.width(), island_rect.height()) * 0.7
        dx = math.cos(self.phase) * span
        dy = math.sin(self.phase) * span

        border_grad = QLinearGradient(
            QPointF(cx - dx, cy - dy),
            QPointF(cx + dx, cy + dy)
        )
        border_grad.setColorAt(0.0, QColor(255, 255, 255, 115))
        border_grad.setColorAt(0.35, QColor(255, 255, 255, 24))
        border_grad.setColorAt(0.72, QColor(r, g, b, 200))
        border_grad.setColorAt(1.0, QColor(255, 255, 255, 60))

        border_pen = QPen(border_grad, 1.35)
        painter.setPen(border_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(
            island_rect.adjusted(0.7, 0.7, -0.7, -0.7),
            radius, radius
        )

        # --- Top inner shine ---
        shine_rect = island_rect.adjusted(
            22.0, 1.2, -22.0, -(island_rect.height() - 2.0)
        )
        shine_grad = QLinearGradient(shine_rect.topLeft(), shine_rect.topRight())
        shine_grad.setColorAt(0.0, QColor(255, 255, 255, 0))
        shine_grad.setColorAt(0.5, QColor(255, 255, 255, 72))
        shine_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(shine_grad)
        painter.drawRect(shine_rect)


# ==================================================================
#                            STT POPUP
# ==================================================================

class STTPopup(QWidget):
    # -- Design tokens -------------------------------------------------
    ISLAND_HEIGHT = 65
    OUTER_MARGINS = (8, 5, 8, 7)
    ISLAND_PADDING = (24, 0, 26, 0)
    ISLAND_SPACING = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_state = "idle"
        self.can_hide = True
        self.last_text = ""
        self.gradient_phase = 0.0
        self.current_rgb = [10.0, 132.0, 255.0]
        self.target_rgb = [10.0, 132.0, 255.0]

        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        eng_font_path = os.path.join(base_dir, "Data", "fonts", "english.ttf")
        hin_font_path = os.path.join(base_dir, "Data", "fonts", "devangri.ttf")

        self.font_eng = QFont("Segoe UI", 12, QFont.Medium)
        eng_id = QFontDatabase.addApplicationFont(eng_font_path)
        if eng_id != -1:
            fams = QFontDatabase.applicationFontFamilies(eng_id)
            if fams:
                self.font_eng = QFont(fams[0], 12, QFont.Medium)

        self.font_hin = QFont("Nirmala UI", 13, QFont.Medium)
        hin_id = QFontDatabase.addApplicationFont(hin_font_path)
        if hin_id != -1:
            fams = QFontDatabase.applicationFontFamilies(hin_id)
            if fams:
                self.font_hin = QFont(fams[0], 13, QFont.Medium)

        fallback_chain = [self.font_hin.family(), "Nirmala UI",
                          "Segoe UI", "Mangal"]
        try:
            QFont.insertSubstitutions(self.font_eng.family(), fallback_chain)
            QFont.insertSubstitutions("MS Shell Dlg 2", fallback_chain)
            QFont.insertSubstitutions("Sansation", fallback_chain)
        except Exception:
            pass

        self.resize_anim = QPropertyAnimation(self, b"geometry", self)
        self.resize_anim.setEasingCurve(QEasingCurve.OutQuint)
        self.resize_anim.setDuration(240)

        self.show_anim_group = QParallelAnimationGroup(self)
        self.hide_anim_group = QParallelAnimationGroup(self)

        self.transcribed_timer = QTimer(self)
        self.transcribed_timer.setSingleShot(True)
        self.transcribed_timer.timeout.connect(self.allow_hide)

        self.glow_timer = QTimer(self)
        self.glow_timer.timeout.connect(self.update_glow_effect)
        self.glow_timer.start(36)

        self.target_geometry = QRect()

        self.initUI()

        self.udpSocket = QUdpSocket(self)
        self.udpSocket.bind(QHostAddress.LocalHost, 5556)
        self.udpSocket.readyRead.connect(self.readPendingDatagrams)
        self.process_status_update({"status": "idle", "text": ""})

    # ----------------------------------------------------------------
    def update_glow_effect(self):
        self.gradient_phase += 0.045
        if self.gradient_phase >= math.pi * 2:
            self.gradient_phase -= math.pi * 2

        color_map = {
            "listening":     [10.0, 132.0, 255.0],
            "understanding": [191.0, 90.0, 242.0],
            "transcribed":   [50.0, 215.0, 75.0],
            "idle":          [110.0, 110.0, 120.0],
        }
        self.target_rgb = color_map.get(self.current_state,
                                        [10.0, 132.0, 255.0])

        for i in range(3):
            self.current_rgb[i] += (self.target_rgb[i] - self.current_rgb[i]) * 0.16

        self.island.update_visuals(self.current_rgb, self.gradient_phase)

    def readPendingDatagrams(self):
        while self.udpSocket.hasPendingDatagrams():
            datagram, host, port = self.udpSocket.readDatagram(
                self.udpSocket.pendingDatagramSize())
            try:
                data = json.loads(datagram.decode('utf-8'))
                if isinstance(data, dict):
                    self.process_status_update(data)
            except Exception:
                pass

    # ----------------------------------------------------------------
    def initUI(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
            Qt.Tool | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(1, 1)

        self.outer_layout = QVBoxLayout(self)
        self.outer_layout.setContentsMargins(*self.OUTER_MARGINS)

        self.island = IslandFrame(self)
        self.island.setObjectName("Island")
        self.island.setFixedHeight(self.ISLAND_HEIGHT)

        self.layout = QHBoxLayout(self.island)
        self.layout.setContentsMargins(*self.ISLAND_PADDING)
        self.layout.setSpacing(self.ISLAND_SPACING)

        self.waveform = WaveformIndicator(self.island)
        self.waveform.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.text_label = QLabel("Listening...", self.island)
        self.text_label.setWordWrap(False)
        self.text_label.setSizePolicy(QSizePolicy.MinimumExpanding,
                                      QSizePolicy.MinimumExpanding)
        self.text_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        self.set_fast_text_style("Listening...",
                                 "rgba(255, 255, 255, 0.45)", QFont.Normal)

        self.layout.addWidget(self.waveform, 0,
                              Qt.AlignVCenter | Qt.AlignLeft)
        self.layout.addWidget(self.text_label, 1,
                              Qt.AlignVCenter | Qt.AlignLeft)

        self.outer_layout.addWidget(self.island)
        self.setWindowOpacity(0.0)
        self.hide()

    # ----------------------------------------------------------------
    def format_text(self, text):
        words = text.strip().split()
        if len(words) > 7:
            return "... " + " ".join(words[-7:])
        return text.strip()

    def is_hindi(self, text):
        for char in str(text):
            if '\u0900' <= char <= '\u097F':
                return True
        return False

    def set_fast_text_style(self, text, color, weight):
        font = self.font_hin if self.is_hindi(text) else self.font_eng
        font.setWeight(weight)
        self.text_label.setFont(font)
        self.text_label.setStyleSheet(
            f"color: {color}; background: transparent; "
            f"border: none; letter-spacing: 0.35px;"
        )
        self.text_label.setText(text)

    # ----------------------------------------------------------------
    def calculate_target_geometry(self):
        self.text_label.adjustSize()
        island_width = (
            self.text_label.sizeHint().width()
            + self.waveform.width()
            + self.ISLAND_PADDING[0] + self.ISLAND_PADDING[2]
            + self.ISLAND_SPACING
        )
        ideal_width = max(240, min(island_width, 760)) \
            + self.OUTER_MARGINS[0] + self.OUTER_MARGINS[2]
        ideal_height = self.ISLAND_HEIGHT \
            + self.OUTER_MARGINS[1] + self.OUTER_MARGINS[3] + 4

        screen = QApplication.primaryScreen().availableGeometry()
        x = (screen.width() - ideal_width) // 2
        y = screen.bottom() - ideal_height - 52
        return QRect(x, y, ideal_width, ideal_height)

    def smooth_resize(self):
        new_geometry = self.calculate_target_geometry()
        if self.target_geometry != new_geometry:
            self.target_geometry = new_geometry
            if self.isVisible() and self.windowOpacity() > 0.4:
                if self.resize_anim.state() == QPropertyAnimation.Running:
                    self.resize_anim.stop()
                self.resize_anim.setStartValue(self.geometry())
                self.resize_anim.setEndValue(new_geometry)
                self.resize_anim.start()
            else:
                self.setGeometry(new_geometry)

    # ----------------------------------------------------------------
    def show_panel(self):
        if self.hide_anim_group.state() == QPropertyAnimation.Running:
            self.hide_anim_group.stop()
        self.smooth_resize()

        if not self.isVisible() or self.windowOpacity() < 1.0:
            if self.show_anim_group.state() == QPropertyAnimation.Running:
                return

            start_y = self.target_geometry.y() + 22
            self.setGeometry(self.target_geometry.x(), start_y,
                             self.target_geometry.width(),
                             self.target_geometry.height())
            self.show()
            self.raise_()
            self.show_anim_group.clear()

            fade_in = QPropertyAnimation(self, b"windowOpacity")
            fade_in.setDuration(240)
            fade_in.setStartValue(self.windowOpacity())
            fade_in.setEndValue(1.0)
            fade_in.setEasingCurve(QEasingCurve.OutCubic)

            slide_up = QPropertyAnimation(self, b"pos")
            slide_up.setDuration(320)
            slide_up.setStartValue(self.pos())
            slide_up.setEndValue(QPoint(self.target_geometry.x(),
                                        self.target_geometry.y()))
            slide_up.setEasingCurve(QEasingCurve.OutQuint)

            self.show_anim_group.addAnimation(fade_in)
            self.show_anim_group.addAnimation(slide_up)
            self.show_anim_group.start()

    def hide_panel(self):
        if not self.isVisible():
            return
        if self.show_anim_group.state() == QPropertyAnimation.Running:
            self.show_anim_group.stop()
        self.hide_anim_group.clear()

        fade_out = QPropertyAnimation(self, b"windowOpacity")
        fade_out.setDuration(200)
        fade_out.setStartValue(self.windowOpacity())
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.InOutQuad)

        slide_down = QPropertyAnimation(self, b"pos")
        slide_down.setDuration(260)
        slide_down.setStartValue(self.pos())
        slide_down.setEndValue(QPoint(self.x(), self.y() + 18))
        slide_down.setEasingCurve(QEasingCurve.InCubic)

        self.hide_anim_group.addAnimation(fade_out)
        self.hide_anim_group.addAnimation(slide_down)
        try:
            self.hide_anim_group.finished.disconnect(self.hide_panel_finished)
        except TypeError:
            pass
        self.hide_anim_group.finished.connect(self.hide_panel_finished)
        self.hide_anim_group.start()

    def hide_panel_finished(self):
        self.hide()

    def allow_hide(self):
        self.can_hide = True
        self.process_status_update({"status": self.current_state,
                                    "text": self.last_text})

    # ----------------------------------------------------------------
    def process_status_update(self, data):
        self.current_state = data.get("status", "idle")
        raw_text = data.get("text", "")

        if self.current_state == "exit":
            QApplication.quit()
            return

        if self.current_state == "idle":
            self.waveform.stop_animation("idle")
            if not self.can_hide:
                return
            self.hide_panel()
            self.last_text = ""
            return

        if self.current_state == "listening":
            self.transcribed_timer.stop()
            self.waveform.start_animation("listening")
            if not raw_text.strip():
                self.set_fast_text_style("Listening...",
                                         "rgba(255, 255, 255, 0.45)",
                                         QFont.Normal)
            else:
                display_text = self.format_text(raw_text) or \
                    self.format_text(self.last_text)
                if (not self.is_hindi(display_text)
                        and display_text
                        and not display_text.startswith("...")):
                    display_text = display_text[0].upper() + display_text[1:]
                self.last_text = raw_text
                self.set_fast_text_style(display_text,
                                         "rgba(255, 255, 255, 0.96)",
                                         QFont.Medium)

        elif self.current_state == "understanding":
            self.transcribed_timer.stop()
            self.waveform.start_animation("understanding")
            display_text = self.format_text(raw_text) or \
                self.format_text(self.last_text)
            if (not self.is_hindi(display_text)
                    and display_text
                    and not display_text.startswith("...")):
                display_text = display_text[0].upper() + display_text[1:]
            self.last_text = raw_text
            self.set_fast_text_style(display_text,
                                     "rgba(255, 255, 255, 0.96)",
                                     QFont.Medium)

        elif self.current_state == "transcribed":
            self.can_hide = False
            self.transcribed_timer.start(2200)
            self.waveform.stop_animation("transcribed")

            display_text = self.format_text(raw_text) or \
                self.format_text(self.last_text)
            if (display_text
                    and not self.is_hindi(display_text)
                    and not display_text.startswith("...")):
                display_text = display_text[0].upper() + display_text[1:]
            self.set_fast_text_style(display_text, "#FFFFFF", QFont.Medium)

        self.show_panel()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    panel = STTPopup()
    sys.exit(app.exec_())