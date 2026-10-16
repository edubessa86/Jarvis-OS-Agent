import os
import logging
import requests
from datetime import datetime


def _sanitize_phone_number(raw_target: str):
    if not raw_target or not str(raw_target).strip():
        return None, "[ERROR] ❌ Target phone number is required."

    clean_target = str(raw_target).strip()
    cleaned_num = (
        clean_target.replace("+", "")
        .replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
        .replace(".", "")
    )

    if cleaned_num.startswith("00"):
        cleaned_num = cleaned_num[2:]
    elif len(cleaned_num) == 11 and cleaned_num.startswith("0"):
        cleaned_num = cleaned_num[1:]

    if not cleaned_num.isdigit() or len(cleaned_num) < 10 or len(cleaned_num) > 15:
        return (
            None,
            f"[ERROR] ❌ Invalid phone number '{clean_target}'. Only numeric phone numbers are supported (contact names are not accepted). Please retrieve the contact's phone number using 'memory_actions' first or ask the user.",
        )

    if len(cleaned_num) == 10:
        target_number = "91" + cleaned_num
        logging.info(f"🔢 10-Digit number detected, added default country code '91': {target_number}")
    else:
        target_number = cleaned_num
        logging.info(f"🔢 Direct number detected: {target_number}")

    return target_number, None


def send_whatsapp_message(to_target: str, message: str = "", attachment_path: str = None) -> str:
    target_number, err = _sanitize_phone_number(to_target)
    if err:
        logging.error(err)
        return err

    clean_message = (message or "").strip()
    clean_attachment = attachment_path.strip() if isinstance(attachment_path, str) and attachment_path.strip() else None

    if not clean_message and not clean_attachment:
        return "[ERROR] ❌ Both message text and attachment cannot be empty."

    if clean_attachment:
        clean_attachment = os.path.abspath(clean_attachment)
        if not os.path.isfile(clean_attachment):
            error_msg = f"[ERROR] ❌ Workspace file not found: {clean_attachment}"
            logging.error(error_msg)
            return error_msg

        if not os.access(clean_attachment, os.R_OK):
            error_msg = f"[ERROR] ❌ Missing read permission for file: {clean_attachment}"
            logging.error(error_msg)
            return error_msg

    local_baileys_url = "http://localhost:3000/send"
    req_timeout = 60 if clean_attachment else 20

    payload = {
        "number": target_number,
        "message": clean_message,
        "file_path": clean_attachment,
    }

    try:
        logging.info(f"🚀 Sending command to local Baileys bridge for {target_number}...")

        response = requests.post(local_baileys_url, json=payload, timeout=req_timeout)

        if response.status_code == 200:
            status_text = "✅ WhatsApp message (with attachment)" if clean_attachment else "✅ WhatsApp message"
            success_text = f"{status_text} successfully sent to {target_number} via Baileys."
            logging.info(success_text)
            return success_text
        else:
            error_text = f"[ERROR] ❌ Baileys Server Error (Code {response.status_code}): {response.text}"
            logging.error(error_text)
            return error_text

    except requests.exceptions.ConnectionError:
        error_msg = "[ERROR] ❌ Node.js server is offline! Please start 'node baileys_service.js' in the BaileysServer directory first."
        logging.error(error_msg)
        return error_msg
    except requests.exceptions.Timeout:
        error_msg = f"[ERROR] ❌ Timeout Error: Node.js server did not respond within {req_timeout} seconds."
        logging.error(error_msg)
        return error_msg
    except requests.exceptions.RequestException as e:
        error_msg = f"[ERROR] ❌ API Request failed: {e}"
        logging.error(error_msg)
        return error_msg
    except Exception as e:
        error_msg = f"[ERROR] ❌ Unexpected Python Error: {e}"
        logging.error(error_msg)
        return error_msg


def fetch_whatsapp_chats(to_target: str, start_date: str, end_date: str) -> str:
    if not to_target or not start_date or not end_date:
        return "[ERROR] ❌ Target phone number, start_date, and end_date are all required."

    target_number, err = _sanitize_phone_number(to_target)
    if err:
        logging.error(err)
        return err

    logging.info(f"📱 Fetching chats for numeric ID: {target_number}")

    try:
        start_dt = datetime.strptime(start_date.strip(), "%Y-%m-%d")
        end_dt = datetime.strptime(end_date.strip(), "%Y-%m-%d")

        if start_dt > end_dt:
            start_dt, end_dt = end_dt, start_dt
            start_date, end_date = end_date, start_date

        start_timestamp = int(start_dt.replace(hour=0, minute=0, second=0).timestamp())
        end_timestamp = int(end_dt.replace(hour=23, minute=59, second=59).timestamp())
    except ValueError:
        return "[ERROR] ❌ Invalid date format. Please use YYYY-MM-DD exactly."

    local_baileys_url = "http://localhost:3000/fetch-chats"

    payload = {
        "number": target_number,
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
    }

    try:
        logging.info(f"📥 Sending fetch request to Baileys bridge for {target_number}...")

        response = requests.post(local_baileys_url, json=payload, timeout=25)

        if response.status_code == 200:
            data = response.json()
            messages = data.get("messages", [])

            if not messages:
                return f"Observation: No conversation history found with {target_number} between {start_date} and {end_date}."

            chat_lines = [f"📱 [CHAT HISTORY WITH {target_number} | {start_date} to {end_date}]"]
            for msg in messages:
                sender = "Me (Jarvis)" if msg.get("fromMe") else target_number
                ts = msg.get("timestamp") or 0
                msg_time = datetime.fromtimestamp(ts).strftime("%d %b, %H:%M")
                text = msg.get("text") or "[Media/Unknown]"
                chat_lines.append(f"[{msg_time}] {sender}: {text}")

            success_text = "\n".join(chat_lines)
            logging.info(f"✅ Fetched {len(messages)} messages successfully.")
            return success_text
        else:
            error_text = f"[ERROR] ❌ Baileys Server Error (Code {response.status_code}): {response.text}"
            logging.error(error_text)
            return error_text

    except requests.exceptions.ConnectionError:
        error_msg = "[ERROR] ❌ Node.js server is offline! Please start 'node baileys_service.js' in the BaileysServer directory first."
        logging.error(error_msg)
        return error_msg
    except requests.exceptions.Timeout:
        error_msg = "[ERROR] ❌ Timeout Error: Node.js server did not respond within 25 seconds."
        logging.error(error_msg)
        return error_msg
    except requests.exceptions.RequestException as e:
        error_msg = f"[ERROR] ❌ API Request failed: {e}"
        logging.error(error_msg)
        return error_msg
    except Exception as e:
        error_msg = f"[ERROR] ❌ Unexpected Python Error: {e}"
        logging.error(error_msg)
        return error_msg