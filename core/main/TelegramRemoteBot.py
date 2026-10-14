import os
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor
import telebot
from core.logger.logger import logger
from core.security import load_decrypted_token
from core.main.CommandHandler import main_command_processor, is_jarvis_busy
from core.ui.telegram_status import set_telegram_context, clear_telegram_context

_bot_instance = None
_bot_thread = None
_is_polling = False
_global_executor = None
_global_memory = None
_state_lock = threading.RLock()
_dispatch_lock = threading.Lock()
_stop_event = threading.Event()

MAX_MESSAGE_LENGTH = 4000


def get_token_path() -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base_dir, "Data", "SessionCookies", "telegram_bot_token.enc")


def set_telegram_remote_context(executor: ThreadPoolExecutor, memory) -> None:
    global _global_executor, _global_memory
    with _state_lock:
        _global_executor = executor
        _global_memory = memory


def _split_message(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> list[str]:
    if not text:
        return []
    text = str(text)
    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text
    while len(remaining) > max_length:
        split_idx = remaining.rfind("\n", 0, max_length)
        if split_idx <= 0:
            split_idx = remaining.rfind(" ", 0, max_length)
        if split_idx <= 0:
            split_idx = max_length
        chunks.append(remaining[:split_idx].strip())
        remaining = remaining[split_idx:].strip()

    if remaining:
        chunks.append(remaining)
    return [c for c in chunks if c]


def _telegram_sender_callback(chat_id, message_text: str) -> None:
    bot = _bot_instance
    if not bot or not message_text:
        return

    for chunk in _split_message(message_text):
        try:
            bot.send_message(chat_id, chunk, parse_mode="Markdown")
        except Exception:
            try:
                bot.send_message(chat_id, chunk)
            except Exception as e:
                logger.error(f"Failed to send Telegram response chunk: {e}")


def _is_authorized(message, allowed_chat_id: str) -> bool:
    if not allowed_chat_id or not message or not getattr(message, "chat", None):
        return False
    return str(message.chat.id).strip() == str(allowed_chat_id).strip()


def _wait_for_context(timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _global_executor is not None and _global_memory is not None:
            return True
        if _stop_event.is_set():
            return False
        time.sleep(0.25)
    return _global_executor is not None and _global_memory is not None


def start_telegram_remote_listener() -> bool:
    global _bot_instance, _bot_thread, _is_polling

    with _state_lock:
        if is_telegram_remote_running():
            return True

        token_path = get_token_path()
        if not os.path.exists(token_path):
            return False

        try:
            data = load_decrypted_token(token_path)
            if not isinstance(data, dict) or not data.get("active", True):
                return False

            token = str(data.get("token") or "").strip()
            allowed_chat_id = str(data.get("allowed_chat_id") or "").strip()

            if not token or not allowed_chat_id:
                logger.warning("Telegram Remote Bot token or allowed_chat_id is missing.")
                return False

            _stop_event.clear()
            bot = telebot.TeleBot(token, threaded=False, skip_pending=True)

            @bot.message_handler(commands=["start", "help"])
            def send_welcome(message):
                if not _is_authorized(message, allowed_chat_id):
                    return
                msg = (
                    "🤖 *Jarvis Remote Controller Active*\n\n"
                    "• Send any text message to execute a command on your PC.\n"
                    "• Use `/silent <command>` to execute without voice output.\n"
                    "• Use `/status` to check if Jarvis is currently busy or ready."
                )
                try:
                    bot.reply_to(message, msg, parse_mode="Markdown")
                except Exception:
                    bot.reply_to(message, msg)

            @bot.message_handler(commands=["status"])
            def send_status(message):
                if not _is_authorized(message, allowed_chat_id):
                    return
                state_text = "⏳ *Busy* (Executing a task)" if is_jarvis_busy() else "✅ *Ready* (Idle)"
                try:
                    bot.reply_to(message, f"🤖 Jarvis Status: {state_text}", parse_mode="Markdown")
                except Exception:
                    bot.reply_to(message, f"Jarvis Status: {state_text}")

            @bot.message_handler(content_types=["text"], func=lambda message: True)
            def handle_remote_command(message):
                if not _is_authorized(message, allowed_chat_id):
                    return

                cmd_text = (message.text or "").strip()
                if not cmd_text:
                    return

                is_silent = False
                if re.match(r"(?i)^/silent(?:\b|$)", cmd_text):
                    is_silent = True
                    cmd_text = re.sub(r"(?i)^/silent\s*", "", cmd_text).strip()
                    if not cmd_text:
                        try:
                            bot.reply_to(message, "⚠️ Usage: `/silent <your command>`", parse_mode="Markdown")
                        except Exception:
                            pass
                        return

                if not _wait_for_context(timeout=10.0):
                    try:
                        bot.reply_to(message, "⏳ Jarvis is still initializing. Please try again in a few seconds.")
                    except Exception:
                        pass
                    return

                with _dispatch_lock:
                    try:
                        bot.send_chat_action(message.chat.id, "typing")
                    except Exception:
                        pass

                    if is_jarvis_busy():
                        if _global_memory and hasattr(_global_memory, "add_live_feedback"):
                            try:
                                _global_memory.add_live_feedback(cmd_text)
                                bot.reply_to(message, "🔄 Jarvis is busy. Your input was added as live feedback.")
                            except Exception as e:
                                logger.error(f"Failed to add Telegram live feedback: {e}")
                    else:
                        if _global_memory is not None:
                            if not hasattr(_global_memory, "ephemeral") or not isinstance(_global_memory.ephemeral, dict):
                                _global_memory.ephemeral = {}
                            _global_memory.ephemeral["force_silent_agentic"] = is_silent

                        set_telegram_context(message.chat.id, _telegram_sender_callback)

                        if _global_executor and _global_memory:
                            _global_executor.submit(
                                main_command_processor,
                                cmd_text,
                                _global_executor,
                                _global_memory,
                                "telegram_bot",
                            )

            def _poll_worker():
                global _is_polling
                retries = 0
                try:
                    bot.remove_webhook()
                except Exception:
                    pass

                while not _stop_event.is_set():
                    try:
                        bot.infinity_polling(
                            timeout=20,
                            long_polling_timeout=10,
                            skip_pending=True,
                            logger_level=None,
                        )
                        break
                    except Exception as e:
                        if _stop_event.is_set():
                            break
                        error_msg = str(e).lower()
                        if "polling exited" in error_msg or "break infinity polling" in error_msg:
                            break
                        retries += 1
                        logger.warning(f"Telegram polling interrupted (retry {retries}/5): {e}")
                        if retries >= 5:
                            logger.error("Telegram Remote Bot reached maximum retry limit. Stopping listener.")
                            break
                        if _stop_event.wait(timeout=5):
                            break

                with _state_lock:
                    _is_polling = False

            _bot_instance = bot
            _is_polling = True
            _bot_thread = threading.Thread(target=_poll_worker, name="TelegramRemoteBotThread", daemon=True)
            _bot_thread.start()
            return True

        except Exception as e:
            logger.error(f"Failed to start Telegram Remote Bot listener: {e}")
            _is_polling = False
            _bot_instance = None
            return False


def stop_telegram_remote_listener() -> None:
    global _bot_instance, _bot_thread, _is_polling

    _stop_event.set()
    with _state_lock:
        bot = _bot_instance
        thread = _bot_thread
        _is_polling = False
        _bot_instance = None
        _bot_thread = None

    if bot:
        try:
            bot.stop_polling()
        except Exception:
            pass
        try:
            bot.stop_bot()
        except Exception:
            pass

    if thread and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=3)

    try:
        clear_telegram_context()
    except Exception:
        pass


def is_telegram_remote_running() -> bool:
    return bool(
        _is_polling
        and not _stop_event.is_set()
        and _bot_instance is not None
        and _bot_thread is not None
        and _bot_thread.is_alive()
    )