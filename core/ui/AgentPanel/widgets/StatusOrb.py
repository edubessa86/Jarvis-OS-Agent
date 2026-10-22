import math

from PyQt5.QtCore import Qt, QPointF, QRectF
from PyQt5.QtWidgets import QWidget
from PyQt5.QtGui import (
    QPainter, QRadialGradient, QColor, QPen, QPainterPath, QPixmap
)


class StatusOrb(QWidget):
    _UNCACHEABLE_MODES = ("thinking", "idle", "")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.rgb = [191, 90, 242]
        self.phase = 0.0
        self.mode = "idle"
        self._cached_icon = None
        self._cached_icon_key = None

    def set_state(self, rgb, phase, mode):
        self.rgb = rgb
        self.phase = phase
        self.mode = mode
        self.update()

    def _icon_cache_key(self):
        bucket = tuple(int(c / 16) * 16 for c in self.rgb)
        return (self.mode, bucket)

    def _render_icon(self, painter, r, g, b):
        pulse = (math.sin(self.phase * 2.2) + 1.0) * 0.5
        center = QPointF(12.0, 12.0)

        core_pen = QPen(QColor(255, 255, 255, 240), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        accent_pen = QPen(QColor(r, g, b, 255), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

        if self.mode == "thinking":
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

        elif self.mode in ("search", "deep_task"):
            painter.setPen(core_pen)
            painter.setBrush(QColor(r, g, b, 85))
            painter.drawEllipse(QPointF(10.8, 10.8), 4.2, 4.2)
            painter.drawLine(QPointF(13.8, 13.8), QPointF(17.2, 17.2))

        elif self.mode == "terminal":
            painter.setPen(core_pen)
            painter.setBrush(Qt.NoBrush)
            path = QPainterPath()
            path.moveTo(7.2, 8.8)
            path.lineTo(11.0, 12.0)
            path.lineTo(7.2, 15.2)
            painter.drawPath(path)
            painter.drawLine(QPointF(12.6, 15.2), QPointF(16.5, 15.2))

        elif self.mode == "python":
            painter.setPen(core_pen)
            painter.setBrush(Qt.NoBrush)
            path = QPainterPath()
            path.moveTo(9.5, 8.5)
            path.lineTo(6.5, 12.0)
            path.lineTo(9.5, 15.5)
            painter.drawPath(path)
            path = QPainterPath()
            path.moveTo(14.5, 8.5)
            path.lineTo(17.5, 12.0)
            path.lineTo(14.5, 15.5)
            painter.drawPath(path)
            painter.drawLine(QPointF(13.0, 9.0), QPointF(11.0, 15.0))

        elif self.mode == "gui":
            painter.setPen(core_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(6.2, 6.2, 11.6, 11.6), 3.2, 3.2)
            painter.setBrush(QColor(r, g, b, 255))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(center, 2.3, 2.3)

        elif self.mode == "memory":
            painter.setPen(core_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(QPointF(9.0, 9.0), QPointF(15.0, 9.0))
            painter.drawLine(QPointF(9.0, 9.0), QPointF(12.0, 15.5))
            painter.drawLine(QPointF(15.0, 9.0), QPointF(12.0, 15.5))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(r, g, b, 245))
            painter.drawEllipse(QPointF(9.0, 9.0), 2.0, 2.0)
            painter.drawEllipse(QPointF(15.0, 9.0), 2.0, 2.0)
            painter.drawEllipse(QPointF(12.0, 15.5), 2.0, 2.0)

        elif self.mode == "file_ops":
            painter.setPen(core_pen)
            painter.setBrush(QColor(r, g, b, 85))
            path = QPainterPath()
            path.moveTo(6.5, 9.0)
            path.lineTo(10.5, 9.0)
            path.lineTo(11.5, 10.5)
            path.lineTo(17.5, 10.5)
            path.lineTo(17.5, 16.5)
            path.lineTo(6.5, 16.5)
            path.closeSubpath()
            painter.drawPath(path)

        elif self.mode == "mobile":
            painter.setPen(core_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(8.0, 6.0, 8.0, 12.5), 1.8, 1.8)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(r, g, b, 245))
            painter.drawEllipse(QPointF(12.0, 16.0), 0.9, 0.9)

        elif self.mode == "file_transfer":
            painter.setPen(core_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(QPointF(7.0, 10.0), QPointF(17.0, 10.0))
            path = QPainterPath()
            path.moveTo(15.0, 8.5)
            path.lineTo(17.0, 10.0)
            path.lineTo(15.0, 11.5)
            painter.drawPath(path)
            painter.drawLine(QPointF(17.0, 14.0), QPointF(7.0, 14.0))
            path = QPainterPath()
            path.moveTo(9.0, 12.5)
            path.lineTo(7.0, 14.0)
            path.lineTo(9.0, 15.5)
            painter.drawPath(path)

        elif self.mode == "image_gen":
            painter.setPen(core_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(6.5, 7.5, 11.0, 9.5), 1.5, 1.5)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(r, g, b, 245))
            painter.drawEllipse(QPointF(9.5, 10.5), 1.1, 1.1)
            path = QPainterPath()
            path.moveTo(7.0, 16.0)
            path.lineTo(11.0, 12.5)
            path.lineTo(13.5, 14.5)
            path.lineTo(15.0, 13.0)
            path.lineTo(17.0, 16.0)
            path.closeSubpath()
            painter.drawPath(path)

        elif self.mode == "clipboard":
            painter.setPen(core_pen)
            painter.setBrush(QColor(r, g, b, 85))
            painter.drawRoundedRect(QRectF(7.0, 8.0, 10.0, 10.0), 1.8, 1.8)
            painter.setBrush(QColor(r, g, b, 245))
            painter.drawRoundedRect(QRectF(9.5, 6.5, 5.0, 2.5), 1.0, 1.0)
            painter.setPen(QPen(QColor(255, 255, 255, 200), 1.0))
            painter.drawLine(QPointF(9.0, 11.5), QPointF(15.0, 11.5))
            painter.drawLine(QPointF(9.0, 14.0), QPointF(15.0, 14.0))

        elif self.mode == "calendar":
            painter.setPen(core_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(6.5, 8.0, 11.0, 9.5), 1.5, 1.5)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(r, g, b, 245))
            painter.drawRect(QRectF(6.5, 8.0, 11.0, 2.5))
            painter.drawEllipse(QPointF(9.5, 13.0), 0.8, 0.8)
            painter.drawEllipse(QPointF(12.0, 13.0), 0.8, 0.8)
            painter.drawEllipse(QPointF(14.5, 13.0), 0.8, 0.8)
            painter.drawEllipse(QPointF(9.5, 15.5), 0.8, 0.8)
            painter.drawEllipse(QPointF(12.0, 15.5), 0.8, 0.8)
            painter.drawEllipse(QPointF(14.5, 15.5), 0.8, 0.8)
            painter.setPen(core_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(QPointF(9.0, 6.5), QPointF(9.0, 8.0))
            painter.drawLine(QPointF(15.0, 6.5), QPointF(15.0, 8.0))

        elif self.mode == "workspace":
            painter.setPen(core_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(6.0, 7.0, 12.0, 9.0), 1.5, 1.5)
            painter.drawLine(QPointF(12.0, 16.0), QPointF(12.0, 17.5))
            painter.drawLine(QPointF(9.0, 17.5), QPointF(15.0, 17.5))

        elif self.mode == "done":
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

        elif self.mode in ("communication", "confirm_whatsapp", "confirm_telegram", "confirm_email"):
            painter.setPen(core_pen)
            painter.setBrush(QColor(r, g, b, 95))
            painter.drawRoundedRect(QRectF(6.0, 7.3, 12.0, 9.4), 2.5, 2.5)
            path = QPainterPath()
            path.moveTo(6.8, 8.3)
            path.lineTo(12.0, 12.1)
            path.lineTo(17.2, 8.3)
            painter.drawPath(path)

        else:
            painter.setPen(accent_pen)
            painter.setBrush(QColor(r, g, b, 235))
            dot_r = 4.0 + pulse * 0.6
            painter.drawEllipse(center, dot_r, dot_r)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 195))
            painter.drawEllipse(QPointF(10.7, 10.7), 1.4, 1.4)

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

        if self.mode in self._UNCACHEABLE_MODES:
            self._render_icon(painter, r, g, b)
        else:
            key = self._icon_cache_key()
            if key != self._cached_icon_key or self._cached_icon is None:
                pixmap = QPixmap(24, 24)
                pixmap.fill(Qt.transparent)
                p = QPainter(pixmap)
                p.setRenderHint(QPainter.Antialiasing, True)
                self._render_icon(p, r, g, b)
                p.end()
                self._cached_icon = pixmap
                self._cached_icon_key = key
            painter.drawPixmap(0, 0, self._cached_icon)