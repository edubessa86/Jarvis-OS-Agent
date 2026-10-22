import math

from PyQt5.QtCore import Qt, QPointF, QRect, QRectF, QSize
from PyQt5.QtWidgets import QFrame
from PyQt5.QtGui import (
    QPainter, QLinearGradient, QRadialGradient, QPen, QColor, QPixmap, QRegion
)


class IslandContainer(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rgb = [191, 90, 242]
        self.phase = 0.0
        self.corner_radius = 28.0
        self._static_pixmap = None
        self._static_size = None
        self._static_bucket = None
        self._static_dpr = 1.0
        self._interactive_resizing = False
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(0, 0)

    def minimumSizeHint(self):
        return QSize(0, 0)

    def set_interactive_resizing(self, active: bool):
        if self._interactive_resizing != active:
            self._interactive_resizing = active
            if not active:
                self._static_pixmap = None
                self.update()

    def update_visuals(self, rgb, phase):
        new_rgb = [max(0, min(255, int(c))) for c in rgb]
        bucket_changed = (tuple(int(c / 16) * 16 for c in new_rgb) != self._static_bucket)
        self.rgb = new_rgb
        self.phase = phase

        if bucket_changed or self._static_size != self.size() or self._interactive_resizing:
            self.update()
            return

        w, h = self.width(), self.height()
        if w > 64 and h > 48:
            il, it, ir, ib = self.island_grip_margins()
            inner_exclude = QRect(
                il + 20,
                it + 16,
                max(1, w - (il + ir + 40)),
                max(1, h - (it + ib + 32)),
            )
            border_region = QRegion(self.rect()) - QRegion(inner_exclude)
            self.update(border_region)
        else:
            self.update()

    def _rgb_bucket(self):
        return tuple(int(c / 16) * 16 for c in self.rgb)

    @classmethod
    def island_grip_margins(cls):
        return (9, 7, 9, 11)

    def _draw_static_layers(self, painter, size, bucket, fast_mode=False):
        rect = QRectF(0, 0, size.width(), size.height())
        il, it, ir, ib = self.island_grip_margins()
        island_rect = rect.adjusted(float(il), float(it), -float(ir), -float(ib))
        radius = self.corner_radius
        r, g, b = bucket

        if fast_mode:
            shadow_rect = island_rect.adjusted(2.0, 3.0, -2.0, 5.0)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 42))
            painter.drawRoundedRect(shadow_rect, radius, radius)
        else:
            for offset, alpha in [(6, 10), (4, 16), (2, 24), (1, 32)]:
                shadow_rect = island_rect.adjusted(2.0, float(offset), -2.0, float(offset + 2))
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(0, 0, 0, alpha))
                painter.drawRoundedRect(shadow_rect, radius, radius)

        bg_grad = QLinearGradient(island_rect.topLeft(), island_rect.bottomLeft())
        bg_grad.setColorAt(0.0, QColor(30, 31, 38, 248))
        bg_grad.setColorAt(0.45, QColor(18, 19, 24, 252))
        bg_grad.setColorAt(1.0, QColor(10, 10, 14, 254))
        painter.setBrush(bg_grad)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(island_rect, radius, radius)

        ambient_glow = QRadialGradient(
            QPointF(island_rect.left() + island_rect.width() * 0.22, island_rect.top()),
            island_rect.width() * 0.58,
        )
        ambient_glow.setColorAt(0.0, QColor(r, g, b, 30))
        ambient_glow.setColorAt(1.0, QColor(r, g, b, 0))
        painter.setBrush(ambient_glow)
        painter.drawRoundedRect(island_rect, radius, radius)

        shine_rect = QRectF(
            island_rect.left() + 16.0,
            island_rect.top() + 1.2,
            max(0.0, island_rect.width() - 32.0),
            1.0,
        )
        shine_grad = QLinearGradient(shine_rect.topLeft(), shine_rect.topRight())
        shine_grad.setColorAt(0.0, QColor(255, 255, 255, 0))
        shine_grad.setColorAt(0.5, QColor(255, 255, 255, 80))
        shine_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(shine_grad)
        painter.drawRect(shine_rect)

    def _ensure_static(self):
        current_size = self.size()
        if current_size.width() <= 0 or current_size.height() <= 0:
            return False
        current_bucket = self._rgb_bucket()
        dpr = self.devicePixelRatioF()
        if (
            self._static_pixmap is not None
            and self._static_size == current_size
            and self._static_bucket == current_bucket
            and self._static_dpr == dpr
        ):
            return True
        self._static_pixmap = self._render_static(current_size, current_bucket, dpr)
        self._static_size = current_size
        self._static_bucket = current_bucket
        self._static_dpr = dpr
        return True

    def _render_static(self, size, bucket, dpr):
        px_w = max(1, int(round(size.width() * dpr)))
        px_h = max(1, int(round(size.height() * dpr)))
        pixmap = QPixmap(px_w, px_h)
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        self._draw_static_layers(painter, size, bucket, fast_mode=False)
        painter.end()
        return pixmap

    def paintEvent(self, event):
        current_size = self.size()
        if current_size.width() <= 0 or current_size.height() <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setClipRegion(event.region())

        if self._interactive_resizing:
            self._draw_static_layers(painter, current_size, self._rgb_bucket(), fast_mode=True)
        else:
            if not self._ensure_static():
                painter.end()
                return
            painter.drawPixmap(0, 0, self._static_pixmap)

        rect = QRectF(self.rect())
        il, it, ir, ib = self.island_grip_margins()
        island_rect = rect.adjusted(float(il), float(it), -float(ir), -float(ib))
        radius = self.corner_radius
        r, g, b = [max(0, min(255, int(c))) for c in self.rgb]

        cx = island_rect.center().x()
        cy = island_rect.center().y()
        span = max(island_rect.width(), island_rect.height()) * 0.65
        dx = math.cos(self.phase) * span
        dy = math.sin(self.phase) * span

        border_grad = QLinearGradient(QPointF(cx - dx, cy - dy), QPointF(cx + dx, cy + dy))
        border_grad.setColorAt(0.0, QColor(255, 255, 255, 100))
        border_grad.setColorAt(0.35, QColor(255, 255, 255, 24))
        border_grad.setColorAt(0.72, QColor(r, g, b, 185))
        border_grad.setColorAt(1.0, QColor(255, 255, 255, 60))

        border_pen = QPen(border_grad, 1.3)
        painter.setPen(border_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(
            island_rect.adjusted(0.65, 0.65, -0.65, -0.65), radius, radius
        )

        inner_rect = island_rect.adjusted(2.0, 2.0, -2.0, -2.0)
        inner_pen = QPen(QColor(r, g, b, 70), 1.0)
        painter.setPen(inner_pen)
        painter.drawRoundedRect(inner_rect, radius - 2.0, radius - 2.0)