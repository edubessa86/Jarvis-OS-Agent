import os
import re
import time
import asyncio
import threading
import queue
import requests
import base64
from io import BytesIO
from collections import OrderedDict
import json
from requests.adapters import HTTPAdapter
from dotenv import load_dotenv

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
import pygame

load_dotenv()

from core.logger.logger import logger

_audio_output_available = False


def _ensure_mixer_ready() -> bool:
    global _audio_output_available
    try:
        if pygame.mixer.get_init() is not None:
            _audio_output_available = True
            return True
        pygame.mixer.init(frequency=24000, buffer=2048)
        _audio_output_available = True
        return True
    except Exception as e:
        _audio_output_available = False
        logger.error(f"Audio init failed: {e}. Running in text-only mode.")
        return False


if _ensure_mixer_ready():
    logger.info("Audio output enabled")

TTS_API_KEY = os.getenv("TTS_API_KEY")
TTS_ENDPOINT = os.getenv("TTS_ENDPOINT")

from core.brain.config import EDGE_TTS_VOICE

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
    logger.info("Edge-TTS fallback ready")
except ImportError:
    EDGE_TTS_AVAILABLE = False
    logger.warning("Edge-TTS not available")

_state_lock = threading.RLock()
_cache_lock = threading.Lock()
_stop_playback = False
is_speaking = False
_tts_limit_reached = False
_sarvam_cooldown_until = 0.0
_sarvam_consecutive_failures = 0
_speech_gen_id = 0
_audio_queue = queue.Queue()
_start_time = 0.0
_last_spoken_text = ""
_tts_command_watcher_running = False
_last_sentence_id = 0

TTS_STATUS_FILE = "Data/tts_status.json"
TTS_COMMAND_FILE = "Data/tts_command.json"
TTS_WORD_SYNC_FILE = "Data/tts_word_sync.json"
TTS_QUOTA_STATE_FILE = "Data/tts_quota_state.json"

_SARVAM_COOLDOWN_SECONDS = 900.0
_SARVAM_TRANSIENT_COOLDOWN_SECONDS = 120.0
_EDGE_STREAM_TIMEOUT = 7.5
_MAX_CACHE_ITEMS = 32
_MAX_CACHE_TEXT_LEN = 75
_audio_cache = OrderedDict()
_edge_timings_cache = OrderedDict()

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002700-\U000027BF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U0001F700-\U0001F77F"
    "\U00002B00-\U00002BFF"
    "\U0001F000-\U0001F02F"
    "]+",
    flags=re.UNICODE
)

TECH_PRONUNCIATION_RULES = [
    (re.compile(r'\b127\.0\.0\.1:(\d+)\b'), r'localhost port \1'),
    (re.compile(r'\blocalhost:(\d+)\b', re.IGNORECASE), r'localhost port \1'),
    (re.compile(r'\bCtrl\s*\+\s*Shift\s*\+\s*([A-Za-z0-9])\b', re.IGNORECASE), r'control shift \1'),
    (re.compile(r'\bCtrl\s*\+\s*([A-Za-z0-9])\b', re.IGNORECASE), r'control \1'),
    (re.compile(r'\bAlt\s*\+\s*([A-Za-z0-9])\b', re.IGNORECASE), r'alt \1'),
    (re.compile(r'(?<=\w)\.py\b', re.IGNORECASE), ' dot pie'),
    (re.compile(r'(?<=\w)\.md\b', re.IGNORECASE), ' dot M D'),
    (re.compile(r'(?<=\w)\.jsonl\b', re.IGNORECASE), ' dot jason L'),
    (re.compile(r'(?<=\w)\.json\b', re.IGNORECASE), ' dot jason'),
    (re.compile(r'(?<=\w)\.env\b', re.IGNORECASE), ' dot E N V'),
    (re.compile(r'(?<=\w)\.exe\b', re.IGNORECASE), ' dot E X E'),
    (re.compile(r'(?<=\w)\.txt\b', re.IGNORECASE), ' dot text'),
    (re.compile(r'(?<=\w)\.csv\b', re.IGNORECASE), ' dot C S V'),
    (re.compile(r'(?<=\w)\.table\b', re.IGNORECASE), ' dot table'),
    (re.compile(r'\b(\d+(?:\.\d+)?)\s*ms\b'), r'\1 milliseconds'),
    (re.compile(r'\b(\d+(?:\.\d+)?)\s*s\b'), r'\1 seconds'),
    (re.compile(r'\b(\d+(?:\.\d+)?)\s*GB\b', re.IGNORECASE), r'\1 G B'),
    (re.compile(r'\b(\d+(?:\.\d+)?)\s*MB\b', re.IGNORECASE), r'\1 M B'),
    (re.compile(r'\b(\d+(?:\.\d+)?)\s*KB\b', re.IGNORECASE), r'\1 K B'),
]

SPEAKABLE_CHAR_RE = re.compile(r'[A-Za-z0-9\u0900-\u097F]')


def _has_speakable_content(text: str) -> bool:
    if not text:
        return False
    return bool(SPEAKABLE_CHAR_RE.search(text))


def _load_quota_state():
    global _tts_limit_reached, _sarvam_cooldown_until
    try:
        if os.path.exists(TTS_QUOTA_STATE_FILE):
            with open(TTS_QUOTA_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            cooldown_until = float(data.get("cooldown_until", 0.0) or 0.0)
            if cooldown_until > time.time():
                with _state_lock:
                    _sarvam_cooldown_until = cooldown_until
                    _tts_limit_reached = True
    except Exception:
        pass


def _save_quota_state(cooldown_until: float):
    try:
        os.makedirs("Data", exist_ok=True)
        tmp_path = TTS_QUOTA_STATE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"cooldown_until": float(cooldown_until), "ts": time.time()}, f)
        os.replace(tmp_path, TTS_QUOTA_STATE_FILE)
    except Exception:
        pass


def _set_sarvam_cooldown(duration_seconds: float):
    global _tts_limit_reached, _sarvam_cooldown_until
    with _state_lock:
        until = time.time() + float(duration_seconds)
        if until > _sarvam_cooldown_until:
            _sarvam_cooldown_until = until
        _tts_limit_reached = True
        saved_until = _sarvam_cooldown_until
    if duration_seconds >= _SARVAM_COOLDOWN_SECONDS:
        _save_quota_state(saved_until)


def _is_sarvam_available() -> bool:
    global _tts_limit_reached, _sarvam_cooldown_until
    if not TTS_API_KEY or not TTS_ENDPOINT:
        return False
    with _state_lock:
        if _tts_limit_reached:
            if time.time() >= _sarvam_cooldown_until:
                _tts_limit_reached = False
                _sarvam_cooldown_until = 0.0
                return True
            return False
        return True


def _is_canceled(gen_id: int) -> bool:
    with _state_lock:
        return bool(_stop_playback or gen_id != _speech_gen_id)


def _create_http_session() -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=2, pool_maxsize=4, max_retries=0)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"Connection": "keep-alive", "Content-Type": "application/json"})
    return session


def _get_cached_audio(engine_name: str, text_chunk: str):
    if len(text_chunk) > _MAX_CACHE_TEXT_LEN:
        return None
    key = (engine_name, text_chunk.lower().strip())
    with _cache_lock:
        if key in _audio_cache:
            _audio_cache.move_to_end(key)
            return _audio_cache[key]
    return None


def _put_cached_audio(engine_name: str, text_chunk: str, audio_bytes: bytes):
    if not audio_bytes or len(text_chunk) > _MAX_CACHE_TEXT_LEN:
        return
    key = (engine_name, text_chunk.lower().strip())
    with _cache_lock:
        _audio_cache[key] = audio_bytes
        _audio_cache.move_to_end(key)
        while len(_audio_cache) > _MAX_CACHE_ITEMS:
            _audio_cache.popitem(last=False)


def _get_cached_edge_timings(cache_key: str):
    with _cache_lock:
        if cache_key in _edge_timings_cache:
            _edge_timings_cache.move_to_end(cache_key)
            return _edge_timings_cache[cache_key]
    return None


def _put_cached_edge_timings(cache_key: str, word_timings: list):
    if not word_timings:
        return
    with _cache_lock:
        _edge_timings_cache[cache_key] = word_timings
        _edge_timings_cache.move_to_end(cache_key)
        while len(_edge_timings_cache) > _MAX_CACHE_ITEMS:
            _edge_timings_cache.popitem(last=False)


def _compute_weighted_word_timings(sentence: str, duration: float) -> list:
    if not sentence or duration <= 0:
        return []
    words = sentence.split()
    if not words:
        return []
    weights = [len(w) + 2 for w in words]
    total = float(sum(weights))
    if total <= 0:
        return []
    timings = []
    cumulative = 0.0
    for w, wt in zip(words, weights):
        s = cumulative / total * duration
        cumulative += wt
        e = cumulative / total * duration
        timings.append({"w": w, "s": s, "e": e})
    return timings


def _write_word_sync(sentence_id, sentence, audio_start_ts, duration, words):
    try:
        os.makedirs("Data", exist_ok=True)
        payload = {
            "active": True,
            "sentence_id": int(sentence_id),
            "sentence": str(sentence),
            "audio_start_ts": float(audio_start_ts),
            "duration": float(duration),
            "words": words,
            "ts": time.time()
        }
        tmp_path = TTS_WORD_SYNC_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, TTS_WORD_SYNC_FILE)
    except Exception:
        pass


def _clear_word_sync():
    try:
        os.makedirs("Data", exist_ok=True)
        payload = {"active": False, "ts": time.time()}
        tmp_path = TTS_WORD_SYNC_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, TTS_WORD_SYNC_FILE)
    except Exception:
        pass


def _write_tts_state():
    try:
        os.makedirs("Data", exist_ok=True)
        with _state_lock:
            payload = {
                "speaking": bool(is_speaking),
                "text": _last_spoken_text,
                "ts": time.time()
            }
        tmp_path = TTS_STATUS_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, TTS_STATUS_FILE)
    except Exception:
        pass


def respeak_last():
    with _state_lock:
        last_text = _last_spoken_text
    if last_text and last_text.strip():
        threading.Thread(target=speak, args=(last_text,), daemon=True).start()


def is_currently_speaking():
    with _state_lock:
        return bool(is_speaking)


def _tts_command_watcher_loop():
    global _tts_command_watcher_running
    _tts_command_watcher_running = True
    processing_file = TTS_COMMAND_FILE + ".processing"
    while _tts_command_watcher_running:
        try:
            if os.path.exists(processing_file):
                try:
                    os.remove(processing_file)
                except Exception:
                    pass

            if os.path.exists(TTS_COMMAND_FILE):
                renamed = False
                try:
                    os.rename(TTS_COMMAND_FILE, processing_file)
                    renamed = True
                except (OSError, FileNotFoundError):
                    renamed = False

                if renamed:
                    data = None
                    try:
                        with open(processing_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except Exception:
                        data = None

                    try:
                        os.remove(processing_file)
                    except Exception:
                        pass

                    if isinstance(data, dict):
                        action = str(data.get("action", "")).strip()
                        cmd_ts = float(data.get("ts", 0.0) or 0.0)
                        if action == "stop":
                            with _state_lock:
                                current_start = _start_time
                            if cmd_ts <= 0.0 or current_start <= 0.0 or cmd_ts >= (current_start - 0.05):
                                stop_speaking()
                        elif action == "respeak":
                            respeak_last()
        except Exception:
            pass
        time.sleep(0.10)


def _strip_bracketed_text(text: str) -> str:
    if not text:
        return ""
    prev = None
    while prev != text and "[" in text and "]" in text:
        prev = text
        text = re.sub(r'\[[^\][\r\n]*\]', ' ', text)
    text = re.sub(r'\[.*?\]', ' ', text, flags=re.DOTALL)
    text = re.sub(r'[\[\]]', ' ', text)
    return text


def clean_text_for_speech(text: str) -> str:
    if not text:
        return ""
    try:
        text = re.sub(r'```.*?```', ' code ', text, flags=re.DOTALL)
        text = re.sub(r'`([^`]*)`', r'\1', text)
        text = re.sub(r'!\[.*?\]\(.*?\)', ' ', text)
        text = re.sub(r'\[([^\]]*)\]\((?:https?\vert{}file\vert{}imgpreview\vert{}preview)://[^)]*\)', r'\1', text, flags=re.IGNORECASE)
        text = _strip_bracketed_text(text)
        text = re.sub(r'http[s]?://\S+', ' link ', text)

        for pattern, replacement in TECH_PRONUNCIATION_RULES:
            text = pattern.sub(replacement, text)

        text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'^[\*\-\+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*>\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'(\*\*|\*|__|_|~~)', '', text)
        text = re.sub(r'\|', ' ', text)
        text = EMOJI_PATTERN.sub(' ', text)
        text = re.sub(r'[\u200B-\u200F\u202A-\u202E\uFEFF]', '', text)
        text = re.sub(r'[/\\@^~<>]', ' ', text)
        text = re.sub(r'\.{3,}', '.', text)
        text = re.sub(r'[!?]{2,}', lambda m: m.group()[0], text)
        text = re.sub(r'[,;:]{2,}', ',', text)
        text = re.sub(r'\(\s*\)', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text if _has_speakable_content(text) else ""
    except Exception as e:
        logger.error(f"Text cleaning error: {e}")
        return text.strip()


def _split_by_word_boundary(text: str, max_len: int = 180) -> list:
    words = text.split()
    if not words:
        return []
    chunks = []
    current = words[0]
    for w in words[1:]:
        if len(current) + 1 + len(w) <= max_len:
            current += " " + w
        else:
            if _has_speakable_content(current):
                chunks.append(current)
            if len(w) <= max_len:
                current = w
            else:
                for i in range(0, len(w), max_len):
                    part = w[i:i + max_len]
                    if i + max_len < len(w):
                        if _has_speakable_content(part):
                            chunks.append(part)
                    else:
                        current = part
    if current and _has_speakable_content(current):
        chunks.append(current)
    return chunks


def smart_split_into_sentences(text: str) -> list:
    if not text or not _has_speakable_content(text):
        return []
    try:
        pattern = (
            r'(?<!\bMr)(?<!\bDr)(?<!\bMs)(?<!\bMrs)(?<!\bProf)'
            r'(?<!\bSr)(?<!\bJr)(?<!\bSt)(?<!\bNo)(?<!\bRs)'
            r'(?<!\be\.g)(?<!\bi\.e)(?<!\betc)(?<!\bvs)(?<!\bFig)'
            r'(?<=[.!?\n])\s+'
        )
        raw_sentences = []
        for block in text.splitlines():
            block = block.strip()
            if block:
                raw_sentences.extend(re.split(pattern, block))

        final_chunks = []

        for s in raw_sentences:
            s = s.strip()
            if not _has_speakable_content(s):
                continue

            if not final_chunks and len(s) > 65:
                clause_match = re.match(r'^(.{15,70}?[,;:—])\s+(.+)$', s)
                if clause_match:
                    first_part = clause_match.group(1).strip()
                    rest_part = clause_match.group(2).strip()
                    if _has_speakable_content(first_part) and _has_speakable_content(rest_part):
                        final_chunks.append(first_part)
                        s = rest_part

            if len(s) > 180:
                sub_parts = [sc.strip() for sc in re.split(r'(?<=[,;:])\s+', s) if _has_speakable_content(sc)]
                for sub in sub_parts:
                    if len(sub) > 180:
                        final_chunks.extend(_split_by_word_boundary(sub, 180))
                    else:
                        final_chunks.append(sub)
            else:
                final_chunks.append(s)

        return final_chunks if final_chunks else _split_by_word_boundary(text, 180)
    except Exception as e:
        logger.error(f"Sentence split error: {e}")
        return _split_by_word_boundary(text, 180)


def _drain_audio_queue():
    cleared = 0
    while True:
        try:
            _audio_queue.get_nowait()
            _audio_queue.task_done()
            cleared += 1
        except queue.Empty:
            break
        except ValueError:
            break
    return cleared


def stop_speaking():
    global _stop_playback, is_speaking, _speech_gen_id
    logger.debug("Stopping TTS playback")
    with _state_lock:
        _speech_gen_id += 1
        _stop_playback = True
        is_speaking = False

    if _audio_output_available:
        try:
            pygame.mixer.stop()
        except Exception:
            pass
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass

    cleared = _drain_audio_queue()
    if cleared:
        logger.debug(f"Cleared {cleared} queued audio chunks")

    _write_tts_state()
    _clear_word_sync()


def _try_repair_edge_clock_skew(err: Exception) -> bool:
    try:
        from edge_tts.drm import DRM
        if hasattr(DRM, "handle_client_response_error"):
            DRM.handle_client_response_error(err)
            return True
    except Exception:
        pass
    return False


async def _fetch_edge_tts_fallback(sentence: str, gen_id: int) -> tuple:
    clean_sentence = _strip_bracketed_text(sentence).strip()
    if not _has_speakable_content(clean_sentence):
        return b"", None, False

    cache_key = clean_sentence.lower().strip()
    cached = _get_cached_audio("edge", clean_sentence)
    if cached:
        cached_timings = _get_cached_edge_timings(cache_key)
        return cached, cached_timings, False

    saw_403 = False
    for attempt in range(2):
        if _is_canceled(gen_id):
            return b"", None, False

        try:
            communicate = edge_tts.Communicate(
                clean_sentence, EDGE_TTS_VOICE, rate='+18%', pitch='-4Hz'
            )
            audio_bytes = bytearray()
            word_timings = []

            async def _collect_stream():
                async for chunk in communicate.stream():
                    if _is_canceled(gen_id):
                        return False
                    chunk_type = chunk.get("type")
                    if chunk_type == "audio":
                        audio_bytes.extend(chunk["data"])
                    elif chunk_type == "WordBoundary":
                        offset_ticks = int(chunk.get("offset", 0))
                        duration_ticks = int(chunk.get("duration", 0))
                        word_text = chunk.get("text", "")
                        if word_text:
                            word_timings.append({
                                "w": word_text,
                                "s": offset_ticks / 10_000_000.0,
                                "e": (offset_ticks + duration_ticks) / 10_000_000.0
                            })
                return True

            completed = await asyncio.wait_for(_collect_stream(), timeout=_EDGE_STREAM_TIMEOUT)
            if not completed or _is_canceled(gen_id):
                return b"", None, False

            result = bytes(audio_bytes)
            if result:
                _put_cached_audio("edge", clean_sentence, result)
                if word_timings:
                    _put_cached_edge_timings(cache_key, word_timings)
                return result, (word_timings if word_timings else None), False
        except Exception as e:
            err_str = str(e)
            if "403" in err_str:
                saw_403 = True
                if attempt == 0 and _try_repair_edge_clock_skew(e) and not _is_canceled(gen_id):
                    continue
                logger.warning("Edge-TTS returned 403 (blocked/clock-skew) — switching to Google TTS fallback")
                break
            if attempt == 0 and not _is_canceled(gen_id):
                await asyncio.sleep(0.15)
                continue
            logger.error(f"Edge-TTS failed: {e}")

    return b"", None, saw_403


def _fetch_google_tts_fallback(http_session: requests.Session, sentence: str, gen_id: int) -> bytes:
    clean_sentence = _strip_bracketed_text(sentence).strip()
    if not _has_speakable_content(clean_sentence) or _is_canceled(gen_id):
        return b""

    cached = _get_cached_audio("gtts", clean_sentence)
    if cached:
        return cached

    try:
        sub_chunks = _split_by_word_boundary(clean_sentence, 160)
        combined = bytearray()
        for part in sub_chunks:
            if _is_canceled(gen_id):
                return b""
            resp = http_session.get(
                "https://translate.googleapis.com/translate_tts",
                params={
                    "ie": "UTF-8",
                    "client": "tw-ob",
                    "tl": "hi",
                    "q": part,
                },
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    )
                },
                timeout=(2.5, 5.0),
            )
            if resp.status_code == 200 and resp.content and len(resp.content) >= 100:
                combined.extend(resp.content)
            else:
                return b""

        result = bytes(combined)
        if result and len(result) >= 100:
            _put_cached_audio("gtts", clean_sentence, result)
            return result
    except Exception as e:
        logger.error(f"Google TTS fallback failed: {e}")

    return b""


def _producer_thread(sentences: list, gen_id: int, producer_done: threading.Event):
    global _sarvam_consecutive_failures

    sarvam_count = 0
    edge_count = 0
    gtts_count = 0
    failed_count = 0
    edge_loop = None
    sarvam_failed_in_session = False
    sarvam_timeouts_in_session = 0
    edge_blocked_in_session = False
    http_session = _create_http_session()

    try:
        for raw_sentence in sentences:
            if _is_canceled(gen_id):
                break

            sentence = _strip_bracketed_text(raw_sentence).strip()
            if not _has_speakable_content(sentence):
                continue

            audio_data = None
            word_timings = None

            if not sarvam_failed_in_session and _is_sarvam_available():
                cached = _get_cached_audio("sarvam", sentence)
                if cached:
                    audio_data = cached
                    sarvam_count += 1
                else:
                    payload = {
                        "inputs": [sentence],
                        "target_language_code": "hi-IN",
                        "speaker": "aditya_hi_conversational",
                        "model": "bulbul:v4-flash",
                        "pace": 1.25,
                        "speech_sample_rate": 24000
                    }

                    try:
                        response = http_session.post(
                            TTS_ENDPOINT,
                            json=payload,
                            headers={"api-subscription-key": TTS_API_KEY},
                            timeout=(2.5, 6.5)
                        )

                        if response.status_code == 200:
                            body = response.json()
                            audios = body.get("audios")
                            if audios and audios[0]:
                                decoded = base64.b64decode(audios[0])
                                if decoded and len(decoded) >= 100:
                                    audio_data = decoded
                                    _put_cached_audio("sarvam", sentence, audio_data)
                                    sarvam_count += 1
                                    sarvam_timeouts_in_session = 0
                                    with _state_lock:
                                        _sarvam_consecutive_failures = 0
                            if not audio_data:
                                sarvam_failed_in_session = True
                                _set_sarvam_cooldown(_SARVAM_COOLDOWN_SECONDS)
                                logger.warning(
                                    "Sarvam returned 200 without valid audio — switching to fallback for 15m"
                                )
                        else:
                            sarvam_failed_in_session = True
                            body_lower = ""
                            try:
                                body_lower = response.text.lower()
                            except Exception:
                                pass

                            quota_keywords = (
                                "limit", "quota", "exceed", "expired",
                                "unauthorized", "subscription", "insufficient",
                                "credits", "forbidden", "payment", "rate"
                            )
                            is_quota_or_auth = (
                                response.status_code in (401, 402, 403, 429)
                                or any(kw in body_lower for kw in quota_keywords)
                            )

                            if is_quota_or_auth:
                                _set_sarvam_cooldown(_SARVAM_COOLDOWN_SECONDS)
                                logger.warning(
                                    f"Sarvam quota/auth error {response.status_code} — switching to fallback for 15m"
                                )
                            else:
                                with _state_lock:
                                    _sarvam_consecutive_failures += 1
                                    consec = _sarvam_consecutive_failures
                                if consec >= 2:
                                    _set_sarvam_cooldown(_SARVAM_TRANSIENT_COOLDOWN_SECONDS)
                                    logger.warning(
                                        f"Sarvam error {response.status_code} (repeated) — switching to fallback for 2m"
                                    )
                                else:
                                    logger.warning(
                                        f"Sarvam TTS error {response.status_code} — falling back"
                                    )
                    except requests.exceptions.Timeout as e:
                        sarvam_timeouts_in_session += 1
                        if sarvam_timeouts_in_session >= 2:
                            sarvam_failed_in_session = True
                        logger.warning(f"Sarvam TTS timeout ({e}) — falling back for this chunk")
                    except Exception as e:
                        sarvam_failed_in_session = True
                        with _state_lock:
                            _sarvam_consecutive_failures += 1
                            consec = _sarvam_consecutive_failures
                        if consec >= 2:
                            _set_sarvam_cooldown(_SARVAM_TRANSIENT_COOLDOWN_SECONDS)
                            logger.warning(f"Sarvam request failed repeatedly ({e}) — switching to fallback for 2m")
                        else:
                            logger.warning(f"Sarvam TTS request failed ({e}) — falling back")

            if (
                not audio_data
                and EDGE_TTS_AVAILABLE
                and not edge_blocked_in_session
                and not _is_canceled(gen_id)
            ):
                try:
                    if edge_loop is None:
                        edge_loop = asyncio.new_event_loop()
                    audio_data, word_timings, saw_403 = edge_loop.run_until_complete(
                        _fetch_edge_tts_fallback(sentence, gen_id)
                    )
                    if saw_403:
                        edge_blocked_in_session = True
                    if audio_data:
                        edge_count += 1
                except Exception as e:
                    logger.error(f"Edge-TTS execution failed: {e}")
                    audio_data = None
                    word_timings = None

            if not audio_data and not _is_canceled(gen_id):
                audio_data = _fetch_google_tts_fallback(http_session, sentence, gen_id)
                if audio_data:
                    gtts_count += 1

            if _is_canceled(gen_id):
                break

            if audio_data and len(audio_data) >= 100:
                _audio_queue.put((gen_id, audio_data, sentence, word_timings))
            else:
                failed_count += 1

        logger.info(
            f"TTS producer done | sarvam={sarvam_count} edge={edge_count} gtts={gtts_count} failed={failed_count}"
        )
    finally:
        producer_done.set()
        if not _is_canceled(gen_id):
            try:
                _audio_queue.put((gen_id, None, "", None), timeout=2.0)
            except Exception:
                pass
        try:
            http_session.close()
        except Exception:
            pass
        if edge_loop is not None:
            try:
                edge_loop.close()
            except Exception:
                pass


def _estimate_duration(chunk: bytes, word_timings: list, sentence: str) -> float:
    if word_timings:
        try:
            last_end = float(word_timings[-1].get("e", 0.0) or 0.0)
            if last_end > 0.05:
                return last_end + 0.08
        except Exception:
            pass
    if chunk:
        est = len(chunk) / 6000.0
        if est > 0.15:
            return est
    words = len(sentence.split()) if sentence else 1
    return max(0.4, words * 0.32)


def _play_chunk_on_mixer(chunk: bytes, sentence: str, word_timings: list, gen_id: int, clock) -> bool:
    global _last_sentence_id

    if not _ensure_mixer_ready():
        return False

    sound = None
    try:
        sound = pygame.mixer.Sound(BytesIO(chunk))
    except Exception:
        try:
            pygame.mixer.quit()
        except Exception:
            pass
        if _ensure_mixer_ready():
            try:
                sound = pygame.mixer.Sound(BytesIO(chunk))
            except Exception:
                sound = None

    if _is_canceled(gen_id):
        return False

    if sound is not None:
        try:
            duration = float(sound.get_length())
        except Exception:
            duration = 0.0

        if duration <= 0.05:
            duration = _estimate_duration(chunk, word_timings, sentence)

        if not word_timings and duration > 0 and sentence:
            word_timings = _compute_weighted_word_timings(sentence, duration)

        try:
            channel = sound.play()
            audio_start = time.time()

            if word_timings and sentence:
                _last_sentence_id += 1
                _write_word_sync(_last_sentence_id, sentence, audio_start, duration, word_timings)

            while (channel.get_busy() if channel is not None else pygame.mixer.get_busy()):
                if _is_canceled(gen_id):
                    pygame.mixer.stop()
                    return False
                clock.tick(60)
            return True
        except Exception as e:
            logger.warning(f"Sound.play fallback to music stream: {e}")

    try:
        duration = _estimate_duration(chunk, word_timings, sentence)
        if not word_timings and duration > 0 and sentence:
            word_timings = _compute_weighted_word_timings(sentence, duration)

        pygame.mixer.music.load(BytesIO(chunk))
        pygame.mixer.music.play()
        audio_start = time.time()

        if word_timings and sentence:
            _last_sentence_id += 1
            _write_word_sync(_last_sentence_id, sentence, audio_start, duration, word_timings)

        while pygame.mixer.music.get_busy():
            if _is_canceled(gen_id):
                pygame.mixer.music.stop()
                return False
            clock.tick(60)
        return True
    except Exception as e:
        logger.error(f"Audio playback failed completely: {e}")
        return False


def _consumer_thread(gen_id: int, producer_done: threading.Event):
    first_chunk = True
    clock = pygame.time.Clock()
    last_activity_ts = time.time()

    while not _is_canceled(gen_id):
        try:
            item = _audio_queue.get(timeout=0.4)
            last_activity_ts = time.time()
        except queue.Empty:
            if _is_canceled(gen_id):
                break
            if producer_done.is_set() and _audio_queue.empty():
                break
            if (time.time() - last_activity_ts) > 25.0:
                logger.warning("Consumer timed out waiting for producer audio")
                break
            continue

        sentence = ""
        word_timings = None
        if isinstance(item, tuple):
            if len(item) == 4:
                item_gen_id, chunk, sentence, word_timings = item
            elif len(item) == 2:
                item_gen_id, chunk = item
            else:
                item_gen_id, chunk = gen_id, item
        else:
            item_gen_id, chunk = gen_id, item

        if item_gen_id != gen_id or _is_canceled(gen_id):
            try:
                _audio_queue.task_done()
            except ValueError:
                pass
            continue

        if chunk is None:
            try:
                _audio_queue.task_done()
            except ValueError:
                pass
            break

        if not chunk or len(chunk) < 100:
            try:
                _audio_queue.task_done()
            except ValueError:
                pass
            continue

        if first_chunk:
            with _state_lock:
                started_at = _start_time
            reaction_time = time.time() - started_at
            logger.info(f"First audio in {reaction_time:.2f}s")
            first_chunk = False

        _play_chunk_on_mixer(chunk, sentence, word_timings, gen_id, clock)
        last_activity_ts = time.time()

        try:
            _audio_queue.task_done()
        except ValueError:
            pass


def speak(text: str):
    global _stop_playback, is_speaking, _start_time, _speech_gen_id, _last_spoken_text

    if not text:
        return

    cleaned = clean_text_for_speech(text)
    if not cleaned:
        return

    if not _ensure_mixer_ready():
        logger.info(f"TTS text-only: {cleaned}")
        return

    stop_speaking()

    with _state_lock:
        _last_spoken_text = cleaned
        _speech_gen_id += 1
        current_gen_id = _speech_gen_id
        _stop_playback = False
        is_speaking = True
        _start_time = time.time()

    _write_tts_state()
    _drain_audio_queue()

    sentences = smart_split_into_sentences(cleaned)
    if not sentences:
        with _state_lock:
            if _speech_gen_id == current_gen_id:
                is_speaking = False
        _write_tts_state()
        return

    logger.info(f"TTS speaking | sentences={len(sentences)} chars={len(cleaned)}")

    producer_done = threading.Event()
    try:
        producer = threading.Thread(
            target=_producer_thread,
            args=(sentences, current_gen_id, producer_done),
            daemon=True
        )
        producer.start()
        _consumer_thread(current_gen_id, producer_done)
    finally:
        with _state_lock:
            is_current = (_speech_gen_id == current_gen_id)
            if is_current:
                is_speaking = False
        _write_tts_state()
        if is_current:
            _clear_word_sync()


def cleanup_temp():
    global _tts_command_watcher_running
    _tts_command_watcher_running = False
    stop_speaking()
    if _audio_output_available:
        try:
            pygame.mixer.quit()
        except Exception:
            pass


os.makedirs("Data", exist_ok=True)
_load_quota_state()
_write_tts_state()
threading.Thread(
    target=_tts_command_watcher_loop,
    daemon=True,
    name="TTSCommandWatcher"
).start()


if __name__ == "__main__":
    speak("[happy] Namaste boss! System live hai. [thinking] TTS pipeline test ho raha hai.")
    speak("Yeh engine network drops aur [ignore_this] brackets ko handle karta hai. Sab smooth chal raha hai.")