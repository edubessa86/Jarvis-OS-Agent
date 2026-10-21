import sys
import os

os.environ["QT_LOGGING_RULES"] = "qt.qpa.window=false;qt.text.font.db=false;default.warning=false"

from PyQt5.QtCore import qInstallMessageHandler


PREMIUM_SINGLE_LINE_SCROLLBAR_CSS = """
    QScrollBar:vertical {
        border: none;
        background: transparent;
        width: 6px;
        margin: 0px;
    }
    QScrollBar::handle:vertical {
        background: rgba(255, 255, 255, 0.22);
        border-radius: 3px;
        min-height: 20px;
    }
    QScrollBar::handle:vertical:hover {
        background: rgba(191, 90, 242, 0.65);
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        border: none;
        background: none;
        height: 0px;
        width: 0px;
    }
    QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical {
        border: none;
        background: none;
        image: none;
        height: 0px;
        width: 0px;
    }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
        background: none;
    }
"""


def qt_message_handler(mode, context, message):
    suppressed = (
        "Unable to set geometry",
        "Resulting geometry",
        "OpenType support missing",
        "Cannot find font directory",
        "Qt no longer ships fonts",
        "does not support raise()",
        "does not support setting window opacity",
    )
    if any(s in message for s in suppressed):
        return
    if sys.__stderr__ is not None:
        sys.__stderr__.write(f"{message}\n")


qInstallMessageHandler(qt_message_handler)