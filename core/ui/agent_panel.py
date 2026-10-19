import sys
import json
import os
import re
import math
import queue
import html
import zmq

os.environ["QT_LOGGING_RULES"] = "qt.qpa.window=false;qt.text.font.db=false;default.warning=false"

from PyQt5.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve,
    QPoint, QPointF, QRect, QRectF, QParallelAnimationGroup,
    pyqtProperty, QSize, qInstallMessageHandler,
    QThread, pyqtSignal, QEvent
)
from PyQt5.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QApplication,
    QFrame, QSizePolicy, QPushButton, QTextEdit, QLineEdit,
    QTextBrowser
)
from PyQt5.QtGui import (
    QFont, QColor, QFontDatabase, QFontMetrics,
    QPainter, QLinearGradient, QRadialGradient, QPen, QPainterPath, QCursor
)


def qt_message_handler(mode, context, message):
    suppressed = (
        "Unable to set geometry",
        "Resulting geometry",
        "OpenType support missing",
    )
    if any(s in message for s in suppressed):
        return
    if sys.__stderr__ is not None:
        sys.__stderr__.write(f"{message}\n")


qInstallMessageHandler(qt_message_handler)


PREMIUM_SINGLE_LINE_SCROLLBAR_CSS = """
    QScrollBar:vertical {
        background: rgba(255, 255, 255, 0.06);
        width: 2px;
        margin: 4px 1px 4px 1px;
        border-radius: 1px;
    }
    QScrollBar::handle:vertical {
        background: rgba(255, 255, 255, 0.38);
        border-radius: 1px;
        min-height: 24px;
    }
    QScrollBar::handle:vertical:hover {
        background: rgba(255, 255, 255, 0.75);
    }
    QScrollBar::handle:vertical:pressed {
        background: rgba(255, 255, 255, 0.95);
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0px;
        width: 0px;
        background: none;
    }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
        background: none;
    }
"""


class MarkdownRenderer:
    TOKEN_MASTER_RE = re.compile(
        r'(?P<comment>#[^\n]*|//[^\n]*|/\*[\s\S]*?\*/)'
        r'|(?P<string>"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')'
        r'|(?P<decorator>@[a-zA-Z_]\w*)'
        r'|(?P<keyword>\b(?:def|class|return|if|elif|else|for|while|try|except|finally|with|as|import|from|pass|break|continue|and|or|not|in|is|lambda|yield|async|await|True|False|None|const|let|var|function|export|default|switch|case|new|this|typeof|interface|type|struct|enum|pub|fn|impl|use|mod|match|where|select|insert|update|delete|create|table|drop|alter|raise|global|nonlocal|del|assert)\b)'
        r'|(?P<builtin>\b(?:self|cls|print|len|range|enumerate|zip|map|filter|int|str|float|list|dict|set|tuple|bool|super|open|isinstance|getattr|setattr|hasattr|Exception|ValueError|TypeError|KeyError|RuntimeError|console|window|document|Promise|JSON|Math|Object|Array)\b)'
        r'|(?P<func>\b[a-zA-Z_]\w*(?=\s*\())'
        r'|(?P<number>\b(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?(?:e[+-]?\d+)?)\b)'
    )

    CODE_BLOCK_RE = re.compile(r"```([a-zA-Z0-9_+-]*)[ \t]*\n?(.*?)```", re.DOTALL)
    INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
    BOLD_ITALIC_RE = re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*")
    BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__")
    ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*|\s)(.+?)(?<!\*|\s)\*(?!\*)")
    HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$")
    BULLET_RE = re.compile(r"^(\s*)[-*•]\s+(.+)$")
    NUMBERED_RE = re.compile(r"^(\s*)(\d+)\.\s+(.+)$")
    LONG_TOKEN_RE = re.compile(r"\S{65,}")

    @classmethod
    def _soft_wrap_long_tokens(cls, text: str) -> str:
        def inject_zwsp(match):
            s = match.group(0)
            return "\u200b".join(s[i:i + 45] for i in range(0, len(s), 45))
        return cls.LONG_TOKEN_RE.sub(inject_zwsp, text)

    @classmethod
    def auto_heal_markdown(cls, text: str) -> str:
        fence_count = len(re.findall(r"```", text))
        if fence_count % 2 != 0:
            text = text + "\n```"
        return text

    @classmethod
    def highlight_code(cls, code: str) -> str:
        result = []
        last_idx = 0
        color_map = {
            "comment": "color: #7E88A6; font-style: italic;",
            "string": "color: #F1FA8C;",
            "decorator": "color: #50FA7B;",
            "keyword": "color: #FF79C6; font-weight: bold;",
            "builtin": "color: #8BE9FD; font-style: italic;",
            "func": "color: #50FA7B;",
            "number": "color: #BD93F9;",
        }

        for match in cls.TOKEN_MASTER_RE.finditer(code):
            start, end = match.span()
            if start > last_idx:
                chunk = cls._soft_wrap_long_tokens(code[last_idx:start])
                result.append(html.escape(chunk))
            group_name = match.lastgroup
            token_text = html.escape(cls._soft_wrap_long_tokens(match.group(0)))
            style = color_map.get(group_name, "color: #F8F8F2;")
            result.append(f'<span style="{style}">{token_text}</span>')
            last_idx = end

        if last_idx < len(code):
            chunk = cls._soft_wrap_long_tokens(code[last_idx:])
            result.append(html.escape(chunk))

        highlighted = "".join(result)
        highlighted = highlighted.replace("\t", "    ")
        return highlighted

    @classmethod
    def format_inline(cls, text: str) -> str:
        inline_vault = []

        def save_inline_code(m):
            idx = len(inline_vault)
            code_content = html.escape(cls._soft_wrap_long_tokens(m.group(1)))
            inline_vault.append(
                f'<span style="background-color: rgba(255, 255, 255, 0.11); '
                f'color: #FF79C6; font-family: \'Cascadia Code\', \'Consolas\', monospace; '
                f'font-size: 9.5pt;">&nbsp;{code_content}&nbsp;</span>'
            )
            return f"@@INLINECODE_{idx}@@"

        processed = cls.INLINE_CODE_RE.sub(save_inline_code, text)
        processed = html.escape(cls._soft_wrap_long_tokens(processed))

        processed = cls.BOLD_ITALIC_RE.sub(
            r'<b style="color: #FFFFFF;"><i>\1</i></b>', processed
        )
        processed = cls.BOLD_RE.sub(
            lambda m: f'<b style="color: #FFFFFF;">{m.group(1) or m.group(2)}</b>',
            processed
        )
        processed = cls.ITALIC_RE.sub(
            r'<i style="color: rgba(235, 235, 250, 0.92);">\1</i>', processed
        )

        for idx, badge_html in enumerate(inline_vault):
            processed = processed.replace(f"@@INLINECODE_{idx}@@", badge_html)

        return processed

    @classmethod
    def to_html(cls, raw_text: str, font_family: str, base_pt: int, accent_rgb: list) -> str:
        if not raw_text.strip():
            return ""

        clean = raw_text.strip()
        r, g, b = [max(0, min(255, int(c))) for c in accent_rgb]
        accent_hex = f"rgb({r}, {g}, {b})"

        healed = cls.auto_heal_markdown(clean)
        code_blocks = []

        def extract_code_block(match):
            lang = (match.group(1) or "code").strip().upper()
            raw_code = match.group(2).rstrip("\n")
            highlighted = cls.highlight_code(raw_code)
            block_html = (
                f'<table width="100%" cellspacing="0" cellpadding="0" '
                f'style="margin-top: 6px; margin-bottom: 6px; table-layout: fixed; '
                f'background-color: rgba(8, 9, 14, 0.82); '
                f'border: 1px solid rgba(255, 255, 255, 0.12);">'
                f'<tr><td style="background-color: rgba(255, 255, 255, 0.06); '
                f'padding: 3px 10px;">'
                f'<span style="font-family: \'Segoe UI\', sans-serif; font-size: 7.5pt; '
                f'font-weight: bold; color: {accent_hex}; letter-spacing: 0.8px;">{html.escape(lang)}</span>'
                f'</td></tr>'
                f'<tr><td style="padding: 8px 10px; word-wrap: break-word;">'
                f'<pre style="margin: 0px; font-family: \'Cascadia Code\', \'Consolas\', \'Courier New\', monospace; '
                f'font-size: 9.5pt; color: #F8F8F2; white-space: pre-wrap; word-wrap: break-word;">{highlighted}</pre>'
                f'</td></tr></table>'
            )
            idx = len(code_blocks)
            code_blocks.append(block_html)
            return f"@@CODEBLOCK_{idx}@@"

        staged = cls.CODE_BLOCK_RE.sub(extract_code_block, healed)
        lines = staged.split("\n")
        out_lines = []
        body_pt = max(10, base_pt - 1)

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("@@CODEBLOCK_") and stripped.endswith("@@"):
                idx_str = stripped.replace("@@CODEBLOCK_", "").replace("@@", "")
                if idx_str.isdigit() and int(idx_str) < len(code_blocks):
                    out_lines.append(code_blocks[int(idx_str)])
                continue

            if not stripped:
                out_lines.append('<div style="height: 5px; font-size: 1pt;">&nbsp;</div>')
                continue

            if stripped in ("---", "***", "___"):
                out_lines.append(
                    '<hr style="border: none; background-color: rgba(255, 255, 255, 0.12); height: 1px; margin: 6px 0px;" />'
                )
                continue

            h_match = cls.HEADING_RE.match(stripped)
            if h_match:
                level = len(h_match.group(1))
                h_text = cls.format_inline(h_match.group(2))
                h_size = max(body_pt, body_pt + (3 - level))
                h_color = "#FFFFFF" if level <= 2 else "#8BE9FD"
                out_lines.append(
                    f'<div style="margin-top: 6px; margin-bottom: 3px; font-size: {h_size}pt; '
                    f'font-weight: bold; color: {h_color};">{h_text}</div>'
                )
                continue

            if stripped.startswith(">"):
                q_text = cls.format_inline(stripped.lstrip(">").strip())
                out_lines.append(
                    f'<div style="margin: 3px 0px 3px 8px; color: rgba(210, 214, 235, 0.85); '
                    f'font-style: italic;"><span style="color: {accent_hex}; font-weight: bold;">▎ </span>{q_text}</div>'
                )
                continue

            b_match = cls.BULLET_RE.match(line)
            if b_match:
                indent_px = 6 + min(36, len(b_match.group(1)) * 6)
                b_text = cls.format_inline(b_match.group(2))
                out_lines.append(
                    f'<div style="margin-left: {indent_px}px; margin-top: 2px; margin-bottom: 2px;">'
                    f'<span style="color: {accent_hex}; font-weight: bold;">•&nbsp;</span>{b_text}</div>'
                )
                continue

            n_match = cls.NUMBERED_RE.match(line)
            if n_match:
                indent_px = 6 + min(36, len(n_match.group(1)) * 6)
                num_str = n_match.group(2)
                n_text = cls.format_inline(n_match.group(3))
                out_lines.append(
                    f'<div style="margin-left: {indent_px}px; margin-top: 2px; margin-bottom: 2px;">'
                    f'<span style="color: {accent_hex}; font-weight: bold;">{num_str}.&nbsp;</span>{n_text}</div>'
                )
                continue

            out_lines.append(
                f'<div style="margin-top: 2px; margin-bottom: 2px; line-height: 1.40; word-wrap: break-word;">'
                f'{cls.format_inline(line)}</div>'
            )

        body_html = "".join(out_lines)
        return (
            f'<div style="font-family: \'{font_family}\', \'Segoe UI\', sans-serif; '
            f'font-size: {body_pt}pt; font-weight: 450; color: rgba(244, 244, 252, 0.95); '
            f'text-align: left; padding-top: 2px;">{body_html}</div>'
        )


class ThoughtViewer(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.viewport().setCursor(Qt.ArrowCursor)
        self.document().setDocumentMargin(4)

        self.setStyleSheet("""
            QTextBrowser {
                background: transparent;
                border: none;
                padding: 2px 6px 2px 2px;
            }
        """ + PREMIUM_SINGLE_LINE_SCROLLBAR_CSS)

        self.user_scrolled_up = False
        self._updating_dom = False
        self._last_raw_text = ""
        self._last_rendered_html = ""
        self._target_scroll_val = 0

        vbar = self.verticalScrollBar()
        self.smooth_scroll_anim = QPropertyAnimation(vbar, b"value", self)
        self.smooth_scroll_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.smooth_scroll_anim.setDuration(135)

        vbar.sliderPressed.connect(self._on_user_slider_interaction)
        vbar.valueChanged.connect(self._on_vbar_value_changed)

    def _on_user_slider_interaction(self):
        vbar = self.verticalScrollBar()
        self.user_scrolled_up = vbar.value() < max(0, vbar.maximum() - 14)

    def _on_vbar_value_changed(self, value):
        if self._updating_dom:
            return
        vbar = self.verticalScrollBar()
        if vbar.isSliderDown():
            self.user_scrolled_up = value < max(0, vbar.maximum() - 14)

    def reset_scroll_lock(self):
        self.user_scrolled_up = False
        self._target_scroll_val = 0

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

    def set_markdown_content(self, raw_text: str, font_family: str, base_pt: int, accent_rgb: list):
        self._last_raw_text = raw_text
        if not raw_text.strip():
            self._updating_dom = True
            self.clear()
            self._last_rendered_html = ""
            self._updating_dom = False
            return

        rendered_html = MarkdownRenderer.to_html(raw_text, font_family, base_pt, accent_rgb)
        if rendered_html == self._last_rendered_html:
            return

        self._last_rendered_html = rendered_html
        vbar = self.verticalScrollBar()
        prev_val = vbar.value()
        follow_bottom = not self.user_scrolled_up

        if self.smooth_scroll_anim.state() == QPropertyAnimation.Running and follow_bottom:
            self.smooth_scroll_anim.stop()

        self._updating_dom = True
        self.setUpdatesEnabled(False)
        self.document().setHtml(rendered_html)

        new_max = vbar.maximum()
        if follow_bottom:
            start_val = min(prev_val, new_max)
            vbar.setValue(start_val)
            self.setUpdatesEnabled(True)
            self._updating_dom = False
            self._target_scroll_val = new_max
            if abs(new_max - start_val) <= 3:
                vbar.setValue(new_max)
            else:
                self.smooth_scroll_anim.setDuration(95)
                self.smooth_scroll_anim.setStartValue(start_val)
                self.smooth_scroll_anim.setEndValue(new_max)
                self.smooth_scroll_anim.start()
        else:
            vbar.setValue(min(prev_val, new_max))
            self.setUpdatesEnabled(True)
            self._updating_dom = False


class AgentZmqListener(QThread):
    status_received = pyqtSignal(dict)
    confirmation_requested = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.reply_queue = queue.Queue()
        self._running = True

    def send_decision(self, payload: dict):
        self.reply_queue.put_nowait(payload)

    def stop(self):
        self._running = False
        self.wait(1000)

    def run(self):
        context = zmq.Context()

        sub_socket = context.socket(zmq.SUB)
        sub_socket.setsockopt(zmq.LINGER, 0)
        sub_socket.connect("tcp://127.0.0.1:5555")
        sub_socket.setsockopt_string(zmq.SUBSCRIBE, "AGENT_UPDATE")

        confirm_pull = context.socket(zmq.PULL)
        confirm_pull.setsockopt(zmq.LINGER, 0)
        try:
            confirm_pull.bind("tcp://127.0.0.1:5556")
        except Exception:
            confirm_pull.connect("tcp://127.0.0.1:5556")

        confirm_push = context.socket(zmq.PUSH)
        confirm_push.setsockopt(zmq.LINGER, 200)
        confirm_push.setsockopt(zmq.SNDTIMEO, 300)
        try:
            confirm_push.bind("tcp://127.0.0.1:5557")
        except Exception:
            confirm_push.connect("tcp://127.0.0.1:5557")

        poller = zmq.Poller()
        poller.register(sub_socket, zmq.POLLIN)
        poller.register(confirm_pull, zmq.POLLIN)

        while self._running:
            try:
                while not self.reply_queue.empty():
                    reply_data = self.reply_queue.get_nowait()
                    try:
                        confirm_push.send_json(reply_data, flags=zmq.NOBLOCK)
                    except zmq.Again:
                        try:
                            confirm_push.send_json(reply_data)
                        except Exception:
                            pass

                socks = dict(poller.poll(25))

                if sub_socket in socks and socks[sub_socket] == zmq.POLLIN:
                    while self._running:
                        try:
                            message = sub_socket.recv_string(flags=zmq.NOBLOCK)
                            topic, json_data = message.split(" ", 1)
                            data = json.loads(json_data)
                            self.status_received.emit(data)
                        except zmq.Again:
                            break
                        except Exception:
                            break

                if confirm_pull in socks and socks[confirm_pull] == zmq.POLLIN:
                    while self._running:
                        try:
                            confirm_data = confirm_pull.recv_json(flags=zmq.NOBLOCK)
                            self.confirmation_requested.emit(confirm_data)
                        except zmq.Again:
                            break
                        except Exception:
                            break
            except Exception:
                pass

        try:
            sub_socket.close()
            confirm_pull.close()
            confirm_push.close()
            context.term()
        except Exception:
            pass


class StatusOrb(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.rgb = [191, 90, 242]
        self.phase = 0.0
        self.mode = "idle"

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

        core_pen = QPen(QColor(255, 255, 255, 240), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        accent_pen = QPen(QColor(r, g, b, 255), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

        if self.mode == "thinking":
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


class IslandContainer(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rgb = [191, 90, 242]
        self.phase = 0.0
        self.corner_radius = 28.0
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
            (1.0, 4.0, 30.0, QColor(0, 0, 0, 18)),
            (2.5, 4.0, 29.5, QColor(0, 0, 0, 34)),
            (4.5, 3.0, 29.0, QColor(0, 0, 0, 50)),
            (6.5, 2.0, 28.5, QColor(r, g, b, 22)),
            (8.0, 1.0, 28.0, QColor(0, 0, 0, 80)),
        ]
        painter.setPen(Qt.NoPen)
        for inset, y_offset, rad, col in shadow_steps:
            s_rect = rect.adjusted(inset, inset + y_offset * 0.4, -inset, -inset + y_offset * 0.6)
            painter.setBrush(col)
            painter.drawRoundedRect(s_rect, rad, rad)

        island_rect = rect.adjusted(9.0, 7.0, -9.0, -11.0)
        radius = self.corner_radius

        bg_grad = QLinearGradient(island_rect.topLeft(), island_rect.bottomLeft())
        bg_grad.setColorAt(0.0, QColor(30, 31, 38, 248))
        bg_grad.setColorAt(0.45, QColor(18, 19, 24, 252))
        bg_grad.setColorAt(1.0, QColor(10, 10, 14, 254))

        painter.setBrush(bg_grad)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(island_rect, radius, radius)

        ambient_glow = QRadialGradient(
            QPointF(island_rect.left() + island_rect.width() * 0.22, island_rect.top()),
            island_rect.width() * 0.58
        )
        ambient_glow.setColorAt(0.0, QColor(r, g, b, 30))
        ambient_glow.setColorAt(1.0, QColor(r, g, b, 0))
        painter.setBrush(ambient_glow)
        painter.drawRoundedRect(island_rect, radius, radius)

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
        painter.drawRoundedRect(island_rect.adjusted(0.65, 0.65, -0.65, -0.65), radius, radius)

        shine_rect = island_rect.adjusted(16.0, 1.2, -16.0, -(island_rect.height() - 2.2))
        shine_grad = QLinearGradient(shine_rect.topLeft(), shine_rect.topRight())
        shine_grad.setColorAt(0.0, QColor(255, 255, 255, 0))
        shine_grad.setColorAt(0.5, QColor(255, 255, 255, 65))
        shine_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(shine_grad)
        painter.drawRect(shine_rect)


class ObservationCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rgb = [191, 90, 242]
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(0, 0)

    def minimumSizeHint(self):
        return QSize(0, 0)

    def set_accent(self, rgb):
        self.rgb = rgb
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        r, g, b = [max(0, min(255, int(c))) for c in self.rgb]

        painter.setBrush(QColor(255, 255, 255, 12))
        painter.setPen(QPen(QColor(255, 255, 255, 24), 1.0))
        painter.drawRoundedRect(rect, 12.0, 12.0)

        bar_rect = QRectF(rect.left() + 5.0, rect.top() + 7.0, 3.2, max(6.0, rect.height() - 14.0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(r, g, b, 220))
        painter.drawRoundedRect(bar_rect, 1.6, 1.6)


class AgentPanel(QWidget):
    COLOR_MAP = {
        "search": [10, 132, 255],
        "deep_task": [255, 55, 95],
        "python": [48, 130, 200],
        "thinking": [191, 90, 242],
        "workspace": [255, 159, 10],
        "communication": [48, 209, 88],
        "confirm_whatsapp": [37, 211, 102],
        "confirm_telegram": [0, 152, 234],
        "confirm_email": [255, 159, 10],
        "gui": [94, 92, 230],
        "mobile": [0, 212, 178],
        "file_transfer": [0, 180, 160],
        "file_ops": [255, 214, 10],
        "terminal": [50, 215, 75],
        "memory": [175, 82, 222],
        "calendar": [90, 200, 250],
        "clipboard": [255, 100, 130],
        "image_gen": [255, 45, 85],
        "done": [48, 209, 88],
        "idle": [110, 110, 120],
    }

    def __init__(self):
        super().__init__()

        self.last_status = None
        self.current_step = -1
        self.last_tag_text = ""
        self.custom_pos = None

        self.is_dragging = False
        self.is_resizing = False
        self.resize_edge = None
        self.resize_start_mouse = QPoint()
        self.resize_start_geom = QRect()
        self._interaction_origin = None
        self._override_cursor_active = False

        self.DEFAULT_WIDTH = 580
        self.DEFAULT_HEIGHT = 196
        self.MIN_WIDTH = 440
        self.MAX_WIDTH = 1280
        self.MIN_HEIGHT = 160
        self.MAX_HEIGHT = 860

        self.user_width = self.DEFAULT_WIDTH
        self.user_height = self.DEFAULT_HEIGHT
        self.MAX_THOUGHT_CHARS = 24000

        self._raw_detail_text = ""
        self._raw_to_text = ""
        self._raw_attachment_text = ""
        self._current_thought_text = ""
        self._pending_thought_text = None

        eng_id = QFontDatabase.addApplicationFont("Data/fonts/english.ttf")
        eng_fams = QFontDatabase.applicationFontFamilies(eng_id) if eng_id != -1 else []
        self.font_eng = eng_fams[0] if eng_fams else "Segoe UI"

        hin_id = QFontDatabase.addApplicationFont("Data/fonts/devangri.ttf")
        hin_fams = QFontDatabase.applicationFontFamilies(hin_id) if hin_id != -1 else []
        self.font_hin = hin_fams[0] if hin_id != -1 and hin_fams else "Nirmala UI"

        fallback_chain = [self.font_hin, "Nirmala UI", "Segoe UI", "Mangal"]
        try:
            QFont.insertSubstitutions(self.font_eng, fallback_chain)
            QFont.insertSubstitutions("MS Shell Dlg 2", fallback_chain)
            QFont.insertSubstitutions("Sansation", fallback_chain)
        except Exception:
            pass

        self.gradient_phase = 0.0
        self.current_action_type = "idle"
        self.awaiting_confirmation = False
        self.active_confirm_id = None
        self.active_confirm_platform = ""

        self.current_rgb = [191.0, 90.0, 242.0]
        self.target_rgb = [191, 90, 242]

        self.rgb_timer = QTimer(self)
        self.rgb_timer.timeout.connect(self.update_glow_effect)

        self.thought_render_timer = QTimer(self)
        self.thought_render_timer.setSingleShot(True)
        self.thought_render_timer.setInterval(32)
        self.thought_render_timer.timeout.connect(self.flush_pending_thought)

        self.target_geometry = None
        self.resize_anim = QPropertyAnimation(self, b"anim_geometry")
        self.resize_anim.setEasingCurve(QEasingCurve.OutQuint)
        self.resize_anim.setDuration(210)

        self.initUI()

        app = QApplication.instance()
        app.installEventFilter(self)
        app.aboutToQuit.connect(self._cleanup_resources)

        self.zmq_listener = AgentZmqListener()
        self.zmq_listener.status_received.connect(self.process_status_update)
        self.zmq_listener.confirmation_requested.connect(self.show_confirmation_preview)
        self.zmq_listener.start()

    def _cleanup_resources(self):
        self.clear_override_cursor()
        if hasattr(self, "zmq_listener") and self.zmq_listener.isRunning():
            self.zmq_listener.stop()

    def closeEvent(self, event):
        self._cleanup_resources()
        super().closeEvent(event)

    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        return QSize(self.user_width, self.user_height)

    @pyqtProperty(QRect)
    def anim_geometry(self):
        return self.geometry()

    @anim_geometry.setter
    def anim_geometry(self, rect):
        self.setGeometry(rect)

    def initUI(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setMinimumSize(0, 0)
        self.setStyleSheet("background: transparent; border: none; outline: none;")

        self.outer_layout = QVBoxLayout(self)
        self.outer_layout.setContentsMargins(14, 10, 14, 12)
        self.outer_layout.setSizeConstraint(QVBoxLayout.SetNoConstraint)

        self.container = IslandContainer(self)
        self.container.setObjectName("IslandWrapper")
        self.container.setMouseTracking(True)
        self.container.setMinimumSize(0, 0)

        self.wrapper_layout = QVBoxLayout(self.container)
        self.wrapper_layout.setContentsMargins(9, 7, 9, 11)
        self.wrapper_layout.setSizeConstraint(QVBoxLayout.SetNoConstraint)

        self.inner_island = QFrame(self.container)
        self.inner_island.setObjectName("Island")
        self.inner_island.setMouseTracking(True)
        self.inner_island.setMinimumSize(0, 0)
        self.inner_island.setStyleSheet("background: transparent; border: none;")
        self.wrapper_layout.addWidget(self.inner_island)

        self.layout = QVBoxLayout(self.inner_island)
        self.layout.setContentsMargins(22, 16, 22, 16)
        self.layout.setSpacing(10)
        self.layout.setSizeConstraint(QVBoxLayout.SetNoConstraint)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(9)

        self.pulse_dot = StatusOrb(self.inner_island)
        self.start_pulse_animation()

        self.status_tag = QLabel("Jarvis Ready")
        self.status_tag.setFont(QFont(self.font_eng, 10, QFont.Bold))
        self.status_tag.setStyleSheet("""
            QLabel {
                color: rgba(255, 255, 255, 0.96);
                background: transparent;
                border: none;
                letter-spacing: 0.4px;
            }
        """)
        self.status_tag.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.detail_pill = QLabel("")
        self.detail_pill.setFont(QFont(self.font_eng, 8, QFont.Medium))
        self.detail_pill.setStyleSheet("""
            QLabel {
                color: rgba(235, 235, 245, 0.82);
                background-color: rgba(255, 255, 255, 0.07);
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 8px;
                padding: 2px 9px;
            }
        """)
        self.detail_pill.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.detail_pill.hide()

        header_layout.addWidget(self.pulse_dot)
        header_layout.addWidget(self.status_tag)
        header_layout.addWidget(self.detail_pill)
        header_layout.addStretch()

        self.meta_capsule = QFrame(self.inner_island)
        self.meta_capsule.setAttribute(Qt.WA_StyledBackground, True)
        self.meta_capsule.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 255, 255, 0.048);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 9px;
            }
        """)
        meta_capsule_layout = QHBoxLayout(self.meta_capsule)
        meta_capsule_layout.setContentsMargins(10, 3, 10, 3)
        meta_capsule_layout.setSpacing(6)

        self.phase_label = QLabel("Step 00")
        self.phase_label.setFont(QFont(self.font_eng, 8, QFont.Bold))
        self.phase_label.setStyleSheet("color: rgba(255, 255, 255, 0.65); border: none; background: transparent;")
        self.phase_label.setAlignment(Qt.AlignCenter)

        self.meta_dot = QLabel("|")
        self.meta_dot.setFont(QFont(self.font_eng, 8, QFont.Normal))
        self.meta_dot.setStyleSheet("color: rgba(255, 255, 255, 0.22); border: none; background: transparent;")

        self.token_label = QLabel("0 tok")
        self.token_label.setFont(QFont(self.font_eng, 8, QFont.Medium))
        self.token_label.setStyleSheet("color: rgba(255, 255, 255, 0.46); border: none; background: transparent;")
        self.token_label.setAlignment(Qt.AlignCenter)

        meta_capsule_layout.addWidget(self.phase_label)
        meta_capsule_layout.addWidget(self.meta_dot)
        meta_capsule_layout.addWidget(self.token_label)

        header_layout.addWidget(self.meta_capsule)
        self.layout.addLayout(header_layout)

        self.thought_viewer = ThoughtViewer(self.inner_island)
        self.thought_viewer.setMinimumSize(0, 0)
        self.thought_viewer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.thought_viewer.setMouseTracking(True)
        self.thought_viewer.viewport().setMouseTracking(True)
        self.layout.addWidget(self.thought_viewer, 1)

        self.separator = QFrame()
        self.separator.setMinimumSize(0, 1)
        self.separator.setMaximumHeight(1)
        self.separator.setStyleSheet(
            "background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "stop:0 rgba(255,255,255,0), stop:0.5 rgba(255,255,255,0.16), stop:1 rgba(255,255,255,0)); "
            "border: none;"
        )
        self.layout.addWidget(self.separator)
        self.separator.hide()

        self.obs_card = ObservationCard(self.inner_island)
        self.obs_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        obs_card_layout = QHBoxLayout(self.obs_card)
        obs_card_layout.setContentsMargins(15, 9, 13, 9)
        obs_card_layout.setSpacing(0)

        self.obs_label = QLabel("")
        self.obs_label.setMinimumSize(0, 0)
        self.obs_label.setWordWrap(True)
        self.obs_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.obs_label.setFont(QFont(self.font_eng, 9, QFont.Normal))
        self.obs_label.setStyleSheet("""
            QLabel {
                color: rgba(232, 232, 244, 0.90);
                border: none;
                background: transparent;
            }
        """)
        obs_card_layout.addWidget(self.obs_label)
        self.layout.addWidget(self.obs_card)
        self.obs_card.hide()

        self.preview_card = QFrame()
        self.preview_card.setObjectName("PreviewCard")
        self.preview_card.setMinimumSize(0, 0)
        self.preview_card.setAttribute(Qt.WA_StyledBackground, True)
        self.preview_card.setStyleSheet("""
            #PreviewCard {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.095);
                border-radius: 15px;
            }
        """)

        self.preview_layout = QVBoxLayout(self.preview_card)
        self.preview_layout.setContentsMargins(14, 12, 14, 12)
        self.preview_layout.setSpacing(8)
        self.preview_layout.setSizeConstraint(QVBoxLayout.SetNoConstraint)

        meta_layout = QHBoxLayout()
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(8)

        self.preview_badge = QLabel("MESSAGE")
        self.preview_badge.setFont(QFont(self.font_eng, 8, QFont.Bold))
        self.preview_badge.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.preview_badge.setStyleSheet("""
            QLabel {
                color: #FFFFFF;
                background-color: rgba(50, 215, 75, 0.25);
                border: 1px solid rgba(50, 215, 75, 0.55);
                border-radius: 6px;
                padding: 2px 8px;
                letter-spacing: 0.7px;
            }
        """)

        self.preview_to_label = QLabel("")
        self.preview_to_label.setFont(QFont(self.font_eng, 9, QFont.Bold))
        self.preview_to_label.setStyleSheet("""
            QLabel {
                color: rgba(255, 255, 255, 0.92);
                background-color: rgba(255, 255, 255, 0.055);
                border: 1px solid rgba(255, 255, 255, 0.085);
                border-radius: 6px;
                padding: 2px 9px;
            }
        """)
        self.preview_to_label.setMinimumSize(0, 0)
        self.preview_to_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

        meta_layout.addWidget(self.preview_badge)
        meta_layout.addWidget(self.preview_to_label)
        meta_layout.addStretch()
        self.preview_layout.addLayout(meta_layout)

        self.preview_subject_input = QLineEdit()
        self.preview_subject_input.setMinimumSize(0, 0)
        self.preview_subject_input.setFixedHeight(30)
        self.preview_subject_input.setCursor(Qt.IBeamCursor)
        self.preview_subject_input.setPlaceholderText("Subject...")
        self.preview_subject_input.setFont(QFont(self.font_eng, 10, QFont.Medium))
        self.preview_subject_input.setStyleSheet("""
            QLineEdit {
                background-color: rgba(0, 0, 0, 0.36);
                color: rgba(255, 255, 255, 0.95);
                border: 1px solid rgba(255, 255, 255, 0.085);
                border-radius: 8px;
                padding: 4px 10px;
            }
            QLineEdit:focus {
                border: 1px solid rgba(255, 255, 255, 0.28);
                background-color: rgba(0, 0, 0, 0.50);
            }
        """)
        self.preview_layout.addWidget(self.preview_subject_input)
        self.preview_subject_input.hide()

        self.preview_body_edit = QTextEdit()
        self.preview_body_edit.setMinimumSize(0, 0)
        self.preview_body_edit.setFixedHeight(82)
        self.preview_body_edit.setCursor(Qt.IBeamCursor)
        self.preview_body_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.preview_body_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.preview_body_edit.setFont(QFont(self.font_eng, 10, QFont.Normal))
        self.preview_body_edit.setStyleSheet("""
            QTextEdit {
                background-color: rgba(0, 0, 0, 0.36);
                color: rgba(245, 245, 250, 0.95);
                border: 1px solid rgba(255, 255, 255, 0.085);
                border-radius: 10px;
                padding: 6px 10px;
                selection-background-color: rgba(191, 90, 242, 0.45);
            }
            QTextEdit:focus {
                border: 1px solid rgba(255, 255, 255, 0.28);
                background-color: rgba(0, 0, 0, 0.50);
            }
        """ + PREMIUM_SINGLE_LINE_SCROLLBAR_CSS)
        self.preview_layout.addWidget(self.preview_body_edit)

        self.preview_attachment_label = QLabel("")
        self.preview_attachment_label.setMinimumSize(0, 0)
        self.preview_attachment_label.setFixedHeight(22)
        self.preview_attachment_label.setFont(QFont(self.font_eng, 8, QFont.Bold))
        self.preview_attachment_label.setStyleSheet("""
            QLabel {
                color: rgba(235, 235, 245, 0.82);
                background-color: rgba(255, 255, 255, 0.055);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
                padding: 2px 8px;
            }
        """)
        self.preview_layout.addWidget(self.preview_attachment_label)
        self.preview_attachment_label.hide()

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 2, 0, 0)
        btn_layout.setSpacing(8)

        self.preview_hint_label = QLabel("Click text to edit  |  Esc to cancel  |  Ctrl+Enter to send")
        self.preview_hint_label.setFont(QFont(self.font_eng, 8, QFont.Normal))
        self.preview_hint_label.setStyleSheet("color: rgba(255, 255, 255, 0.36); background: transparent; border: none;")
        btn_layout.addWidget(self.preview_hint_label)
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.setFixedSize(90, 30)
        self.cancel_btn.setFont(QFont(self.font_eng, 9, QFont.Bold))
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 69, 58, 0.14);
                color: #FF6961;
                border: 1px solid rgba(255, 69, 58, 0.32);
                border-radius: 9px;
            }
            QPushButton:hover {
                background-color: rgba(255, 69, 58, 0.28);
                border: 1px solid rgba(255, 69, 58, 0.58);
                color: #FFFFFF;
            }
            QPushButton:pressed {
                background-color: rgba(255, 69, 58, 0.40);
            }
        """)
        self.cancel_btn.clicked.connect(lambda: self.handle_confirm_decision(False))

        self.send_btn = QPushButton("Send Now")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setFixedSize(114, 30)
        self.send_btn.setFont(QFont(self.font_eng, 9, QFont.Bold))
        self.send_btn.clicked.connect(lambda: self.handle_confirm_decision(True))

        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.send_btn)
        self.preview_layout.addLayout(btn_layout)

        self.layout.addWidget(self.preview_card)
        self.preview_card.hide()

        self.outer_layout.addWidget(self.container)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide_panel)

        self.setWindowOpacity(0.0)
        self.setCursor(Qt.ArrowCursor)
        self.hide()

    def _resolve_mouse_coords(self, obj, event):
        local_pos = event.pos() if obj is self else obj.mapTo(self, event.pos())
        raw_global = event.globalPos()
        if raw_global == event.pos():
            origin = (
                self._interaction_origin
                if (self.is_dragging or self.is_resizing) and self._interaction_origin is not None
                else self.geometry().topLeft()
            )
            if origin != QPoint(0, 0):
                global_pos = origin + local_pos
            else:
                global_pos = raw_global
        else:
            global_pos = raw_global
        return local_pos, global_pos

    def detect_resize_edge(self, local_pos: QPoint):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return None

        ix1, iy1 = 23, 17
        ix2, iy2 = w - 23, h - 23
        grip = 9
        corner_span = 22

        x, y = local_pos.x(), local_pos.y()
        if x < 0 or x > w or y < 0 or y > h:
            return None

        near_left = x <= ix1 + grip
        near_right = x >= ix2 - grip
        near_top = y <= iy1 + grip
        near_bottom = y >= iy2 - grip

        if not (near_left or near_right or near_top or near_bottom):
            return None

        in_left_corner = x <= ix1 + corner_span
        in_right_corner = x >= ix2 - corner_span
        in_top_corner = y <= iy1 + corner_span
        in_bottom_corner = y >= iy2 - corner_span

        if (near_top and in_left_corner) or (near_left and in_top_corner):
            return "top_left"
        if (near_top and in_right_corner) or (near_right and in_top_corner):
            return "top_right"
        if (near_bottom and in_left_corner) or (near_left and in_bottom_corner):
            return "bottom_left"
        if (near_bottom and in_right_corner) or (near_right and in_bottom_corner):
            return "bottom_right"
        if near_left:
            return "left"
        if near_right:
            return "right"
        if near_top:
            return "top"
        if near_bottom:
            return "bottom"
        return None

    def set_override_cursor_shape(self, cursor_shape):
        if cursor_shape is not None:
            if self._override_cursor_active:
                QApplication.changeOverrideCursor(cursor_shape)
            else:
                QApplication.setOverrideCursor(cursor_shape)
                self._override_cursor_active = True
        else:
            self.clear_override_cursor()

    def set_edge_cursor(self, edge):
        cursor_map = {
            "left": Qt.SizeHorCursor,
            "right": Qt.SizeHorCursor,
            "top": Qt.SizeVerCursor,
            "bottom": Qt.SizeVerCursor,
            "top_left": Qt.SizeFDiagCursor,
            "bottom_right": Qt.SizeFDiagCursor,
            "top_right": Qt.SizeBDiagCursor,
            "bottom_left": Qt.SizeBDiagCursor,
        }
        self.set_override_cursor_shape(cursor_map.get(edge))

    def clear_override_cursor(self):
        if self._override_cursor_active:
            QApplication.restoreOverrideCursor()
            self._override_cursor_active = False

    def is_interactive_child(self, widget):
        interactive_widgets = (
            self.cancel_btn,
            self.send_btn,
            self.preview_subject_input,
            self.preview_body_edit,
            self.preview_body_edit.viewport(),
            self.thought_viewer.verticalScrollBar(),
            self.preview_body_edit.verticalScrollBar(),
        )
        curr = widget
        while curr is not None and curr is not self:
            if curr in interactive_widgets:
                return True
            curr = curr.parentWidget()
        return False

    def stop_active_geometry_animations(self):
        if self.resize_anim.state() == QPropertyAnimation.Running:
            self.resize_anim.stop()
        if hasattr(self, "show_anim_group") and self.show_anim_group.state() == QPropertyAnimation.Running:
            self.show_anim_group.stop()

    def get_extra_cards_height(self, for_width: int) -> int:
        if self.awaiting_confirmation and self.preview_card.isVisible():
            extra_height = 200
            if self.preview_subject_input.isVisible():
                extra_height += 38
            if self.preview_attachment_label.isVisible():
                extra_height += 30
            return extra_height
        if self.obs_card.isVisible() and self.obs_label.text().strip():
            fm = QFontMetrics(self.obs_label.font())
            avail_w = max(180, for_width - 108)
            bound = fm.boundingRect(0, 0, avail_w, 300, Qt.TextWordWrap, self.obs_label.text())
            obs_h = min(72, max(24, bound.height() + 4))
            return obs_h + 34
        return 0

    def perform_interactive_resize(self, global_pos: QPoint):
        delta = global_pos - self.resize_start_mouse
        start = self.resize_start_geom
        x, y, w, h = start.x(), start.y(), start.width(), start.height()

        if "right" in self.resize_edge:
            w = max(self.MIN_WIDTH, min(self.MAX_WIDTH, start.width() + delta.x()))
        elif "left" in self.resize_edge:
            w = max(self.MIN_WIDTH, min(self.MAX_WIDTH, start.width() - delta.x()))
            x = start.right() - w + 1

        extra_h = self.get_extra_cards_height(w)
        min_total_h = self.MIN_HEIGHT + extra_h
        max_total_h = self.MAX_HEIGHT + extra_h

        if "bottom" in self.resize_edge:
            h = max(min_total_h, min(max_total_h, start.height() + delta.y()))
        elif "top" in self.resize_edge:
            h = max(min_total_h, min(max_total_h, start.height() - delta.y()))
            y = start.bottom() - h + 1

        self.user_width = w
        self.user_height = max(self.MIN_HEIGHT, min(self.MAX_HEIGHT, h - extra_h))
        self.custom_pos = QPoint(x, y)

        new_rect = QRect(x, y, w, h)
        self.target_geometry = new_rect
        self.setGeometry(new_rect)
        self.refresh_elided_labels()

    def reset_to_default_size(self):
        self.user_width = self.DEFAULT_WIDTH
        self.user_height = self.DEFAULT_HEIGHT
        new_geom = self.calculate_target_geometry()
        self.target_geometry = new_geom
        self.stop_active_geometry_animations()
        self.resize_anim.setStartValue(self.geometry())
        self.resize_anim.setEndValue(new_geom)
        self.resize_anim.start()
        self.refresh_elided_labels()

    def eventFilter(self, obj, event):
        if not self.isVisible() or not isinstance(obj, QWidget):
            return super().eventFilter(obj, event)

        if obj is not self and not self.isAncestorOf(obj):
            return super().eventFilter(obj, event)

        etype = event.type()

        if etype == QEvent.MouseMove:
            local_pos, global_pos = self._resolve_mouse_coords(obj, event)

            if self.is_resizing:
                self.perform_interactive_resize(global_pos)
                return True

            if self.is_dragging and hasattr(self, "drag_position"):
                new_pos = global_pos - self.drag_position
                self.custom_pos = new_pos
                self.stop_active_geometry_animations()
                self.move(new_pos)
                self.target_geometry = self.geometry()
                return True

            if event.buttons() == Qt.NoButton:
                if self.is_interactive_child(obj):
                    self.clear_override_cursor()
                else:
                    edge = self.detect_resize_edge(local_pos)
                    self.set_edge_cursor(edge)

        elif etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._interaction_origin = self.geometry().topLeft()
            local_pos, global_pos = self._resolve_mouse_coords(obj, event)
            edge = None if self.is_interactive_child(obj) else self.detect_resize_edge(local_pos)

            if edge is not None:
                self.is_resizing = True
                self.resize_edge = edge
                self.resize_start_mouse = global_pos
                self.resize_start_geom = self.geometry()
                self.stop_active_geometry_animations()
                self.set_edge_cursor(edge)
                return True

            if not self.is_interactive_child(obj):
                self.is_dragging = True
                self.drag_position = global_pos - self.geometry().topLeft()
                self.stop_active_geometry_animations()
                self.set_override_cursor_shape(Qt.ClosedHandCursor)
                return True

        elif etype == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            local_pos, _ = self._resolve_mouse_coords(obj, event)
            if self.is_resizing:
                self.is_resizing = False
                self.resize_edge = None
                self._interaction_origin = None
                self.set_edge_cursor(self.detect_resize_edge(local_pos))
                return True

            if self.is_dragging:
                self.is_dragging = False
                self._interaction_origin = None
                if hasattr(self, "drag_position"):
                    del self.drag_position
                self.set_edge_cursor(self.detect_resize_edge(local_pos))
                return True

        elif etype == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
            local_pos, _ = self._resolve_mouse_coords(obj, event)
            if self.detect_resize_edge(local_pos) is None and not self.is_interactive_child(obj):
                if local_pos.y() <= 62:
                    self.reset_to_default_size()
                    return True

        elif etype == QEvent.Leave:
            if not self.is_resizing and not self.is_dragging:
                cursor_local = self.mapFromGlobal(QCursor.pos())
                if not self.rect().contains(cursor_local):
                    self.clear_override_cursor()

        return super().eventFilter(obj, event)

    def refresh_elided_labels(self):
        curr_w = self.width() if self.width() > 0 else self.user_width

        if self._raw_detail_text and self.detail_pill.isVisible():
            fm = QFontMetrics(self.detail_pill.font())
            max_detail_w = max(110, curr_w - 365)
            self.detail_pill.setText(fm.elidedText(self._raw_detail_text, Qt.ElideRight, max_detail_w))

        if self._raw_to_text and self.preview_card.isVisible():
            fm_to = QFontMetrics(self.preview_to_label.font())
            max_to_w = max(180, curr_w - 220)
            self.preview_to_label.setText(fm_to.elidedText(self._raw_to_text, Qt.ElideRight, max_to_w))

        if self._raw_attachment_text and self.preview_attachment_label.isVisible():
            fm_att = QFontMetrics(self.preview_attachment_label.font())
            max_att_w = max(220, curr_w - 120)
            self.preview_attachment_label.setText(fm_att.elidedText(self._raw_attachment_text, Qt.ElideRight, max_att_w))

    def is_hindi(self, text):
        return bool(re.search(r"[\u0900-\u097F]", str(text)))

    def format_tokens(self, n):
        if n >= 1000:
            val = n / 1000.0
            formatted = f"{val:.1f}".rstrip("0").rstrip(".")
            return f"{formatted}k"
        return str(n)

    def set_label_font(self, widget, text, base_size, weight=QFont.Medium):
        if self.is_hindi(text):
            font = QFont(self.font_hin, base_size + 1, weight)
        else:
            font = QFont(self.font_eng, base_size, weight)
            font.setLetterSpacing(QFont.PercentageSpacing, 101)
        widget.setFont(font)

    def render_thought_now(self):
        text = self._current_thought_text
        stable_rgb = self.COLOR_MAP.get(self.current_action_type, [191, 90, 242])
        if not text:
            self.thought_viewer.set_markdown_content("", self.font_eng, 12, stable_rgb)
            return
        use_hin = self.is_hindi(text)
        fam = self.font_hin if use_hin else self.font_eng
        pt = 13 if use_hin else 12
        self.thought_viewer.set_markdown_content(text, fam, pt, stable_rgb)

    def schedule_thought_render(self, clean_thought: str):
        if not self.thought_render_timer.isActive():
            self._current_thought_text = clean_thought
            self.render_thought_now()
            self.thought_render_timer.start()
        else:
            self._pending_thought_text = clean_thought

    def flush_pending_thought(self):
        if self._pending_thought_text is not None:
            self._current_thought_text = self._pending_thought_text
            self._pending_thought_text = None
            self.render_thought_now()

    def update_glow_effect(self):
        self.gradient_phase += 0.045
        if self.gradient_phase >= math.pi * 2:
            self.gradient_phase -= math.pi * 2

        self.target_rgb = self.COLOR_MAP.get(self.current_action_type, [191, 90, 242])

        for i in range(3):
            self.current_rgb[i] += (self.target_rgb[i] - self.current_rgb[i]) * 0.16

        self.pulse_dot.set_state(self.current_rgb, self.gradient_phase, self.current_action_type)
        self.container.update_visuals(self.current_rgb, self.gradient_phase)
        if self.obs_card.isVisible():
            self.obs_card.set_accent(self.current_rgb)

    def reset_island_style(self):
        self.current_action_type = "idle"
        self.target_rgb = [110, 110, 120]

    def start_pulse_animation(self):
        if not self.rgb_timer.isActive():
            self.rgb_timer.start(36)

    def keyPressEvent(self, event):
        if self.awaiting_confirmation:
            if event.key() == Qt.Key_Escape:
                self.handle_confirm_decision(False)
                event.accept()
                return
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and (event.modifiers() & Qt.ControlModifier):
                self.handle_confirm_decision(True)
                event.accept()
                return
        super().keyPressEvent(event)

    def calculate_target_geometry(self):
        fixed_width = self.user_width
        base_height = self.user_height
        extra_height = self.get_extra_cards_height(fixed_width)
        fixed_height = base_height + extra_height

        self.layout.activate()
        self.wrapper_layout.activate()
        self.outer_layout.activate()
        min_required_h = self.outer_layout.totalMinimumSize().height()
        fixed_height = max(fixed_height, min_required_h)

        if getattr(self, "custom_pos", None) is not None:
            return QRect(self.custom_pos.x(), self.custom_pos.y(), fixed_width, fixed_height)

        screen = QApplication.primaryScreen().availableGeometry()
        x = (screen.width() - fixed_width) // 2
        y = screen.top() + 18
        return QRect(x, y, fixed_width, fixed_height)

    def show_confirmation_preview(self, data: dict):
        self.hide_timer.stop()
        self.awaiting_confirmation = True
        self.active_confirm_id = data.get("request_id", "")
        platform = str(data.get("platform", "message")).lower()
        self.active_confirm_platform = platform
        recipient = str(data.get("to", ""))
        subject = str(data.get("subject", ""))
        body = str(data.get("body", ""))
        attachments = data.get("attachments") or []

        if not self.rgb_timer.isActive():
            self.rgb_timer.start(36)

        theme_styles = {
            "whatsapp": (
                "WHATSAPP",
                "confirm_whatsapp",
                "rgba(37, 211, 102, 0.22)",
                "rgba(37, 211, 102, 0.65)",
                "#25D366",
                "#1DA851",
            ),
            "telegram": (
                "TELEGRAM",
                "confirm_telegram",
                "rgba(0, 152, 234, 0.22)",
                "rgba(0, 152, 234, 0.65)",
                "#0098EA",
                "#007ACC",
            ),
            "email": (
                "EMAIL",
                "confirm_email",
                "rgba(255, 149, 0, 0.22)",
                "rgba(255, 149, 0, 0.65)",
                "#FF9500",
                "#CC7700",
            ),
        }

        badge_text, action_type, badge_bg, badge_border, btn_bg, btn_hover = theme_styles.get(
            platform,
            ("MESSAGE", "communication", "rgba(50, 215, 75, 0.22)", "rgba(50, 215, 75, 0.65)", "#32D74B", "#28B43E"),
        )

        self.current_action_type = action_type
        self.status_tag.setText(f"Approve {badge_text.capitalize()}")
        self.detail_pill.hide()

        self.preview_badge.setText(badge_text)
        self.preview_badge.setStyleSheet(f"""
            QLabel {{
                color: #FFFFFF;
                background-color: {badge_bg};
                border: 1px solid {badge_border};
                border-radius: 6px;
                padding: 2px 8px;
                letter-spacing: 0.7px;
            }}
        """)

        self.send_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {btn_bg};
                color: #FFFFFF;
                border: none;
                border-radius: 9px;
            }}
            QPushButton:hover {{
                background-color: {btn_hover};
            }}
            QPushButton:pressed {{
                background-color: rgba(255, 255, 255, 0.25);
            }}
        """)

        self._raw_to_text = f"To: {recipient}"
        self.set_label_font(self.preview_to_label, self._raw_to_text, 9, QFont.Bold)
        fm = QFontMetrics(self.preview_to_label.font())
        max_to_w = max(180, self.user_width - 220)
        self.preview_to_label.setText(fm.elidedText(self._raw_to_text, Qt.ElideRight, max_to_w))

        if platform == "email":
            self.set_label_font(self.preview_subject_input, subject, 10)
            self.preview_subject_input.setText(subject)
            self.preview_subject_input.show()
        else:
            self.preview_subject_input.clear()
            self.preview_subject_input.hide()

        self.set_label_font(self.preview_body_edit, body, 10, QFont.Normal)
        self.preview_body_edit.setPlainText(body)

        if attachments:
            self._raw_attachment_text = f"Attachment: {', '.join(attachments)}"
            self.set_label_font(self.preview_attachment_label, self._raw_attachment_text, 8, QFont.Bold)
            fm_att = QFontMetrics(self.preview_attachment_label.font())
            max_att_w = max(220, self.user_width - 120)
            self.preview_attachment_label.setText(fm_att.elidedText(self._raw_attachment_text, Qt.ElideRight, max_att_w))
            self.preview_attachment_label.show()
        else:
            self._raw_attachment_text = ""
            self.preview_attachment_label.clear()
            self.preview_attachment_label.hide()

        self.obs_card.hide()
        self.separator.show()
        self.preview_card.show()
        self.show_panel()
        self.activateWindow()
        self.preview_body_edit.setFocus()

    def handle_confirm_decision(self, approved: bool):
        if not self.awaiting_confirmation:
            return

        req_id = self.active_confirm_id
        edited_subject = self.preview_subject_input.text().strip()
        edited_body = self.preview_body_edit.toPlainText().strip()

        self.awaiting_confirmation = False
        self.active_confirm_id = None
        self.preview_card.hide()
        self.preview_subject_input.hide()
        self.preview_attachment_label.hide()
        self.separator.hide()

        if approved:
            self.current_action_type = "communication"
            self.status_tag.setText(f"Sending {self.active_confirm_platform.capitalize()}")
        else:
            self.current_action_type = "idle"
            self.status_tag.setText("Message Cancelled")

        self.zmq_listener.send_decision({
            "request_id": req_id,
            "approved": approved,
            "subject": edited_subject,
            "body": edited_body,
        })

        self.show_panel()

    def show_panel(self):
        self.hide_timer.stop()

        if hasattr(self, "hide_anim_group") and self.hide_anim_group.state() == QPropertyAnimation.Running:
            self.hide_anim_group.stop()

        new_geometry = self.calculate_target_geometry()
        is_fading_in = hasattr(self, "show_anim_group") and self.show_anim_group.state() == QPropertyAnimation.Running

        if not self.isVisible() or (self.windowOpacity() < 1.0 and not is_fading_in):
            if self.resize_anim.state() == QPropertyAnimation.Running:
                self.resize_anim.stop()

            self.target_geometry = new_geometry

            start_geom = QRect(
                new_geometry.x(),
                new_geometry.y() - 24,
                new_geometry.width(),
                new_geometry.height(),
            )
            if not self.isVisible():
                self.setGeometry(start_geom)
                self.setWindowOpacity(0.0)
            else:
                start_geom = self.geometry()

            self.show()
            self.raise_()

            self.show_anim_group = QParallelAnimationGroup(self)

            fade_in = QPropertyAnimation(self, b"windowOpacity")
            fade_in.setDuration(230)
            fade_in.setStartValue(self.windowOpacity())
            fade_in.setEndValue(1.0)
            fade_in.setEasingCurve(QEasingCurve.OutCubic)
            self.show_anim_group.addAnimation(fade_in)

            if not self.is_dragging and not self.is_resizing:
                geom_anim = QPropertyAnimation(self, b"anim_geometry")
                geom_anim.setDuration(290)
                geom_anim.setStartValue(start_geom)
                geom_anim.setEndValue(new_geometry)
                geom_anim.setEasingCurve(QEasingCurve.OutQuint)
                self.show_anim_group.addAnimation(geom_anim)

            self.show_anim_group.start()
        else:
            if self.target_geometry != new_geometry and not is_fading_in:
                self.target_geometry = new_geometry
                if self.resize_anim.state() == QPropertyAnimation.Running:
                    self.resize_anim.stop()

                if self.is_dragging or self.is_resizing:
                    self.setGeometry(new_geometry)
                else:
                    self.resize_anim.setStartValue(self.geometry())
                    self.resize_anim.setEndValue(new_geometry)
                    self.resize_anim.start()

    def hide_panel(self):
        if not self.isVisible():
            return

        self.clear_override_cursor()

        if hasattr(self, "show_anim_group") and self.show_anim_group.state() == QPropertyAnimation.Running:
            self.show_anim_group.stop()
        if self.resize_anim.state() == QPropertyAnimation.Running:
            self.resize_anim.stop()

        self.hide_anim_group = QParallelAnimationGroup(self)

        fade_out = QPropertyAnimation(self, b"windowOpacity")
        fade_out.setDuration(210)
        fade_out.setStartValue(self.windowOpacity())
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.InOutQuad)

        curr_geom = self.geometry()
        end_geom = QRect(curr_geom.x(), curr_geom.y() - 20, curr_geom.width(), curr_geom.height())
        slide_up = QPropertyAnimation(self, b"anim_geometry")
        slide_up.setDuration(240)
        slide_up.setStartValue(curr_geom)
        slide_up.setEndValue(end_geom)
        slide_up.setEasingCurve(QEasingCurve.InCubic)

        self.hide_anim_group.addAnimation(fade_out)
        self.hide_anim_group.addAnimation(slide_up)
        self.hide_anim_group.finished.connect(self.hide)
        self.hide_anim_group.start()

    def resolve_action_display(self, action_raw: str):
        action_map = {
            "THINKING": ("Thinking", "thinking"),
            "search_actions": ("Web Search", "search"),
            "deep_research": ("Deep Research", "deep_task"),
            "email_action": ("Gmail", "confirm_email"),
            "whatsapp_action": ("WhatsApp", "confirm_whatsapp"),
            "telegram_action": ("Telegram", "confirm_telegram"),
            "image_command": ("Image Studio", "image_gen"),
            "clipboard_action": ("Clipboard", "clipboard"),
            "file_operations": ("Files & Code", "file_ops"),
            "file_transfer_action": ("Wi-Fi Transfer", "file_transfer"),
            "mobile_action": ("Mobile Bridge", "mobile"),
            "gui_controller": ("Screen Control", "gui"),
            "system_controller": ("System Control", "workspace"),
            "memory_actions": ("Memory Graph", "memory"),
            "calendar_action": ("Calendar", "calendar"),
            "execute_terminal_command": ("Terminal", "terminal"),
            "run_python_code": ("Python Engine", "python"),
            "ABORT": ("Task Aborted", "deep_task"),
        }

        if not action_raw:
            return "Thinking", "thinking"

        parts = [p.strip() for p in str(action_raw).split("|") if p.strip()]
        if not parts:
            return "Executing", "thinking"

        primary = parts[0]
        base_title, act_type = action_map.get(primary, (primary.replace("_", " ").title(), "thinking"))
        if len(parts) > 1:
            base_title = f"{base_title} +{len(parts) - 1}"
        return base_title, act_type

    def process_status_update(self, status):
        step = status.get("step", 0)
        action = status.get("action", "")
        thought = status.get("thought", "")

        if step == 0 and action in ["idle", ""] and "Initialized" in thought:
            self.reset_island_style()
            self.thought_viewer.reset_scroll_lock()
            return

        if self.awaiting_confirmation:
            tokens = status.get("tokens", 0)
            self.token_label.setText(f"{self.format_tokens(tokens)} tok")
            if step > 0 and self.current_step != step:
                self.phase_label.setText(f"Step {step:02}")
                self.current_step = step
            return

        if status == self.last_status:
            if self.isVisible() and step == 0:
                self.hide_timer.start(4000)
            return

        self.last_status = status
        tokens = status.get("tokens", 0)
        action_detail = status.get("action_detail", "")
        observation = status.get("observation", "")

        is_task_complete = False
        formatted_tokens = self.format_tokens(tokens)
        self.token_label.setText(f"{formatted_tokens} tok")

        if step == 0:
            self.current_step = -1
            self.last_tag_text = ""
            self.current_action_type = "done"
            is_task_complete = True
        else:
            if not self.rgb_timer.isActive():
                self.rgb_timer.start(36)

        if is_task_complete:
            self.phase_label.setText("Done")
            self.status_tag.setText("Task Completed")
            self._raw_detail_text = ""
            self.detail_pill.hide()
        else:
            base_text, self.current_action_type = self.resolve_action_display(action)
            self.status_tag.setText(base_text)

            clean_detail = str(action_detail or "").strip()
            if clean_detail and action != "THINKING":
                self._raw_detail_text = clean_detail
                self.set_label_font(self.detail_pill, clean_detail, 8, QFont.Medium)
                fm = QFontMetrics(self.detail_pill.font())
                max_detail_w = max(110, self.user_width - 365)
                elided_detail = fm.elidedText(clean_detail, Qt.ElideRight, max_detail_w)
                self.detail_pill.setText(elided_detail)
                self.detail_pill.show()
            else:
                self._raw_detail_text = ""
                self.detail_pill.clear()
                self.detail_pill.hide()

            if self.current_step != step:
                if step == 1:
                    self.thought_viewer.reset_scroll_lock()
                self.phase_label.setText(f"Step {step:02}")
                self.current_step = step

        if observation and not is_task_complete:
            clean_obs = observation.replace("Observation:", "").strip()
            if len(clean_obs) > 155:
                clean_obs = clean_obs[:152] + "..."

            if self.obs_label.text() != clean_obs:
                self.set_label_font(self.obs_label, clean_obs, 9, QFont.Normal)
                self.obs_label.setText(clean_obs)
                self.obs_card.set_accent(self.current_rgb)
                self.separator.show()
                self.obs_card.show()
        else:
            if self.obs_card.isVisible():
                self.obs_label.setText("")
                self.separator.hide()
                self.obs_card.hide()

        self.show_panel()

        if thought:
            clean_thought = thought.strip()
            if len(clean_thought) > self.MAX_THOUGHT_CHARS:
                cutoff_idx = len(clean_thought) - self.MAX_THOUGHT_CHARS
                newline_pos = clean_thought.find("\n", cutoff_idx)
                if newline_pos != -1:
                    clean_thought = "...\n" + clean_thought[newline_pos + 1:].strip()
                else:
                    clean_thought = "... " + clean_thought[-self.MAX_THOUGHT_CHARS:]

            self.schedule_thought_render(clean_thought)
        else:
            self.schedule_thought_render("")

        if is_task_complete:
            self.hide_timer.start(4000)
        else:
            self.hide_timer.stop()


def run_panel():
    app = QApplication(sys.argv)
    panel = AgentPanel()
    sys.exit(app.exec_())


if __name__ == "__main__":
    run_panel()