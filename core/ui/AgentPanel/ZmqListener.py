import queue
import json
import zmq

from PyQt5.QtCore import QThread, pyqtSignal


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
                    latest_data = None
                    while self._running:
                        try:
                            message = sub_socket.recv_string(flags=zmq.NOBLOCK)
                            topic, json_data = message.split(" ", 1)
                            latest_data = json.loads(json_data)
                        except zmq.Again:
                            break
                        except Exception:
                            break
                    if latest_data is not None:
                        self.status_received.emit(latest_data)

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