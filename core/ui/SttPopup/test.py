# test.py
"""
Comprehensive test suite for STTPopup (fixed).
Run:  python test.py
"""

import os
import sys
import json
import time
import socket
import traceback

os.environ["QT_LOGGING_RULES"] = "qt.qpa.window=false;qt.text.font.db=false;default.warning=false"

from PyQt5.QtCore import QTimer, QObject
from PyQt5.QtWidgets import QApplication

# --- Make sure we can import SttPopup even if run from another cwd ---
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Sanity check for the popup file
POPUP_FILE = os.path.join(HERE, "SttPopup.py")
if not os.path.exists(POPUP_FILE):
    print(f"[FATAL] SttPopup.py not found at: {POPUP_FILE}")
    print("        Make sure SttPopup.py sits next to test.py.")
    sys.exit(1)

from SttPopup import STTPopup

UDP_HOST = "127.0.0.1"
UDP_PORT = 5556


# ==================================================================
#                  GLOBAL EXCEPTION HOOK (debugging)
# ==================================================================

def _excepthook(exc_type, exc_value, exc_tb):
    print("\n[UNHANDLED EXCEPTION]")
    traceback.print_exception(exc_type, exc_value, exc_tb)
    # Don't kill the app; let it keep going so we can see more
    # (set to sys.__excepthook__ if you want the default abort)

sys.excepthook = _excepthook


# ==================================================================
#                        UDP SENDER
# ==================================================================

class UDPSender:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, status: str, text: str = ""):
        payload = json.dumps({"status": status, "text": text}).encode("utf-8")
        self.sock.sendto(payload, (UDP_HOST, UDP_PORT))

    def send_raw(self, raw: str):
        self.sock.sendto(raw.encode("utf-8"), (UDP_HOST, UDP_PORT))

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


# ==================================================================
#                        TEST CASES
# ==================================================================

TEST_CASES = [
    {
        "name": "01 – Basic listening pulse",
        "steps": [
            (0,    "idle",        ""),
            (200,  "listening",   ""),
            (800,  "listening",   "hello world this is a test"),
            (1600, "idle",        ""),
        ],
        "duration": 2400,
    },
    {
        "name": "02 – Full pipeline (listen→understand→transcribe)",
        "steps": [
            (0,    "listening",     ""),
            (200,  "listening",     "play some music"),
            (800,  "understanding", "play some music"),
            (1800, "transcribed",   "play some music"),
            (4600, "idle",          ""),
        ],
        "duration": 5200,
    },
    {
        "name": "03 – Long text truncation",
        "steps": [
            (0,    "listening",   "one two three four five six seven eight nine ten eleven twelve"),
            (1500, "transcribed", "one two three four five six seven eight nine ten eleven twelve"),
            (4200, "idle",        ""),
        ],
        "duration": 4800,
    },
    {
        "name": "04 – Hindi text rendering",
        "steps": [
            (0,    "listening",   "नमस्ते दुनिया यह एक परीक्षण है"),
            (1200, "transcribed", "नमस्ते दुनिया यह एक परीक्षण है"),
            (3900, "idle",        ""),
        ],
        "duration": 4400,
    },
    {
        "name": "05 – Mixed English + Hindi",
        "steps": [
            (0,    "listening",   "Please set alarm for सुबह सात बजे"),
            (1300, "transcribed", "Please set alarm for सुबह सात बजे"),
            (4000, "idle",        ""),
        ],
        "duration": 4600,
    },
    {
        "name": "06 – Rapid transitions stress",
        "steps": [
            (0,    "listening",     "a"),
            (100,  "understanding", "a b"),
            (200,  "listening",     "a b c"),
            (300,  "understanding", "a b c d"),
            (400,  "listening",     "a b c d e"),
            (500,  "understanding", "a b c d e f"),
            (600,  "transcribed",   "a b c d e f g"),
            (3000, "idle",          ""),
        ],
        "duration": 3600,
    },
    {
        "name": "07 – Transcribed hold window",
        "steps": [
            (0,    "listening",   "testing hold"),
            (500,  "transcribed", "testing hold"),
            (700,  "idle",        ""),
            (2000, "idle",        ""),
            (3500, "idle",        ""),
        ],
        "duration": 4200,
    },
    {
        "name": "08 – Empty text in all states",
        "steps": [
            (0,    "listening",     ""),
            (400,  "understanding", ""),
            (800,  "transcribed",   ""),
            (3000, "idle",          ""),
        ],
        "duration": 3600,
    },
    {
        "name": "09 – Auto capitalization (english)",
        "steps": [
            (0,    "listening",   "lowercase sentence here"),
            (1000, "transcribed", "lowercase sentence here"),
            (3500, "idle",        ""),
        ],
        "duration": 4000,
    },
    {
        "name": "10 – Malformed JSON ignored",
        "steps": [
            (0,    "listening",  "before bad packet"),
            (400,  "__RAW__",    '{"status": "listen'),
            (800,  "__RAW__",    'not even json'),
            (1200, "transcribed", "after bad packets"),
            (3700, "idle",        ""),
        ],
        "duration": 4300,
    },
    {
        "name": "11 – Unknown status value",
        "steps": [
            (0,    "listening",    "hi"),
            (500,  "bogus_state",  "???"),
            (1000, "understanding","hi"),
            (1500, "transcribed",  "hi"),
            (4000, "idle",         ""),
        ],
        "duration": 4600,
    },
    {
        "name": "12 – Incremental transcript updates",
        "steps": [
            (0,    "listening",   "hey"),
            (300,  "listening",   "hey there"),
            (600,  "listening",   "hey there how"),
            (900,  "listening",   "hey there how are"),
            (1200, "listening",   "hey there how are you"),
            (1600, "transcribed", "hey there how are you"),
            (4000, "idle",        ""),
        ],
        "duration": 4600,
    },
    {
        "name": "13 – Very long single line",
        "steps": [
            (0,    "listening",   " ".join(f"word{i}" for i in range(50))),
            (1500, "transcribed", " ".join(f"word{i}" for i in range(50))),
            (4200, "idle",        ""),
        ],
        "duration": 4800,
    },
]


# ==================================================================
#                          TEST RUNNER
# ==================================================================

class TestRunner(QObject):
    def __init__(self, popup, app):
        super().__init__()
        self.popup = popup
        self.app = app
        self.sender = UDPSender()
        self.case_index = 0
        self.results = []
        self.current_start = 0.0
        self.total_start = time.time()

        self.step_timer = QTimer(self)
        self.step_timer.setSingleShot(True)
        self.step_timer.timeout.connect(self._safe(self._run_next_step))

        self.pending_steps = []
        self.step_idx = 0

        self.finish_timer = QTimer(self)
        self.finish_timer.setSingleShot(True)
        self.finish_timer.timeout.connect(self._safe(self._finalize_case))

    # ---- wrap slot callbacks so one exception doesn't kill the loop ----
    def _safe(self, fn):
        def wrapper(*a, **kw):
            try:
                fn(*a, **kw)
            except Exception:
                print("\n[RUNNER EXCEPTION]")
                traceback.print_exc()
                # keep going
                QTimer.singleShot(300, self._safe(self._finalize_case))
        return wrapper

    def start(self):
        print("=" * 72)
        print("  STT Popup — Full Feature Test Suite (UDP 127.0.0.1:5556)")
        print("=" * 72)
        print(f"Total test cases: {len(TEST_CASES)}")
        print(f"Popup file   : {POPUP_FILE}")
        print("Popup will appear at bottom-center of your screen.\n")
        QTimer.singleShot(500, self._safe(self._run_case))

    def _run_case(self):
        if self.case_index >= len(TEST_CASES):
            return self._finish()

        case = TEST_CASES[self.case_index]
        print(f"\n▶ [{case['name']}]  ({len(case['steps'])} steps, {case['duration']}ms)")

        self.current_start = time.time()
        self.pending_steps = list(case["steps"])
        self.step_idx = 0

        # force popup to initial idle so animations reset
        self.sender.send("idle", "")

        # schedule first step after a small delay
        self.step_timer.start(200)

        # schedule end-of-case
        self.finish_timer.start(case["duration"] + 300)

    def _run_next_step(self):
        if self.step_idx >= len(self.pending_steps):
            return

        delay, status, text = self.pending_steps[self.step_idx]
        self.step_idx += 1

        if status == "__RAW__":
            self.sender.send_raw(text)
            print(f"   · {delay:>5}ms  RAW payload: {text!r}")
        else:
            self.sender.send(status, text)
            preview = (text[:34] + "…") if len(text) > 34 else text
            print(f"   · {delay:>5}ms  {status:<14}  {preview!r}")

        if self.step_idx < len(self.pending_steps):
            next_delay = self.pending_steps[self.step_idx][0]
            wait = max(10, next_delay - delay)
            self.step_timer.start(wait)

    def _finalize_case(self):
        self.step_timer.stop()
        self.finish_timer.stop()

        elapsed = time.time() - self.current_start
        ok = True
        reason = "ok"

        try:
            state = self.popup.current_state
            visible = self.popup.isVisible()
            opacity = self.popup.windowOpacity()
        except Exception as e:
            ok = False
            reason = f"introspection failed: {e}"
            state = visible = opacity = None

        self.results.append({
            "name": TEST_CASES[self.case_index]["name"],
            "ok": ok,
            "reason": reason,
            "elapsed": elapsed,
            "state": state,
            "visible": visible,
            "opacity": opacity if opacity is not None else 0.0,
        })

        tag = "✔" if ok else "✘"
        print(f"   {tag} {reason}  ({elapsed:.2f}s)  "
              f"state={state} visible={visible} "
              f"opacity={(opacity if opacity is not None else 0):.2f}")

        self.case_index += 1
        QTimer.singleShot(500, self._safe(self._run_case))

    def _finish(self):
        print("\n▶ [final] exit command")
        self.sender.send("exit", "")
        QTimer.singleShot(700, self._safe(self._print_summary))

    def _print_summary(self):
        total = time.time() - self.total_start
        passed = sum(1 for r in self.results if r["ok"])
        failed = len(self.results) - passed

        print("\n" + "=" * 72)
        print("  TEST SUMMARY")
        print("=" * 72)
        for r in self.results:
            tag = "PASS" if r["ok"] else "FAIL"
            print(f"  [{tag}]  {r['name']:48s}  {r['elapsed']:.2f}s  "
                  f"state={r['state']} vis={r['visible']}")
        print("-" * 72)
        print(f"  Passed: {passed}/{len(self.results)}   Failed: {failed}")
        print(f"  Total wall time: {total:.2f}s")
        print("=" * 72)

        self.sender.close()
        QTimer.singleShot(500, self.app.quit)


# ==================================================================
#                             MAIN
# ==================================================================

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("STT Popup Tester")

    # ⭐ THE CRITICAL FIX ⭐
    # STTPopup is a Qt.Tool window that starts hidden.
    # Without this flag, Qt fires lastWindowClosed when no visible
    # primary window exists -> app.exec_() returns immediately.
    app.setQuitOnLastWindowClosed(False)

    popup = STTPopup()
    runner = TestRunner(popup, app)
    runner.start()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()