import os

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont, QFontDatabase


_family_cache = {}
_loaded_files = set()
_substitutions_installed = False
_ui_family = "Segoe UI"
_hindi_family = "Nirmala UI"
_code_family = "Consolas"


def _font_dirs():
    _here = os.path.dirname(os.path.abspath(__file__))
    return [
        os.path.normpath(os.path.join(_here, "..", "Data", "fonts")),
        os.path.normpath(os.path.join(_here, "..", "..", "Data", "fonts")),
        os.path.normpath(os.path.join(_here, "..", "..", "..", "Data", "fonts")),
        os.path.normpath(os.path.join(_here, "..", "..", "..", "..", "Data", "fonts")),
    ]


def _qt_ready():
    return QApplication.instance() is not None


def _load_font(filename):
    if filename in _loaded_files:
        return _family_cache.get(filename, "")
    if not _qt_ready():
        return ""
    _loaded_files.add(filename)
    for base in _font_dirs():
        path = os.path.normpath(os.path.join(base, filename))
        if os.path.exists(path):
            try:
                font_id = QFontDatabase.addApplicationFont(path)
            except Exception:
                font_id = -1
            if font_id != -1:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    _family_cache[filename] = families[0]
                    return families[0]
    _family_cache[filename] = ""
    return ""


def _initialize():
    global _ui_family, _hindi_family, _code_family, _substitutions_installed

    if _substitutions_installed:
        return

    if not _qt_ready():
        return

    eng = _load_font("english.ttf")
    _ui_family = eng if eng else "Segoe UI"

    hin = _load_font("devangri.ttf")
    _hindi_family = hin if hin else "Nirmala UI"

    code = _load_font("code.ttf")
    _code_family = code if code else "Consolas"

    try:
        ui_chain = [_hindi_family, "Nirmala UI", "Segoe UI Emoji", "Segoe UI", "Mangal"]
        QFont.insertSubstitutions(_ui_family, ui_chain)
        QFont.insertSubstitutions("MS Shell Dlg 2", ui_chain)
        QFont.insertSubstitutions("Sansation", ui_chain)
        QFont.insertSubstitutions(
            _code_family,
            [_hindi_family, "Nirmala UI", "Segoe UI Emoji", "Consolas", "Courier New"],
        )
    except Exception:
        pass

    _substitutions_installed = True


def get_ui_font():
    _initialize()
    return _ui_family


def get_hindi_font():
    _initialize()
    return _hindi_family


def get_code_font():
    _initialize()
    return _code_family


def install_substitutions():
    _initialize()