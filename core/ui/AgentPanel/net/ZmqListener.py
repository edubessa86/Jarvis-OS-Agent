import queue
import json
import sys
import zmq

from PyQt5.QtCore import QThread, pyqtSignal


def _log_error(msg):
    if sys.__stderr__ is not None:
        try:
            sys.__stderr__.write(msg + "\n")
        except Exception:
            pass


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
        self.wait(300)

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
                        self.reply_queue.put(reply_data)
                        break
                    except Exception:
                        pass

                socks = dict(poller.poll(25))

                if sub_socket in socks and socks[sub_socket] == zmq.POLLIN:
                    latest_raw = None
                    for _ in range(100):
                        if not self._running:
                            break
                        try:
                            message = sub_socket.recv_string(flags=zmq.NOBLOCK)
                            _, json_data = message.split(" ", 1)
                            if '"step": 0' in json_data or '"step":0' in json_data or '"Initialized"' in json_data:
                                parsed_special = json.loads(json_data)
                                self.status_received.emit(parsed_special)
                                latest_raw = None
                            else:
                                latest_raw = json_data
                        except zmq.Again:
                            break
                        except Exception as e:
                            _log_error(f"ZmqListener status decode error: {e}")
                            break
                    if latest_raw is not None:
                        try:
                            self.status_received.emit(json.loads(latest_raw))
                        except Exception as e:
                            _log_error(f"ZmqListener final decode error: {e}")

                if confirm_pull in socks and socks[confirm_pull] == zmq.POLLIN:
                    for _ in range(100):
                        if not self._running:
                            break
                        try:
                            confirm_data = confirm_pull.recv_json(flags=zmq.NOBLOCK)
                            self.confirmation_requested.emit(confirm_data)
                        except zmq.Again:
                            break
                        except Exception as e:
                            _log_error(f"ZmqListener confirm decode error: {e}")
                            break
            except Exception as e:
                _log_error(f"ZmqListener loop error: {e}")

        try:
            sub_socket.close()
            confirm_pull.close()
            confirm_push.close()
            context.term()
        except Exception:
            pass