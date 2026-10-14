import os
import sys
import io
import time
import asyncio
import uuid
import json
import socket
import shutil
import urllib.parse
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import List, Optional

try:
    import qrcode
    from qrcode.image.pil import PilImage
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "qrcode[pil]"])
    import qrcode
    from qrcode.image.pil import PilImage

try:
    import multipart
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-multipart"])

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger.logger import logger

PORT = 8090
devices = {}
device_meta = {}
ui_clients = []
pending_requests = {}
registered_files = {}

STATE_FILE = os.path.join(PROJECT_ROOT, "Data", "SessionCookies", "connected_devices.json")
USER_HOME = os.path.expanduser("~")
PC_SHARE_DIR = os.path.join(USER_HOME, "Documents", "Jarvis", "JarvisShare")
os.makedirs(PC_SHARE_DIR, exist_ok=True)

STAGING_DIR = os.path.join(PROJECT_ROOT, "Data", "Staging")
os.makedirs(STAGING_DIR, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    update_state_file()
    logger.info(f"Mobile Bridge Service Started on port {PORT}. Connected devices: {list(devices.keys())}")
    yield


app = FastAPI(title="Jarvis Mobile Bridge", lifespan=lifespan)

templates_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
os.makedirs(templates_dir, exist_ok=True)
templates = Jinja2Templates(directory=templates_dir)


class CommandRequest(BaseModel):
    target_device: str
    command: str
    timeout: Optional[float] = 90.0


class RegisterFilesRequest(BaseModel):
    file_paths: List[str]


class TransferRequest(BaseModel):
    direction: str
    device_id: Optional[str] = None
    file_paths: List[str]


class QuickActionRequest(BaseModel):
    action: str
    payload: Optional[str] = None


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def update_state_file():
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(list(devices.keys()), f)
    except Exception as e:
        logger.error(f"Mobile Bridge: Failed to update state file -> {e}")


def serialize_devices():
    out = []
    for did in devices.keys():
        meta = device_meta.get(did, {})
        out.append({
            "id": did,
            "battery": meta.get("battery"),
            "storage": meta.get("storage"),
            "network": meta.get("network"),
        })
    return out


async def broadcast_ui(message: dict):
    dead = []
    for c in ui_clients:
        try:
            await c.send_json(message)
        except Exception:
            dead.append(c)
    for c in dead:
        if c in ui_clients:
            ui_clients.remove(c)


def resolve_unique_save_path(directory: str, filename: str) -> str:
    safe_name = os.path.basename(filename.strip()) or f"file_{int(time.time())}"
    candidate = os.path.join(directory, safe_name)
    if not os.path.exists(candidate):
        return candidate
    name, ext = os.path.splitext(safe_name)
    counter = 1
    while True:
        candidate = os.path.join(directory, f"{name}_{counter}{ext}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


async def send_device_request(device_id: str, payload: dict, timeout: float = 90.0):
    if device_id not in devices:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' offline.")
    ws = devices[device_id]
    req_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_requests[req_id] = future
    payload["req_id"] = req_id

    try:
        await ws.send_json(payload)
    except Exception as e:
        pending_requests.pop(req_id, None)
        raise HTTPException(status_code=500, detail=f"Failed to send payload to device: {e}")

    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        return {"error": f"Timeout: Device did not respond in {int(timeout)} seconds.", "returncode": -1}
    finally:
        pending_requests.pop(req_id, None)


@app.get("/api/health")
async def health_check():
    return {
        "status": "online",
        "port": PORT,
        "connected_devices": list(devices.keys()),
    }


@app.post("/api/register_files")
async def register_files_for_download(req: RegisterFilesRequest):
    items = []
    missing = []
    for p in req.file_paths:
        abs_p = os.path.abspath(p)
        if not os.path.isfile(abs_p):
            missing.append(p)
            continue
        token = uuid.uuid4().hex
        name = os.path.basename(abs_p)
        size = os.path.getsize(abs_p)
        registered_files[token] = {
            "path": abs_p,
            "filename": name,
            "size": size,
            "created_at": time.time(),
        }
        items.append({
            "token": token,
            "filename": name,
            "size": size,
            "url_path": f"/api/download/{token}",
        })
    if missing:
        raise HTTPException(status_code=400, detail=f"Files not found on PC: {missing}")
    return {"status": "success", "files": items}


@app.get("/api/download/{identifier:path}")
async def download_file(identifier: str):
    decoded_id = urllib.parse.unquote(identifier)
    target_path = None
    display_name = None
    cleanup_token = None

    if decoded_id in registered_files:
        info = registered_files[decoded_id]
        target_path = info["path"]
        display_name = info["filename"]
        cleanup_token = decoded_id
    else:
        staged_path = os.path.join(STAGING_DIR, os.path.basename(decoded_id))
        if os.path.isfile(staged_path):
            target_path = staged_path
            display_name = os.path.basename(staged_path)

    if not target_path or not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")

    total_size = os.path.getsize(target_path)

    async def file_streamer():
        sent = 0
        chunk_size = 256 * 1024
        await broadcast_ui({
            "type": "transfer_start",
            "direction": "pc_to_mobile",
            "filename": display_name,
            "total": total_size,
        })
        last_emit = 0.0
        try:
            with open(target_path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    sent += len(chunk)
                    now = time.time()
                    if total_size > 0 and (now - last_emit > 0.25 or sent == total_size):
                        last_emit = now
                        pct = int((sent / total_size) * 100)
                        await broadcast_ui({
                            "type": "transfer_progress",
                            "direction": "pc_to_mobile",
                            "filename": display_name,
                            "percent": pct,
                            "transferred": sent,
                            "total": total_size,
                        })
                    yield chunk
        finally:
            if cleanup_token:
                registered_files.pop(cleanup_token, None)
            elif target_path.startswith(STAGING_DIR) and os.path.exists(target_path):
                try:
                    os.remove(target_path)
                except Exception:
                    pass
            await broadcast_ui({
                "type": "transfer_complete",
                "direction": "pc_to_mobile",
                "filename": display_name,
                "total": total_size,
            })

    encoded_name = urllib.parse.quote(display_name)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}",
        "Content-Length": str(total_size),
    }
    return StreamingResponse(file_streamer(), media_type="application/octet-stream", headers=headers)


async def _save_uploaded_stream(upload: UploadFile) -> str:
    save_path = resolve_unique_save_path(PC_SHARE_DIR, upload.filename or "shared_file")
    filename = os.path.basename(save_path)
    await broadcast_ui({
        "type": "transfer_start",
        "direction": "mobile_to_pc",
        "filename": filename,
        "total": upload.size or 0,
    })
    written = 0
    last_emit = 0.0
    chunk_size = 256 * 1024
    with open(save_path, "wb") as buffer:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            buffer.write(chunk)
            written += len(chunk)
            now = time.time()
            if now - last_emit > 0.25:
                last_emit = now
                pct = int((written / upload.size) * 100) if upload.size else 50
                await broadcast_ui({
                    "type": "transfer_progress",
                    "direction": "mobile_to_pc",
                    "filename": filename,
                    "percent": min(99, pct),
                    "transferred": written,
                    "total": upload.size or written,
                })
    await broadcast_ui({
        "type": "transfer_complete",
        "direction": "mobile_to_pc",
        "filename": filename,
        "path": save_path,
        "total": written,
    })
    logger.info(f"Mobile Bridge: Received file from mobile -> {save_path} ({written} bytes)")
    return save_path


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    save_path = await _save_uploaded_stream(file)
    return {"status": "success", "path": save_path, "filename": os.path.basename(save_path)}


@app.post("/api/upload_batch")
async def upload_batch(files: List[UploadFile] = File(...)):
    saved = []
    errors = []
    for file in files:
        try:
            save_path = await _save_uploaded_stream(file)
            saved.append(save_path)
        except Exception as e:
            logger.error(f"Mobile Bridge: Failed saving {file.filename} -> {e}")
            errors.append({"file": file.filename, "error": str(e)})
    return {
        "status": "success" if not errors else ("partial" if saved else "error"),
        "count": len(saved),
        "saved": saved,
        "errors": errors,
    }


@app.post("/api/send_to_device/{device_id}")
async def send_files_from_ui_to_device(device_id: str, files: List[UploadFile] = File(...)):
    if device_id not in devices:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' offline.")
    pc_ip = get_local_ip()
    download_items = []
    temp_paths = []
    try:
        for upload in files:
            safe_name = os.path.basename(upload.filename or f"file_{int(time.time())}")
            temp_path = os.path.join(STAGING_DIR, f"{uuid.uuid4().hex}_{safe_name}")
            with open(temp_path, "wb") as buffer:
                shutil.copyfileobj(upload.file, buffer)
            temp_paths.append(temp_path)
            token = uuid.uuid4().hex
            registered_files[token] = {
                "path": temp_path,
                "filename": safe_name,
                "size": os.path.getsize(temp_path),
                "created_at": time.time(),
            }
            download_items.append({
                "url": f"http://{pc_ip}:{PORT}/api/download/{token}",
                "filename": safe_name,
            })

        timeout = max(60.0, 20.0 * len(download_items))
        resp = await send_device_request(
            device_id,
            {"type": "pull_files", "files": download_items},
            timeout=timeout,
        )
        for p in temp_paths:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        if resp.get("returncode", 0) != 0 or resp.get("error"):
            raise HTTPException(status_code=500, detail=resp.get("error") or resp.get("result") or "Transfer failed")
        return {"status": "success", "result": resp.get("result"), "files": [i["filename"] for i in download_items]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/transfer")
async def handle_structured_transfer(req: TransferRequest):
    if not devices:
        raise HTTPException(status_code=404, detail="No mobile device connected.")
    target_device = req.device_id if req.device_id in devices else next(iter(devices.keys()))
    pc_ip = get_local_ip()

    if req.direction == "pc_to_mobile":
        missing = [p for p in req.file_paths if not os.path.isfile(p)]
        if missing:
            raise HTTPException(status_code=400, detail=f"Files not found on PC: {missing}")

        download_items = []
        for p in req.file_paths:
            abs_p = os.path.abspath(p)
            token = uuid.uuid4().hex
            name = os.path.basename(abs_p)
            registered_files[token] = {
                "path": abs_p,
                "filename": name,
                "size": os.path.getsize(abs_p),
                "created_at": time.time(),
            }
            download_items.append({
                "url": f"http://{pc_ip}:{PORT}/api/download/{token}",
                "filename": name,
            })

        timeout = max(60.0, 25.0 * len(download_items))
        resp = await send_device_request(
            target_device,
            {"type": "pull_files", "files": download_items},
            timeout=timeout,
        )
        if resp.get("returncode", 0) != 0 or resp.get("error"):
            return {
                "status": "error",
                "error": resp.get("error") or resp.get("result") or "Failed to transfer files to phone",
            }
        return {
            "status": "success",
            "device_id": target_device,
            "destination": "/sdcard/JarvisShare",
            "files": [i["filename"] for i in download_items],
            "detail": resp.get("result"),
        }

    elif req.direction == "mobile_to_pc":
        timeout = max(60.0, 25.0 * len(req.file_paths))
        resp = await send_device_request(
            target_device,
            {
                "type": "push_files",
                "file_paths": req.file_paths,
                "upload_url": f"http://{pc_ip}:{PORT}/api/upload",
            },
            timeout=timeout,
        )
        if resp.get("returncode", 0) != 0 or resp.get("error"):
            return {
                "status": "error",
                "error": resp.get("error") or resp.get("result") or "Failed to fetch files from phone",
            }
        return {
            "status": "success",
            "device_id": target_device,
            "destination": PC_SHARE_DIR,
            "files": resp.get("saved_files", [os.path.basename(p) for p in req.file_paths]),
            "detail": resp.get("result"),
        }

    raise HTTPException(status_code=400, detail=f"Unsupported direction: {req.direction}")


@app.post("/api/action/{device_id}")
async def trigger_quick_action(device_id: str, req: QuickActionRequest):
    if device_id not in devices:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' offline.")
    pc_ip = get_local_ip()
    action = req.action.lower().strip()

    if action == "ring":
        cmd = (
            "termux-vibrate -d 1500 && "
            "termux-notification --title 'J.A.R.V.I.S Alert' --content 'PC is paging your device' --priority max --sound && "
            "termux-toast -g middle 'J.A.R.V.I.S is ringing your phone!'"
        )
        resp = await send_device_request(device_id, {"type": "command", "command": cmd}, timeout=20.0)
        return {"status": "success", "result": resp.get("result") or "Phone alerted."}

    elif action == "clipboard_get":
        resp = await send_device_request(
            device_id,
            {"type": "command", "command": "termux-clipboard-get"},
            timeout=15.0,
        )
        return {"status": "success", "clipboard": resp.get("result", "")}

    elif action == "clipboard_set":
        text = req.payload or ""
        resp = await send_device_request(
            device_id,
            {"type": "clipboard_set", "text": text},
            timeout=15.0,
        )
        return {"status": "success", "result": resp.get("result", "Clipboard updated on phone.")}

    elif action == "screenshot":
        resp = await send_device_request(
            device_id,
            {"type": "push_files", "file_paths": ["__latest_screenshot__"], "upload_url": f"http://{pc_ip}:{PORT}/api/upload"},
            timeout=30.0,
        )
        if resp.get("returncode", 0) != 0:
            raise HTTPException(status_code=500, detail=resp.get("result") or "Could not fetch screenshot")
        return {"status": "success", "result": resp.get("result")}

    elif action == "latest_photo":
        resp = await send_device_request(
            device_id,
            {"type": "push_files", "file_paths": ["__latest_photo__"], "upload_url": f"http://{pc_ip}:{PORT}/api/upload"},
            timeout=45.0,
        )
        if resp.get("returncode", 0) != 0:
            raise HTTPException(status_code=500, detail=resp.get("result") or "Could not fetch latest photo")
        return {"status": "success", "result": resp.get("result")}

    raise HTTPException(status_code=400, detail=f"Unknown quick action: {action}")


@app.get("/dashboard", response_class=HTMLResponse)
async def render_dashboard(request: Request):
    ip_address = get_local_ip()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"ip_address": ip_address, "port": PORT},
    )


@app.get("/api/qr")
async def get_qr():
    ip = get_local_ip()
    data = f"curl -s http://{ip}:{PORT}/api/setup | python"
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color=(5, 8, 20), back_color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/setup", response_class=PlainTextResponse)
async def get_setup_script(request: Request):
    ip_address = request.url.hostname or get_local_ip()

    script = f'''import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
import uuid
import mimetypes

SERVER_IP = "{ip_address}"
SERVER_PORT = {PORT}

try:
    import websockets
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "websockets", "--quiet"])
    import websockets


def _run_sync(cmd, timeout=4):
    try:
        return subprocess.check_output(
            cmd, timeout=timeout, stderr=subprocess.DEVNULL
        ).decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def setup_environment():
    try:
        subprocess.Popen(["termux-wake-lock"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    try:
        os.makedirs("/sdcard/JarvisShare", exist_ok=True)
    except Exception:
        pass

    home = os.path.expanduser("~")
    bin_dir = os.path.join(home, "bin")
    os.makedirs(bin_dir, exist_ok=True)

    share_uploader = os.path.join(home, ".jarvis_share_upload.py")
    uploader_code = """import os, sys, uuid, urllib.request
SERVER = "http://""" + SERVER_IP + """:""" + str(SERVER_PORT) + """"

def upload_file(filepath):
    if not os.path.isfile(filepath):
        os.system("termux-toast 'Jarvis: File not found'")
        return
    filename = os.path.basename(filepath)
    boundary = uuid.uuid4().hex
    os.system(f"termux-toast 'Sending {filename} to Jarvis PC...'")
    try:
        with open(filepath, "rb") as f:
            file_data = f.read()
        header = (
            f"--{boundary}\\r\\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\\r\\n'
            f"Content-Type: application/octet-stream\\r\\n\\r\\n"
        ).encode("utf-8")
        footer = f"\\r\\n--{boundary}--\\r\\n".encode("utf-8")
        body = header + file_data + footer
        req = urllib.request.Request(
            f"{SERVER}/api/upload",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            if resp.status == 200:
                os.system(f"termux-toast 'Sent {filename} to Jarvis PC!'")
            else:
                os.system(f"termux-toast 'Upload failed: HTTP {resp.status}'")
    except Exception as e:
        os.system(f"termux-toast 'Jarvis Upload Error: {e}'")

if __name__ == "__main__":
    for arg in sys.argv[1:]:
        upload_file(arg)
"""
    try:
        with open(share_uploader, "w", encoding="utf-8") as f:
            f.write(uploader_code)
        editor_hook = os.path.join(bin_dir, "termux-file-editor")
        with open(editor_hook, "w", encoding="utf-8") as f:
            f.write(f"python {{share_uploader}} \\"$@\\"\\n")
        os.chmod(editor_hook, 0o755)
    except Exception:
        pass


def get_device_name():
    model = _run_sync(["getprop", "ro.product.model"])
    brand = _run_sync(["getprop", "ro.product.brand"])
    market = _run_sync(["getprop", "ro.product.marketname"])

    if market and market.lower() not in ("", "unknown"):
        base = market
    elif model and model.lower() not in ("", "unknown"):
        base = model
        if brand and brand.lower() not in ("", "unknown") and not model.lower().startswith(brand.lower()):
            base = f"{{brand}} {{model}}"
    else:
        hn = ""
        try:
            hn = socket.gethostname()
        except Exception:
            pass
        base = hn if (hn and hn.lower() != "localhost") else "android_device"

    base = base.strip().replace(" ", "_") or "android_device"

    try:
        aid = _run_sync(["settings", "get", "secure", "android_id"], timeout=2)
        if aid and len(aid) >= 4 and "Failure" not in aid:
            base = f"{{base}}_{{aid[:4]}}"
    except Exception:
        pass

    return base


def get_battery():
    try:
        out = subprocess.check_output(["termux-battery-status"], timeout=4, stderr=subprocess.DEVNULL)
        data = json.loads(out.decode("utf-8", errors="replace"))
        level = int(data.get("percentage", -1))
        if level < 0:
            return None
        return {{
            "level": level,
            "charging": data.get("status") in ("CHARGING", "FULL"),
            "temperature": data.get("temperature"),
        }}
    except Exception:
        return None


def get_storage():
    try:
        target = "/sdcard" if os.path.exists("/sdcard") else os.path.expanduser("~")
        usage = shutil.disk_usage(target)
        free_gb = round(usage.free / (1024 ** 3), 1)
        total_gb = round(usage.total / (1024 ** 3), 1)
        return {{"free_gb": free_gb, "total_gb": total_gb}}
    except Exception:
        return None


def get_network_info():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((SERVER_IP, SERVER_PORT))
        ip = s.getsockname()[0]
        s.close()
        return {{"ip": ip}}
    except Exception:
        return None


async def run_shell_command(cmd: str, timeout: float = 85.0):
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return "Command timed out on mobile device.", -1
        out_str = stdout.decode("utf-8", errors="replace").strip() if stdout else ""
        err_str = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
        combined = out_str if out_str else err_str
        return combined, (proc.returncode or 0)
    except Exception as e:
        return str(e), -1


def _download_single_file(url: str, dest_path: str):
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest_path, "wb") as out:
        shutil.copyfileobj(resp, out, length=256 * 1024)


def _resolve_special_mobile_path(token_or_path: str) -> str:
    if token_or_path == "__latest_photo__":
        search_dirs = ["/sdcard/DCIM/Camera", "/sdcard/Pictures", "/sdcard/Download"]
    elif token_or_path == "__latest_screenshot__":
        search_dirs = ["/sdcard/Pictures/Screenshots", "/sdcard/DCIM/Screenshots"]
    else:
        return token_or_path

    candidates = []
    for d in search_dirs:
        if os.path.isdir(d):
            for entry in os.scandir(d):
                if entry.is_file() and not entry.name.startswith("."):
                    try:
                        candidates.append((entry.stat().st_mtime, entry.path))
                    except Exception:
                        pass
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return token_or_path


def _upload_single_file(filepath: str, upload_url: str):
    real_path = _resolve_special_mobile_path(filepath)
    if not os.path.isfile(real_path):
        raise FileNotFoundError(f"File not found on phone: {{real_path}}")
    filename = os.path.basename(real_path)
    boundary = uuid.uuid4().hex
    with open(real_path, "rb") as f:
        file_bytes = f.read()
    header = (
        f"--{{boundary}}\\r\\n"
        f'Content-Disposition: form-data; name="file"; filename="{{filename}}"\\r\\n'
        f"Content-Type: application/octet-stream\\r\\n\\r\\n"
    ).encode("utf-8")
    footer = f"\\r\\n--{{boundary}}--\\r\\n".encode("utf-8")
    body = header + file_bytes + footer
    req = urllib.request.Request(
        upload_url,
        data=body,
        headers={{"Content-Type": f"multipart/form-data; boundary={{boundary}}"}},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace")), filename


async def telemetry_loop(ws):
    while True:
        await asyncio.sleep(25)
        b = await asyncio.to_thread(get_battery)
        s = await asyncio.to_thread(get_storage)
        try:
            await ws.send(json.dumps({{
                "type": "battery_update",
                "battery": b,
                "storage": s,
            }}))
        except Exception:
            return


async def handle_ws_message(ws, data: dict):
    msg_type = data.get("type")
    req_id = data.get("req_id")

    if msg_type == "command":
        cmd = data.get("command", "")
        result, code = await run_shell_command(cmd)
        await ws.send(json.dumps({{
            "type": "response",
            "req_id": req_id,
            "result": result,
            "returncode": code,
        }}))

    elif msg_type == "pull_files":
        files = data.get("files", [])
        saved = []
        errors = []
        for item in files:
            url = item.get("url")
            name = os.path.basename(item.get("filename") or "file")
            dest = f"/sdcard/JarvisShare/{{name}}"
            try:
                await asyncio.to_thread(_download_single_file, url, dest)
                saved.append(dest)
            except Exception as e:
                errors.append(f"{{name}}: {{e}}")
        if saved:
            await run_shell_command(f"termux-toast 'Jarvis: Received {{len(saved)}} file(s)'", timeout=5)
        await ws.send(json.dumps({{
            "type": "response",
            "req_id": req_id,
            "result": f"Saved {{len(saved)}} files to /sdcard/JarvisShare" if not errors else "; ".join(errors),
            "saved_files": saved,
            "returncode": 0 if not errors else 1,
        }}))

    elif msg_type == "push_files":
        paths = data.get("file_paths", [])
        upload_url = data.get("upload_url")
        uploaded = []
        errors = []
        for p in paths:
            try:
                _, fname = await asyncio.to_thread(_upload_single_file, p, upload_url)
                uploaded.append(fname)
            except Exception as e:
                errors.append(str(e))
        if uploaded:
            await run_shell_command(f"termux-toast 'Jarvis: Sent {{len(uploaded)}} file(s) to PC'", timeout=5)
        await ws.send(json.dumps({{
            "type": "response",
            "req_id": req_id,
            "result": f"Uploaded {{len(uploaded)}} files: {{', '.join(uploaded)}}" if not errors else "; ".join(errors),
            "saved_files": uploaded,
            "returncode": 0 if not errors else 1,
        }}))

    elif msg_type == "clipboard_set":
        text = data.get("text", "")
        proc = await asyncio.create_subprocess_exec(
            "termux-clipboard-set",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate(input=text.encode("utf-8"))
        await run_shell_command("termux-toast 'Clipboard synced from Jarvis PC'", timeout=4)
        await ws.send(json.dumps({{
            "type": "response",
            "req_id": req_id,
            "result": "Clipboard set on mobile device.",
            "returncode": 0,
        }}))


async def connect_jarvis():
    uri = f"ws://{{SERVER_IP}}:{{SERVER_PORT}}/ws"
    device_id = await asyncio.to_thread(get_device_name)
    print(f"Connecting to Jarvis at {{uri}}...")
    async with websockets.connect(uri, ping_interval=20, ping_timeout=20, max_size=None) as websocket:
        print(f"Connected as: {{device_id}} (Android Share -> Termux enabled)")
        await websocket.send(json.dumps({{
            "type": "handshake",
            "device_id": device_id,
            "battery": await asyncio.to_thread(get_battery),
            "storage": await asyncio.to_thread(get_storage),
            "network": await asyncio.to_thread(get_network_info),
        }}))
        t_task = asyncio.create_task(telemetry_loop(websocket))
        try:
            while True:
                msg = await websocket.recv()
                data = json.loads(msg)
                if data.get("type") == "system" and data.get("command") == "shutdown_bridge":
                    return False
                asyncio.create_task(handle_ws_message(websocket, data))
        finally:
            t_task.cancel()
    return True


async def main_loop():
    setup_environment()
    while True:
        try:
            should_reconnect = await connect_jarvis()
            if should_reconnect is False:
                print("Bridge explicitly shut down by Jarvis PC.")
                break
        except Exception:
            print("Disconnected or Jarvis offline. Retrying in 3 seconds...")
        await asyncio.sleep(3)


if __name__ == "__main__":
    try:
        with open(os.path.expanduser("~/.jarvis_bridge.py"), "w", encoding="utf-8") as _f:
            pass
    except Exception:
        pass
    asyncio.run(main_loop())
'''
    return script


@app.websocket("/ws/ui")
async def ui_websocket(websocket: WebSocket):
    await websocket.accept()
    ui_clients.append(websocket)
    try:
        await websocket.send_json({"type": "init", "devices": serialize_devices()})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in ui_clients:
            ui_clients.remove(websocket)
    except Exception as e:
        logger.error(f"UI WebSocket Error -> {e}")
        if websocket in ui_clients:
            ui_clients.remove(websocket)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    device_id = None
    try:
        handshake = await websocket.receive_json()
        if handshake.get("type") == "handshake":
            device_id = handshake.get("device_id")
            devices[device_id] = websocket
            device_meta[device_id] = {
                "battery": handshake.get("battery"),
                "storage": handshake.get("storage"),
                "network": handshake.get("network"),
            }
            update_state_file()
            logger.info(f"Mobile Bridge: Device Connected -> {device_id}")
            await broadcast_ui({
                "type": "device_connected",
                "device_id": device_id,
                "battery": handshake.get("battery"),
                "storage": handshake.get("storage"),
                "network": handshake.get("network"),
            })
            await websocket.send_json({"type": "welcome", "msg": "Connected to Jarvis PC"})
        else:
            logger.warning("Mobile Bridge: Invalid handshake.")
            await websocket.close()
            return

        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "response":
                req_id = data.get("req_id")
                if req_id in pending_requests and not pending_requests[req_id].done():
                    pending_requests[req_id].set_result(data)

            elif msg_type == "battery_update":
                battery = data.get("battery")
                storage = data.get("storage")
                meta = device_meta.setdefault(device_id, {})
                meta["battery"] = battery
                if storage:
                    meta["storage"] = storage
                await broadcast_ui({
                    "type": "battery_update",
                    "device_id": device_id,
                    "battery": battery,
                    "storage": storage,
                })

            elif msg_type == "error":
                logger.error(f"Mobile Bridge: Phone error ({device_id}) -> {data.get('message')}")

    except WebSocketDisconnect:
        if device_id:
            logger.warning(f"Mobile Bridge: Device Disconnected -> {device_id}")
            if devices.get(device_id) is websocket:
                del devices[device_id]
                device_meta.pop(device_id, None)
                update_state_file()
                await broadcast_ui({"type": "device_disconnected", "device_id": device_id})
    except Exception as e:
        logger.error(f"Mobile Bridge: WebSocket Error -> {e}")
        if device_id and devices.get(device_id) is websocket:
            del devices[device_id]
            device_meta.pop(device_id, None)
            update_state_file()
            await broadcast_ui({"type": "device_disconnected", "device_id": device_id})


@app.post("/api/disconnect/{device_id}")
async def disconnect_device(device_id: str):
    if device_id in devices:
        ws = devices[device_id]
        try:
            await ws.send_json({"type": "system", "command": "shutdown_bridge"})
            logger.info(f"Mobile Bridge: Sent shutdown command to {device_id}")
            return {"status": "success", "msg": "Disconnect signal sent"}
        except Exception as e:
            logger.error(f"Mobile Bridge: Failed to disconnect {device_id} -> {e}")
            raise HTTPException(status_code=500, detail="Failed to send disconnect signal")
    raise HTTPException(status_code=404, detail="Device not found")


@app.post("/api/execute")
async def execute_command(req: CommandRequest):
    if req.target_device not in devices:
        logger.warning(f"Mobile Bridge: Execution failed, '{req.target_device}' is offline.")
        raise HTTPException(status_code=404, detail=f"Device '{req.target_device}' offline.")

    logger.info(f"Mobile Bridge: Sending command to {req.target_device}: {req.command}")
    await broadcast_ui({
        "type": "command_start",
        "device_id": req.target_device,
        "command": req.command,
    })

    resp = await send_device_request(
        req.target_device,
        {"type": "command", "command": req.command},
        timeout=req.timeout or 90.0,
    )

    if "error" in resp:
        logger.error(f"Mobile Bridge: Command error on {req.target_device} -> {resp['error']}")
        return {"error": resp["error"], "returncode": resp.get("returncode", -1)}

    result_text = resp.get("result", "")
    returncode = resp.get("returncode", 0)
    logger.info(f"Mobile Bridge: Response from {req.target_device} (exit={returncode})")

    await broadcast_ui({
        "type": "command_complete",
        "device_id": req.target_device,
        "command": req.command,
        "returncode": returncode,
    })

    return {
        "result": result_text,
        "returncode": returncode,
        "status": "success" if returncode == 0 else "error",
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")