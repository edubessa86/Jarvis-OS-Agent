#!/usr/bin/env python3
"""
agent_panel_test.py
====================
Test harness for AgentPanel.

Runs the real AgentPanel and a control popup that publishes
test scenarios over ZMQ (ports 5555/5556/5557).

Usage:
    python agent_panel_test.py

Make sure agent_panel.py is in the same directory.
"""

import sys
import json
import time
import threading

import zmq

from PyQt5.QtCore import Qt, QObject, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QLabel,
    QScrollArea, QTextEdit, QFrame
)
from PyQt5.QtGui import QFont

from agent_panel import AgentPanel


# ═══════════════════════════════════════════════════════════════════════
# Test Content Samples
# ═══════════════════════════════════════════════════════════════════════

SIMPLE_THOUGHT = (
    "Analyzing your request... Looking at the input parameters and "
    "determining the best approach to solve this task efficiently."
)

LONG_MARKDOWN = "\n".join([
    "## Deep Analysis Report",
    "",
    "I need to thoroughly analyze this request. Let me break it down **step by step**.",
    "",
    "### Step 1: Understanding Requirements",
    "",
    "The user wants me to test **every single feature** of the agent panel. This includes:",
    "- Long reasoning text rendering",
    "- **Markdown** formatting (bold, *italic*, ***bold italic***)",
    "- Code blocks with syntax highlighting",
    "- Inline `code` snippets",
    "- Headings of various levels",
    "- **Bullet points** and numbered lists",
    "- Blockquotes and horizontal rules",
    "",
    "> **Note**: This is a blockquote to test the visual presentation of quoted content.",
    "",
    "### Step 2: Code Block Testing",
    "",
    "Here is a **Python** example:",
    "",
    "```python",
    "import numpy as np",
    "from dataclasses import dataclass",
    "",
    "@dataclass",
    "class AgentState:",
    "    name: str",
    "    step: int = 0",
    "    active: bool = True",
    "",
    "    def advance(self):",
    "        self.step += 1",
    "        return f\"Step {self.step} complete\"",
    "",
    "def calculate_loss(predictions, targets):",
    "    return np.mean((predictions - targets) ** 2)",
    "",
    "state = AgentState(name=\"jarvis\")",
    "print(calculate_loss(np.array([1, 2, 3]), np.array([1.1, 2.1, 3.1])))",
    "```",
    "",
    "Now a **JavaScript** snippet:",
    "",
    "```javascript",
    "async function fetchData(url) {",
    "    const response = await fetch(url);",
    "    if (!response.ok) throw new Error(`HTTP ${response.status}`);",
    "    return await response.json();",
    "}",
    "",
    "const data = await fetchData('/api/users');",
    "console.log(`Fetched ${data.length} users`);",
    "```",
    "",
    "And a **bash** script:",
    "",
    "```bash",
    "#!/bin/bash",
    "for file in *.py; do",
    "    python -m py_compile \"$file\" && echo \"$file compiles\"",
    "done",
    "```",
    "",
    "### Step 3: List Testing",
    "",
    "Numbered items:",
    "1. First item with `inline code`",
    "2. Second item with **bold text**",
    "3. Third item with *italic text*",
    "4. Fourth item with ***both styles***",
    "5. Fifth item with a very long line that should wrap properly when it exceeds the available width of the container",
    "",
    "Bullet points:",
    "- Alpha feature",
    "- Beta feature",
    "- Gamma feature",
    "  - Nested item one",
    "  - Nested item two",
    "- Delta feature",
    "",
    "### Step 4: Long Token Wrapping Test",
    "",
    "Here is a very long unbreakable token to test wrapping:",
    "`aVeryLongIdentifierThatShouldWrapWhenItExceedsTheAvailableContainerWidthBecauseItHasNoSpacesInItAtAllWhatsoever123456789`",
    "",
    "### Step 5: Conclusion",
    "",
    "After this thorough analysis, I can confirm the rendering pipeline:",
    "1. Handles **all** markdown features",
    "2. Syntactically highlights code correctly",
    "3. Wraps long content without breaking layout",
    "4. Maintains visual hierarchy with headings",
    "",
    "---",
    "",
    "*End of analysis. Proceeding to next step.*",
    "",
    "**Final verdict**: All systems nominal.",
])

HINDI_THOUGHT = "\n".join([
    "## विश्लेषण रिपोर्ट",
    "",
    "मैं इस अनुरोध का **गहन विश्लेषण** कर रहा हूँ।",
    "",
    "### चरण 1: आवश्यकताओं को समझना",
    "",
    "- उपयोगकर्ता की आवश्यकता स्पष्ट है",
    "- सभी विवरणों की जाँच करनी है",
    "- *सटीकता* महत्वपूर्ण है",
    "",
    "### चरण 2: कार्यान्वयन",
    "",
    "`print(\"नमस्ते दुनिया\")` का उपयोग करके परीक्षण करें।",
    "",
    "> **ध्यान दें**: यह हिंदी पाठ का परीक्षण है।",
    "",
    "अंत में, सब कुछ ठीक है।",
])


# ═══════════════════════════════════════════════════════════════════════
# ZMQ Test Controller
# ═══════════════════════════════════════════════════════════════════════

class TestController(QObject):
    """Publishes test scenarios to the AgentPanel via ZMQ."""
    decision_received = pyqtSignal(dict)
    log_message = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.context = zmq.Context()

        # Status PUB -> panel SUB on 5555
        self.pub = self.context.socket(zmq.PUB)
        self.pub.setsockopt(zmq.LINGER, 0)
        try:
            self.pub.bind("tcp://127.0.0.1:5555")
            self.log_message.emit("PUB bound on 5555")
        except Exception:
            self.pub.connect("tcp://127.0.0.1:5555")
            self.log_message.emit("PUB connected to 5555")

        # Confirmation PUSH -> panel PULL on 5556
        self.confirm_push = self.context.socket(zmq.PUSH)
        self.confirm_push.setsockopt(zmq.LINGER, 200)
        self.confirm_push.setsockopt(zmq.SNDTIMEO, 500)
        try:
            self.confirm_push.bind("tcp://127.0.0.1:5556")
            self.log_message.emit("Confirm PUSH bound on 5556")
        except Exception:
            self.confirm_push.connect("tcp://127.0.0.1:5556")
            self.log_message.emit("Confirm PUSH connected to 5556")

        # Decision PULL <- panel PUSH on 5557
        self.confirm_pull = self.context.socket(zmq.PULL)
        self.confirm_pull.setsockopt(zmq.LINGER, 0)
        try:
            self.confirm_pull.bind("tcp://127.0.0.1:5557")
            self.log_message.emit("Decision PULL bound on 5557")
        except Exception:
            self.confirm_pull.connect("tcp://127.0.0.1:5557")
            self.log_message.emit("Decision PULL connected to 5557")

        self.running = True
        self._poll_thread = threading.Thread(
            target=self._poll_decisions, daemon=True
        )
        self._poll_thread.start()

    def _poll_decisions(self):
        poller = zmq.Poller()
        poller.register(self.confirm_pull, zmq.POLLIN)
        while self.running:
            try:
                socks = dict(poller.poll(100))
                if self.confirm_pull in socks:
                    try:
                        data = self.confirm_pull.recv_json(flags=zmq.NOBLOCK)
                        self.decision_received.emit(data)
                        self.log_message.emit(
                            f"<- Decision: {json.dumps(data)}"
                        )
                    except zmq.Again:
                        pass
            except Exception as e:
                self.log_message.emit(f"Poll error: {e}")

    def send_status(self, data):
        msg = "AGENT_UPDATE " + json.dumps(data)
        try:
            self.pub.send_string(msg, flags=zmq.NOBLOCK)
            self.log_message.emit(
                f"-> step={data.get('step')} | {data.get('action')} "
                f"| {data.get('tokens')} tok"
            )
        except zmq.Again:
            self.log_message.emit("X PUB send failed (no subscriber)")

    def send_confirm(self, data):
        try:
            self.confirm_push.send_json(data, flags=zmq.NOBLOCK)
            self.log_message.emit(
                f"-> confirm: {data.get('platform')} -> {data.get('to')}"
            )
        except zmq.Again:
            self.log_message.emit("X Confirm send failed")

    def shutdown(self):
        self.running = False
        time.sleep(0.12)
        try:
            self.pub.close()
            self.confirm_push.close()
            self.confirm_pull.close()
            self.context.term()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════
# Test Control Panel UI
# ═══════════════════════════════════════════════════════════════════════

class TestPanel(QWidget):
    def __init__(self, controller: TestController, agent_panel: AgentPanel):
        super().__init__()
        self.controller = controller
        self.agent_panel = agent_panel
        self._stream_idx = 0
        self._stream_timer = None

        self.setWindowTitle("Agent Panel - Test Controller")
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Window)
        self.resize(430, 760)

        self.setStyleSheet("""
            QWidget {
                background-color: #16171f;
                color: #e8e8f0;
                font-family: 'Segoe UI', sans-serif;
            }
            QPushButton {
                background-color: #24252f;
                border: 1px solid #3a3b46;
                border-radius: 6px;
                padding: 8px 12px;
                text-align: left;
                font-size: 10pt;
                color: #e8e8f0;
            }
            QPushButton:hover {
                background-color: #31323d;
                border: 1px solid #bf5af2;
            }
            QPushButton:pressed {
                background-color: #4a4b5a;
            }
            QLabel { color: #e8e8f0; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)

        header = QLabel("Agent Panel Test Suite")
        header.setFont(QFont("Segoe UI", 14, QFont.Bold))
        header.setStyleSheet("color: #bf5af2; padding: 2px;")
        root.addWidget(header)

        subtitle = QLabel(
            "Click any test below. If nothing shows, wait 1s for ZMQ to connect."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #8a8ba0; font-size: 9pt; padding: 0 2px 6px 2px;")
        root.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        # --- Content Tests ---
        self._section(cl, "Content Rendering")
        self._button(cl, "1. Simple Thinking", self.test_simple)
        self._button(cl, "2. Long Reasoning + Markdown + Code Blocks", self.test_long_reasoning)
        self._button(cl, "3. Hindi Text Rendering", self.test_hindi)
        self._button(cl, "4. Streaming (5 rapid updates)", self.test_stream)

        # --- Action Types ---
        self._section(cl, "Action Type Indicators")
        self._button(cl, "5. Web Search", self.test_web_search)
        self._button(cl, "6. Deep Research", self.test_deep_research)
        self._button(cl, "7. Python Engine", self.test_python)
        self._button(cl, "8. Terminal Command", self.test_terminal)
        self._button(cl, "9. File Operations", self.test_file_ops)
        self._button(cl, "10. Image Studio", self.test_image_gen)
        self._button(cl, "11. Memory Graph", self.test_memory)
        self._button(cl, "12. Multi-Action Combo (3 actions)", self.test_combo)

        # --- Confirmations ---
        self._section(cl, "Confirmation Previews")
        self._button(cl, "13. WhatsApp Message", self.test_whatsapp)
        self._button(cl, "14. Telegram Message", self.test_telegram)
        self._button(cl, "15. Email with Attachments", self.test_email)

        # --- UI & State ---
        self._section(cl, "UI & State")
        self._button(cl, "16. Show Observation Card", self.test_observation)
        self._button(cl, "17. Task Complete", self.test_complete)
        self._button(cl, "18. Reset / Idle", self.test_reset)
        self._button(cl, "19. Force Wide Resize (1000x320)", self.test_wide)
        self._button(cl, "20. Force Tall Resize (580x700)", self.test_tall)

        # --- Log ---
        log_label = QLabel("ZMQ Event Log")
        log_label.setStyleSheet(
            "color: #bf5af2; font-weight: bold; padding-top: 6px;"
        )
        root.addWidget(log_label)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setFixedHeight(130)
        self.log_area.setStyleSheet("""
            QTextEdit {
                background: #0a0b11;
                color: #8be9fd;
                border: 1px solid #2a2b36;
                border-radius: 6px;
                padding: 6px;
                font-family: 'Consolas', monospace;
                font-size: 9pt;
            }
        """)
        root.addWidget(self.log_area)

    # ---- Helpers -------------------------------------------------------

    def _section(self, layout, title):
        lbl = QLabel(title)
        lbl.setStyleSheet(
            "color: #bf5af2; font-weight: bold; font-size: 10pt; "
            "padding: 10px 2px 2px 2px;"
        )
        layout.addWidget(lbl)

    def _button(self, layout, text, cb):
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(cb)
        layout.addWidget(btn)

    def log(self, msg):
        self.log_area.append(msg)

    def on_decision(self, data):
        approved = data.get("approved")
        status = "APPROVED" if approved else "CANCELLED"
        self.log(f"*** {status}: {data.get('request_id', '')[:16]} ***")

    def _step(self, step, action, thought, detail="", observation="", tokens=0):
        self.controller.send_status({
            "step": step,
            "action": action,
            "thought": thought,
            "action_detail": detail,
            "observation": observation,
            "tokens": tokens,
        })

    # ---- Content Tests -------------------------------------------------

    def test_simple(self):
        self._step(1, "THINKING", SIMPLE_THOUGHT, tokens=128)

    def test_long_reasoning(self):
        self._step(
            2, "THINKING", LONG_MARKDOWN,
            detail="Analyzing complex request", tokens=1547,
        )

    def test_hindi(self):
        self._step(
            3, "THINKING", HINDI_THOUGHT,
            detail="Hindi rendering test", tokens=342,
        )

    def test_stream(self):
        self._stream_idx = 0
        steps = [
            "Initializing analysis of user query...",
            "Scanning available tools and context...",
            "Formulating approach based on gathered data...",
            "Preparing to execute the primary action...",
            "Almost done - finalizing the response...",
        ]
        self._stream_steps = steps
        if self._stream_timer is None:
            self._stream_timer = QTimer(self)
            self._stream_timer.timeout.connect(self._stream_tick)
        self._stream_timer.start(400)

    def _stream_tick(self):
        if self._stream_idx >= len(self._stream_steps):
            self._stream_timer.stop()
            return
        self._step(
            step=self._stream_idx + 4,
            action="THINKING",
            thought=self._stream_steps[self._stream_idx],
            detail=f"Stream update {self._stream_idx + 1}/{len(self._stream_steps)}",
            tokens=200 + self._stream_idx * 150,
        )
        self._stream_idx += 1

    # ---- Action Types --------------------------------------------------

    def test_web_search(self):
        self._step(
            5, "search_actions",
            "### Web Search Initiated\n\n"
            "Searching for: **latest AI news 2025**\n\n"
            "Sources:\n- Google News\n- TechCrunch\n- The Verge",
            detail="Query: latest AI news 2025",
            observation="Found 15 relevant articles from trusted sources.",
            tokens=623,
        )

    def test_deep_research(self):
        self._step(
            6, "deep_research",
            "## Deep Research Mode\n\n"
            "Conducting multi-source analysis:\n"
            "1. Primary sources\n2. Secondary sources\n3. Expert opinions\n\n"
            "```python\nresults = [scrape(url) for url in sources]\nanalyze(results)\n```",
            detail="Research: quantum computing advances",
            observation="Gathered 42 sources, analyzed 12 in depth.",
            tokens=2341,
        )

    def test_python(self):
        self._step(
            7, "run_python_code",
            "Executing Python script to compute results.\n\n"
            "```python\ndef fibonacci(n):\n"
            "    a, b = 0, 1\n"
            "    for _ in range(n):\n"
            "        yield a\n"
            "        a, b = b, a + b\n\n"
            "print(list(fibonacci(10)))\n```",
            detail="fibonacci.py - computing sequence",
            observation="Output: [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]",
            tokens=856,
        )

    def test_terminal(self):
        self._step(
            8, "execute_terminal_command",
            "Running terminal command:\n\n"
            "```bash\nls -la /home/user/projects\n```\n\n"
            "Expecting to list project directories.",
            detail="ls -la /home/user/projects",
            observation="total 42\ndrwxr-xr-x 5 user user 4096 ...",
            tokens=312,
        )

    def test_file_ops(self):
        self._step(
            9, "file_operations",
            "### File Operations\n\n"
            "Reading config and updating settings:\n"
            "- `config.json`\n- `settings.py`\n- `README.md`",
            detail="editing config.json, settings.py",
            observation="3 files modified successfully.",
            tokens=445,
        )

    def test_image_gen(self):
        self._step(
            10, "image_command",
            "Generating image with prompt: "
            "*A futuristic cityscape at sunset with neon lights*",
            detail="prompt: futuristic cityscape",
            observation="Image generated (1024x1024, 2.3MB)",
            tokens=189,
        )

    def test_memory(self):
        self._step(
            11, "memory_actions",
            "### Memory Graph Update\n\n"
            "Storing new memory:\n"
            "- **User preference**: prefers concise responses\n"
            "- **Context**: working on agent panel\n"
            "- **Timestamp**: now",
            detail="memory_store: user_preferences",
            observation="Memory stored, 3 nodes updated.",
            tokens=267,
        )

    def test_combo(self):
        self._step(
            12,
            "search_actions|run_python_code|execute_terminal_command",
            "Running a **multi-action pipeline**:\n"
            "1. Web search\n2. Python analysis\n3. Terminal output\n\n"
            "This tests the combo action title format.",
            detail="pipeline: search -> analyze -> output",
            observation="All 3 actions completed in sequence.",
            tokens=1789,
        )

    # ---- Confirmation Previews ----------------------------------------

    def test_whatsapp(self):
        self.controller.send_confirm({
            "request_id": f"wa_{int(time.time())}",
            "platform": "whatsapp",
            "to": "+91 98765 43210",
            "subject": "",
            "body": (
                "Hey! I've finished analyzing the data. "
                "Here's a quick summary:\n\n"
                "- Total records: 15,234\n"
                "- Anomalies found: 3\n"
                "- Confidence: 94%\n\n"
                "Let me know if you want the full report!"
            ),
            "attachments": [],
        })

    def test_telegram(self):
        self.controller.send_confirm({
            "request_id": f"tg_{int(time.time())}",
            "platform": "telegram",
            "to": "@jarvis_user",
            "subject": "",
            "body": (
                "Deployment complete!\n\n"
                "Version: v2.4.1\n"
                "Status: All checks passing\n"
                "Time: 45 seconds\n\n"
                "No action required."
            ),
            "attachments": [],
        })

    def test_email(self):
        self.controller.send_confirm({
            "request_id": f"em_{int(time.time())}",
            "platform": "email",
            "to": "stakeholder@company.com",
            "subject": "Weekly Progress Report - Q4 2025",
            "body": (
                "Dear Stakeholder,\n\n"
                "Here is the weekly progress report:\n\n"
                "1. Completed: 12 tasks\n"
                "2. In progress: 5 tasks\n"
                "3. Blocked: 0 tasks\n\n"
                "Overall status: On track for delivery.\n\n"
                "Best regards,\nJarvis"
            ),
            "attachments": ["report_q4.pdf", "metrics.xlsx"],
        })

    # ---- UI & State ----------------------------------------------------

    def test_observation(self):
        self._step(
            13, "THINKING",
            "Let me check the current system state before proceeding.",
            detail="checking system state",
            observation=(
                "System healthy. CPU 12%, RAM 3.2/16 GB, disk 45% free, "
                "8 services running, no critical alerts."
            ),
            tokens=234,
        )

    def test_complete(self):
        self._step(0, "idle", "", tokens=4521)

    def test_reset(self):
        self._step(
            0, "idle", "Initialized. Waiting for input.", tokens=0,
        )

    def test_wide(self):
        self.agent_panel.user_width = 1000
        self.agent_panel.user_height = 320
        self.agent_panel.show_panel()

    def test_tall(self):
        self.agent_panel.user_width = 580
        self.agent_panel.user_height = 700
        self.agent_panel.show_panel()

    # ---- Lifecycle -----------------------------------------------------

    def closeEvent(self, event):
        try:
            self.agent_panel.close()
        except Exception:
            pass
        super().closeEvent(event)


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Panel first (binds 5556, 5557; SUB connects to 5555)
    panel = AgentPanel()

    # Allow ZMQ listener thread a moment to set up sockets
    time.sleep(0.35)

    # Test controller (binds or connects as needed)
    controller = TestController()

    test_panel = TestPanel(controller, panel)
    controller.log_message.connect(test_panel.log)
    controller.decision_received.connect(test_panel.on_decision)

    # Position test panel bottom-left
    screen = app.primaryScreen().availableGeometry()
    test_panel.move(20, max(20, screen.height() - test_panel.height() - 40))
    test_panel.show()

    # Warm-up: bypass ZMQ PUB/SUB slow-joiner
    def warmup():
        for _ in range(4):
            controller.send_status({
                "step": 0,
                "action": "",
                "thought": "Initialized. Waiting for input.",
                "action_detail": "",
                "observation": "",
                "tokens": 0,
            })
    QTimer.singleShot(400, warmup)

    # Cleanup
    app.aboutToQuit.connect(controller.shutdown)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()