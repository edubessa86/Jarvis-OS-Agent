import sys
import os
import re
import math

from .styles import PREMIUM_SINGLE_LINE_SCROLLBAR_CSS

from PyQt5.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve,
    QPoint, QRect, QParallelAnimationGroup,
    pyqtProperty, QSize, QEvent
)
from PyQt5.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QApplication,
    QFrame, QSizePolicy, QPushButton, QTextEdit, QLineEdit
)
from PyQt5.QtGui import (
    QFont, QFontDatabase, QFontMetrics, QCursor
)

from .ThoughtViewer import ThoughtViewer
from .ZmqListener import AgentZmqListener
from .StatusOrb import StatusOrb
from .IslandContainer import IslandContainer
from .ObservationCard import ObservationCard


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
        self._user_resized = False
        self.MAX_THOUGHT_CHARS = 24000

        self._raw_detail_text = ""
        self._raw_to_text = ""
        self._raw_attachment_text = ""
        self._current_thought_text = ""
        self._pending_thought_text = None

        self._last_sync_width = -1
        self._last_sync_text = None
        self._last_sync_height = -1
        self._last_sync_text_len = 0
        self._viewer_at_max_height = False
        self._last_max_h = -1
        self._last_geom_key = None
        self._last_min_required_h = 0

        _here = os.path.dirname(os.path.abspath(__file__))
        _font_candidates = [
            os.path.join(_here, "Data", "fonts"),
            os.path.join(_here, "..", "..", "Data", "fonts"),
            os.path.join(os.getcwd(), "Data", "fonts"),
            os.path.join(os.getcwd(), "..", "..", "Data", "fonts"),
        ]

        def _load_font(filename: str):
            for base in _font_candidates:
                path = os.path.normpath(os.path.join(base, filename))
                if os.path.exists(path):
                    return QFontDatabase.addApplicationFont(path)
            return -1

        eng_id = _load_font("english.ttf")
        eng_fams = QFontDatabase.applicationFontFamilies(eng_id) if eng_id != -1 else []
        self.font_eng = eng_fams[0] if eng_fams else "Segoe UI"

        hin_id = _load_font("devangri.ttf")
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
        self.thought_render_timer.setInterval(50)
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
        if self.rgb_timer.isActive():
            self.rgb_timer.stop()
        if self.thought_render_timer.isActive():
            self.thought_render_timer.stop()
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
        """)
        self.preview_body_edit.verticalScrollBar().setStyleSheet(PREMIUM_SINGLE_LINE_SCROLLBAR_CSS)
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

    def _is_link_click(self, obj, global_pos):
        if obj is self.thought_viewer or obj is self.thought_viewer.viewport():
            vp = self.thought_viewer.viewport()
            vp_pos = vp.mapFromGlobal(global_pos)
            return bool(self.thought_viewer.anchorAt(vp_pos))
        return False

    def stop_active_geometry_animations(self):
        if self.resize_anim.state() == QPropertyAnimation.Running:
            self.resize_anim.stop()
        if hasattr(self, "show_anim_group") and self.show_anim_group.state() == QPropertyAnimation.Running:
            self.show_anim_group.stop()

    def _invalidate_height_cache(self):
        self._last_sync_width = -1
        self._last_sync_text = None
        self._last_sync_height = -1
        self._last_sync_text_len = 0
        self._viewer_at_max_height = False

    def sync_thought_viewer_height(self, for_width: int) -> int:
        has_extra_card = (
            (self.awaiting_confirmation and self.preview_card.isVisible())
            or (self.obs_card.isVisible() and bool(self.obs_label.text().strip()))
        )
        in_confirm = self.awaiting_confirmation and self.preview_card.isVisible()

        if not self._current_thought_text.strip():
            if has_extra_card:
                self.thought_viewer.hide()
                return 0
            if self._user_resized and not in_confirm:
                available_h = max(28, self.user_height - 116)
                self.thought_viewer.show()
                self.thought_viewer.setFixedHeight(available_h)
                return available_h
            self.thought_viewer.show()
            self.thought_viewer.setFixedHeight(32)
            return 32

        if in_confirm:
            max_h = 88
        elif self._user_resized:
            max_h = max(28, self.user_height - 116)
        else:
            max_h = max(88, self.user_height - 106)

        if max_h != self._last_max_h:
            self._last_max_h = max_h
            self._viewer_at_max_height = False

        if self._user_resized and not in_confirm:
            clamped_h = max(28, max_h)
            self.thought_viewer.show()
            self.thought_viewer.setFixedHeight(clamped_h)
            return clamped_h

        if (self._viewer_at_max_height
                and not in_confirm
                and self._last_sync_width == for_width
                and len(self._current_thought_text) >= self._last_sync_text_len - 100):
            self.thought_viewer.show()
            self.thought_viewer.setFixedHeight(max_h)
            return max_h

        if (self._last_sync_width == for_width
                and self._last_sync_text == self._current_thought_text
                and not in_confirm
                and self._last_sync_height > 0):
            self.thought_viewer.setFixedHeight(self._last_sync_height)
            return self._last_sync_height

        self.thought_viewer.show()
        avail_w = max(200, for_width - 98)
        doc = self.thought_viewer.document()
        doc.setTextWidth(avail_w)
        doc_h = int(math.ceil(doc.size().height())) + 4

        if doc_h >= max_h:
            self._viewer_at_max_height = True
            clamped_h = max_h
        else:
            self._viewer_at_max_height = False
            clamped_h = max(28, doc_h)

        self._last_sync_width = for_width
        self._last_sync_text = self._current_thought_text
        self._last_sync_height = clamped_h
        self._last_sync_text_len = len(self._current_thought_text)

        self.thought_viewer.setFixedHeight(clamped_h)
        if not self.thought_viewer.user_scrolled_up:
            vbar = self.thought_viewer.verticalScrollBar()
            vbar.setValue(vbar.maximum())
        return clamped_h

    def get_extra_cards_height(self, for_width: int) -> int:
        if self.awaiting_confirmation and self.preview_card.isVisible():
            extra_height = 178
            if self.preview_subject_input.isVisible():
                extra_height += 38
            if self.preview_attachment_label.isVisible():
                extra_height += 30
            self.preview_card.setFixedHeight(extra_height)
            return extra_height
        self.preview_card.setMinimumHeight(0)
        self.preview_card.setMaximumHeight(16777215)
        if self.obs_card.isVisible() and self.obs_label.text().strip():
            fm = QFontMetrics(self.obs_label.font())
            avail_w = max(180, for_width - 118)
            bound = fm.boundingRect(0, 0, avail_w, 300, Qt.TextWordWrap, self.obs_label.text())
            obs_h = min(90, max(36, bound.height() + 20))
            self.obs_card.setFixedHeight(obs_h)
            return obs_h
        self.obs_card.setMinimumHeight(0)
        self.obs_card.setMaximumHeight(16777215)
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

        self._invalidate_height_cache()

        if self.awaiting_confirmation and self.preview_card.isVisible():
            thought_h = self.sync_thought_viewer_height(w)
            preview_h = self.get_extra_cards_height(w)
            h = 96 + (10 + thought_h if thought_h > 0 else 0) + 10 + preview_h
            if "top" in self.resize_edge:
                y = start.bottom() - h + 1
            self.user_width = w
        else:
            if "top" in self.resize_edge or "bottom" in self.resize_edge:
                self._user_resized = True
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
            thought_h = self.sync_thought_viewer_height(w)
            h = 96 + (10 + thought_h if thought_h > 0 else 0) + (10 + extra_h if extra_h > 0 else 0)
            if "top" in self.resize_edge:
                y = start.bottom() - h + 1

        self.custom_pos = QPoint(x, y)

        new_rect = QRect(x, y, w, h)
        self.target_geometry = new_rect
        self.setGeometry(new_rect)
        self.refresh_elided_labels()

    def reset_to_default_size(self):
        self.user_width = self.DEFAULT_WIDTH
        self.user_height = self.DEFAULT_HEIGHT
        self._user_resized = False
        self._invalidate_height_cache()
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
                if self.is_interactive_child(obj) or self._is_link_click(obj, global_pos):
                    self.clear_override_cursor()
                else:
                    edge = self.detect_resize_edge(local_pos)
                    self.set_edge_cursor(edge)

        elif etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._interaction_origin = self.geometry().topLeft()
            local_pos, global_pos = self._resolve_mouse_coords(obj, event)

            if self._is_link_click(obj, global_pos):
                return super().eventFilter(obj, event)

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
        self._pending_thought_text = clean_thought
        if not self.thought_render_timer.isActive():
            self.thought_render_timer.start()

    def flush_pending_thought(self):
        if self._pending_thought_text is None:
            return
        self._current_thought_text = self._pending_thought_text
        self._pending_thought_text = None
        self.render_thought_now()
        if not self.hide_timer.isActive():
            if self.isVisible() or self._current_thought_text.strip():
                self.show_panel()

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
        self._invalidate_height_cache()

    def start_pulse_animation(self):
        if not self.rgb_timer.isActive():
            self.rgb_timer.start(50)

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
        thought_h = self.sync_thought_viewer_height(fixed_width)
        extra_height = self.get_extra_cards_height(fixed_width)

        fixed_height = 96 + (10 + thought_h if thought_h > 0 else 0) + (10 + extra_height if extra_height > 0 else 0)

        geom_key = (
            fixed_width, thought_h, extra_height,
            self.preview_card.isVisible(),
            self.obs_card.isVisible(),
            bool(self._current_thought_text.strip()),
        )
        if geom_key != self._last_geom_key:
            self.layout.activate()
            self.wrapper_layout.activate()
            self.outer_layout.activate()
            self._last_min_required_h = self.outer_layout.totalMinimumSize().height()
            self._last_geom_key = geom_key
        fixed_height = max(fixed_height, self._last_min_required_h)

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
            self.rgb_timer.start(50)

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
        self.separator.hide()
        self.preview_card.show()
        self._invalidate_height_cache()
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
        self._invalidate_height_cache()

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

        if not self.rgb_timer.isActive():
            self.rgb_timer.start(50)

        new_geometry = self.calculate_target_geometry()
        is_fading_in = hasattr(self, "show_anim_group") and self.show_anim_group.state() == QPropertyAnimation.Running

        if (self.isVisible()
                and not is_fading_in
                and self.windowOpacity() >= 1.0
                and new_geometry == self.geometry()):
            return

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
        self.hide_anim_group.finished.connect(self._on_hidden_finished)
        self.hide_anim_group.start()

    def _on_hidden_finished(self):
        self.hide()
        if self.rgb_timer.isActive():
            self.rgb_timer.stop()

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
        if not isinstance(status, dict):
            return

        def _safe_str(v):
            if v is None:
                return ""
            return v if isinstance(v, str) else str(v)

        def _safe_int(v, default=0):
            try:
                return int(v) if v is not None else default
            except (ValueError, TypeError):
                return default

        step = _safe_int(status.get("step"), 0)
        action = _safe_str(status.get("action"))
        thought = _safe_str(status.get("thought"))
        tokens = _safe_int(status.get("tokens"), 0)

        if step == 0 and action in ["idle", ""] and "Initialized" in thought:
            self.reset_island_style()
            self.thought_viewer.reset_scroll_lock()
            return

        if self.awaiting_confirmation:
            self.token_label.setText(f"{self.format_tokens(tokens)} tok")
            if step > 0 and self.current_step != step:
                self.phase_label.setText(f"Step {step:02}")
                self.current_step = step
            if thought:
                clean_thought = thought.strip()
                if len(clean_thought) > self.MAX_THOUGHT_CHARS:
                    cutoff_idx = len(clean_thought) - self.MAX_THOUGHT_CHARS
                    newline_pos = clean_thought.find("\n", cutoff_idx)
                    if newline_pos != -1:
                        clean_thought = "...\n" + clean_thought[newline_pos + 1:].strip()
                    else:
                        clean_thought = "... " + clean_thought[-self.MAX_THOUGHT_CHARS:]
                self._current_thought_text = clean_thought
                self._pending_thought_text = None
                self.render_thought_now()
                self.show_panel()
            return

        if status == self.last_status:
            if self.isVisible() and step == 0:
                self.hide_timer.start(4000)
            return

        self.last_status = status
        action_detail = _safe_str(status.get("action_detail"))
        observation = _safe_str(status.get("observation"))

        is_task_complete = False
        formatted_tokens = self.format_tokens(tokens)
        self.token_label.setText(f"{formatted_tokens} tok")

        if step == 0:
            self.current_step = -1
            self.last_tag_text = ""
            self.current_action_type = "done"
            is_task_complete = True
            self._invalidate_height_cache()
        else:
            if not self.rgb_timer.isActive():
                self.rgb_timer.start(50)

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
                    self._invalidate_height_cache()
                self.phase_label.setText(f"Step {step:02}")
                self.current_step = step

        if observation and not is_task_complete:
            clean_obs = observation.replace("Observation:", "").strip()
            if len(clean_obs) > 155:
                clean_obs = clean_obs[:152] + "..."

            if self.obs_label.text() != clean_obs or not self.obs_card.isVisible():
                self.set_label_font(self.obs_label, clean_obs, 9, QFont.Normal)
                self.obs_label.setText(clean_obs)
                self.obs_card.set_accent(self.current_rgb)
                self.separator.hide()
                self.obs_card.show()
        else:
            if self.obs_card.isVisible():
                self.obs_label.setText("")
                self.separator.hide()
                self.obs_card.hide()

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

        if not self.isVisible():
            self.show_panel()

        if is_task_complete:
            if self.thought_render_timer.isActive():
                self.thought_render_timer.stop()
            self._pending_thought_text = None
            self.hide_timer.start(4000)
        else:
            self.hide_timer.stop()


def run_panel():
    app = QApplication(sys.argv)
    panel = AgentPanel()
    sys.exit(app.exec_())


if __name__ == "__main__":
    run_panel()