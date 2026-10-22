from PyQt5.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, QRectF
)
from PyQt5.QtWidgets import QTextBrowser, QFrame
from PyQt5.QtGui import (
    QColor, QPainter, QPen, QTextFormat, QTextCursor, QBrush
)

from ..styles import PREMIUM_SINGLE_LINE_SCROLLBAR_CSS
from ..render.MarkdownRenderer import MarkdownRenderer


class ThoughtViewer(QTextBrowser):
    _CODE_BG_COLOR = QColor(10, 11, 16)
    _CODE_MARKER_PROP = QTextFormat.UserProperty + 1
    _CODE_VISUAL_BG = QColor(8, 9, 14, 240)
    _CODE_BORDER = QColor(255, 255, 255, 20)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenExternalLinks(True)
        self.setOpenLinks(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
        self.viewport().setCursor(Qt.ArrowCursor)
        self.document().setDocumentMargin(4)

        self.setStyleSheet("""
            QTextBrowser {
                background: transparent;
                border: none;
                padding: 2px 6px 2px 2px;
            }
        """)
        self.verticalScrollBar().setStyleSheet(PREMIUM_SINGLE_LINE_SCROLLBAR_CSS)

        self.user_scrolled_up = False
        self._updating_dom = False
        self._last_raw_text = ""
        self._last_render_key = None
        self._target_scroll_val = 0
        self._code_groups_cache = None

        vbar = self.verticalScrollBar()
        self.smooth_scroll_anim = QPropertyAnimation(vbar, b"value", self)
        self.smooth_scroll_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.smooth_scroll_anim.setDuration(135)

        vbar.sliderPressed.connect(self._on_user_slider_interaction)
        vbar.valueChanged.connect(self._on_vbar_value_changed)
        self.smooth_scroll_anim.finished.connect(self._on_scroll_anim_finished)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._code_groups_cache = None

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if self.anchorAt(event.pos()):
            self.viewport().setCursor(Qt.PointingHandCursor)
        else:
            self.viewport().setCursor(Qt.ArrowCursor)

    def _on_user_slider_interaction(self):
        vbar = self.verticalScrollBar()
        self.user_scrolled_up = vbar.value() < max(0, vbar.maximum() - 14)

    def _on_scroll_anim_finished(self):
        self._target_scroll_val = self.verticalScrollBar().value()

    def _on_vbar_value_changed(self, value):
        if self._updating_dom:
            return
        vbar = self.verticalScrollBar()
        if vbar.isSliderDown():
            self.user_scrolled_up = value < max(0, vbar.maximum() - 14)
        elif self.smooth_scroll_anim.state() != QPropertyAnimation.Running:
            self.user_scrolled_up = value < max(0, vbar.maximum() - 14)

    def reset_scroll_lock(self):
        self.user_scrolled_up = False
        self._target_scroll_val = self.verticalScrollBar().value()

    def wheelEvent(self, event):
        vbar = self.verticalScrollBar()
        if vbar.maximum() <= 0:
            event.ignore()
            return

        delta_y = event.angleDelta().y()
        if delta_y == 0:
            event.accept()
            return

        step = int(-delta_y * 0.65)
        base_val = (
            self._target_scroll_val
            if self.smooth_scroll_anim.state() == QPropertyAnimation.Running
            else vbar.value()
        )
        new_target = max(vbar.minimum(), min(vbar.maximum(), base_val + step))
        self._target_scroll_val = new_target

        self.user_scrolled_up = new_target < max(0, vbar.maximum() - 14)

        if self.smooth_scroll_anim.state() == QPropertyAnimation.Running:
            self.smooth_scroll_anim.stop()
        self.smooth_scroll_anim.setDuration(135)
        self.smooth_scroll_anim.setStartValue(vbar.value())
        self.smooth_scroll_anim.setEndValue(new_target)
        self.smooth_scroll_anim.start()
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (
            Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown,
            Qt.Key_Home, Qt.Key_End,
        ):
            if self.smooth_scroll_anim.state() == QPropertyAnimation.Running:
                self.smooth_scroll_anim.stop()
            self._target_scroll_val = self.verticalScrollBar().value()
        super().keyPressEvent(event)

    def _post_process_code_blocks(self):
        doc = self.document()
        if doc is None:
            return
        marker_rgb = self._CODE_BG_COLOR.rgb()
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        block = doc.begin()
        while block.isValid():
            bf = block.blockFormat()
            bg = bf.background()
            if bg.style() != Qt.NoBrush and bg.color().rgb() == marker_rgb:
                bf.setBackground(QBrush(Qt.NoBrush))
                bf.setProperty(self._CODE_MARKER_PROP, True)
                cursor.setPosition(block.position())
                cursor.setBlockFormat(bf)
            block = block.next()
        cursor.endEditBlock()

    def set_markdown_content(self, raw_text: str, font_family: str, base_pt: int, accent_rgb: list):
        key = (raw_text, font_family, base_pt, tuple(accent_rgb))
        if key == self._last_render_key:
            return
        self._last_render_key = key
        self._last_raw_text = raw_text
        self._code_groups_cache = None

        if not raw_text.strip():
            self._updating_dom = True
            self.clear()
            self._updating_dom = False
            return

        rendered_html = MarkdownRenderer.to_html(raw_text, font_family, base_pt, accent_rgb)

        vbar = self.verticalScrollBar()
        prev_val = vbar.value()
        follow_bottom = not self.user_scrolled_up
        anim_was_running = self.smooth_scroll_anim.state() == QPropertyAnimation.Running

        self._updating_dom = True
        self.setUpdatesEnabled(False)
        self.document().setHtml(rendered_html)
        self._post_process_code_blocks()
        self._code_groups_cache = None

        new_max = vbar.maximum()
        if follow_bottom:
            if anim_was_running:
                vbar.setValue(prev_val)
                self.smooth_scroll_anim.setEndValue(new_max)
            else:
                vbar.setValue(new_max)
            self._target_scroll_val = new_max
            self.setUpdatesEnabled(True)
            self._updating_dom = False
        else:
            vbar.setValue(min(prev_val, new_max))
            self._target_scroll_val = vbar.value()
            self.setUpdatesEnabled(True)
            self._updating_dom = False

    def _get_code_groups(self):
        if self._code_groups_cache is not None:
            return self._code_groups_cache

        doc = self.document()
        if doc is None:
            self._code_groups_cache = []
            return self._code_groups_cache
        layout = doc.documentLayout()
        if layout is None:
            self._code_groups_cache = []
            return self._code_groups_cache

        groups = []
        current = None

        block = doc.begin()
        while block.isValid():
            bf = block.blockFormat()
            is_code = (
                bf.hasProperty(self._CODE_MARKER_PROP)
                and bool(bf.property(self._CODE_MARKER_PROP))
            )
            if is_code:
                r = layout.blockBoundingRect(block)
                if current is None:
                    current = [r.top(), r.bottom(), r.left(), r.right()]
                else:
                    current[1] = r.bottom()
                    current[2] = min(current[2], r.left())
                    current[3] = max(current[3], r.right())
            else:
                if current is not None:
                    groups.append(tuple(current))
                    current = None
            block = block.next()

        if current is not None:
            groups.append(tuple(current))

        self._code_groups_cache = groups
        return groups

    def paintEvent(self, event):
        self._paint_code_block_backgrounds()
        super().paintEvent(event)

    def _paint_code_block_backgrounds(self):
        if self._updating_dom:
            return
        groups = self._get_code_groups()
        if not groups:
            return

        vbar = self.verticalScrollBar()
        hbar = self.horizontalScrollBar()
        offset_y = vbar.value() if vbar else 0
        offset_x = hbar.value() if hbar else 0
        vp_h = self.viewport().height()

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing, True)

        for top_doc, bottom_doc, left_doc, right_doc in groups:
            top = top_doc - offset_y
            bottom = bottom_doc - offset_y
            if bottom <= -4 or top >= vp_h + 4:
                continue
            left = left_doc - offset_x
            right = right_doc - offset_x
            self._draw_code_bg(painter, top, bottom, left, right)

        painter.end()

    def _draw_code_bg(self, painter, top, bottom, left, right):
        pad = 2.0
        rect = QRectF(
            left - pad,
            top - pad,
            (right - left) + 2 * pad,
            (bottom - top) + 2 * pad,
        )
        radius = 8.0
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._CODE_VISUAL_BG)
        painter.drawRoundedRect(rect, radius, radius)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(self._CODE_BORDER, 1))
        painter.drawRoundedRect(rect, radius, radius)