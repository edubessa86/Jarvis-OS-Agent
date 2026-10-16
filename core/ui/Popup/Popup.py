import os
import sys

os.environ["QT_LOGGING_RULES"] = "qt.qpa.window=false;qt.text.font.db=false;default.warning=false"

from PyQt5.QtCore import qInstallMessageHandler
from PyQt5.QtWidgets import QApplication
from PopupUI import TypingPopup


def suppress_qt_warnings(mode, context, message):
    suppressed = (
        "OpenType support missing",
        "QColor",
        "Unable to set geometry",
        "Resulting geometry",
        "QWindowsWindow::setGeometry",
    )
    if any(s in message for s in suppressed):
        return


qInstallMessageHandler(suppress_qt_warnings)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    popup = TypingPopup()
    sys.exit(app.exec_())