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
from requests.adapters import HTTPAdapter
from dotenv import load_dotenv

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
import pygame

load_dotenv()

from core.logger.logger import logger

_audio_output_available = False
try:
    pygame.mixer.init(frequency=24000, buffer=2048)
    _audio_output_available = True
    logger.info("Audio output enabled")
except Exception as e:
    logger.error(f"Audio init failed: {e}. Running in text-only mode.")

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
_speech_gen_id = 0
_audio_queue = queue.Queue()
_start_time = 0.0

_SARVAM_COOLDOWN_SECONDS = 900.0
_MAX_CACHE_ITEMS = 32
_MAX_CACHE_TEXT_LEN = 75
_audio_cache = OrderedDict()

_http_session = requests.Session()
_http_adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=0)
_http_session.mount("https://", _http_adapter)
_http_session.mount("http://", _http_adapter)
_http_session.headers.update({"Connection": "keep-alive", "Content-Type": "application/json"})

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


def clean_text_for_speech(text: str) -> str:
    if not text:
        return ""
    try:
        text = re.sub(r'```.*?```', ' code ', text, flags=re.DOTALL)
        text = re.sub(r'`([^`]*)`', r'\1', text)
        text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
        text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
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
        text = re.sub(r'\[\s*\]', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text
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
            chunks.append(current)
            if len(w) <= max_len:
                current = w
            else:
                for i in range(0, len(w), max_len):
                    part = w[i:i + max_len]
                    if i + max_len < len(w):
                        chunks.append(part)
                    else:
                        current = part
    if current:
        chunks.append(current)
    return chunks


def smart_split_into_sentences(text: str) -> list:
    try:
        pattern = r'(?<!\bMr)(?<!\bDr)(?<!\bMs)(?<!\bMrs)(?<!\bProf)(?<!\be\.g)(?<!\bi\.e)(?<!\betc)(?<!\bvs)\s*[.!?\n]\s+'
        sentences = re.split(pattern, text)
        final_chunks = []

        for s in sentences:
            s = s.strip()
            if not s:
                continue

            if not final_chunks and len(s) > 65:
                clause_match = re.match(r'^(.{12,70}?[,;:—\-])\s+(.+)$', s)
                if clause_match:
                    first_part = clause_match.group(1).strip()
                    rest_part = clause_match.group(2).strip()
                    if first_part:
                        final_chunks.append(first_part)
                        s = rest_part

            if len(s) > 180:
                sub_parts = [sc.strip() for sc in re.split(r'[,;:]\s+', s) if sc.strip()]
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

    cleared = _drain_audio_queue()
    if cleared:
        logger.debug(f"Cleared {cleared} queued audio chunks")


async def _fetch_edge_tts_fallback(sentence: str, gen_id: int) -> bytes:
    try:
        clean_sentence = re.sub(r'\[.*?\]', '', sentence).strip()
        if not clean_sentence:
            return b""

        cached = _get_cached_audio("edge", clean_sentence)
        if cached:
            return cached

        communicate = edge_tts.Communicate(
            clean_sentence, EDGE_TTS_VOICE, rate='+18%', pitch='-4Hz'
        )
        audio_bytes = bytearray()
        async for chunk in communicate.stream():
            if _stop_playback or gen_id != _speech_gen_id:
                return b""
            if chunk["type"] == "audio":
                audio_bytes.extend(chunk["data"])

        result = bytes(audio_bytes)
        if result and not _stop_playback and gen_id == _speech_gen_id:
            _put_cached_audio("edge", clean_sentence, result)
        return result
    except Exception as e:
        logger.error(f"Edge-TTS failed: {e}")
        return b""


def _producer_thread(sentences: list, gen_id: int):
    global _stop_playback, _tts_limit_reached, _sarvam_cooldown_until

    sarvam_count = 0
    edge_count = 0
    failed_count = 0
    edge_loop = None

    try:
        for sentence in sentences:
            if _stop_playback or gen_id != _speech_gen_id:
                break

            audio_data = None

            if TTS_API_KEY and TTS_ENDPOINT and not _tts_limit_reached:
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

                    for attempt in range(2):
                        if _stop_playback or gen_id != _speech_gen_id:
                            break
                        try:
                            response = _http_session.post(
                                TTS_ENDPOINT,
                                json=payload,
                                headers={"api-subscription-key": TTS_API_KEY},
                                timeout=(3.5, 8.0)
                            )
                            if response.status_code == 200:
                                body = response.json()
                                if body.get("audios"):
                                    audio_data = base64.b64decode(body["audios"][0])
                                    if audio_data:
                                        _put_cached_audio("sarvam", sentence, audio_data)
                                        sarvam_count += 1
                                break
                            if (
                                response.status_code == 429
                                or "limit" in response.text.lower()
                                or "quota" in response.text.lower()
                            ):
                                _tts_limit_reached = True
                                _sarvam_cooldown_until = time.time() + _SARVAM_COOLDOWN_SECONDS
                                logger.warning("Sarvam quota reached, switching to Edge-TTS for 15m")
                                break
                            logger.error(f"Sarvam TTS error {response.status_code}")
                            break
                        except Exception as e:
                            if attempt == 0:
                                continue
                            logger.error(f"Sarvam TTS request failed: {e}")

            if not audio_data and EDGE_TTS_AVAILABLE and not _stop_playback and gen_id == _speech_gen_id:
                try:
                    if edge_loop is None:
                        edge_loop = asyncio.new_event_loop()
                    audio_data = edge_loop.run_until_complete(_fetch_edge_tts_fallback(sentence, gen_id))
                    if audio_data:
                        edge_count += 1
                except Exception as e:
                    logger.error(f"Edge-TTS execution failed: {e}")

            if _stop_playback or gen_id != _speech_gen_id:
                break

            if audio_data:
                _audio_queue.put((gen_id, audio_data))
            else:
                failed_count += 1

        if not _stop_playback and gen_id == _speech_gen_id:
            try:
                _audio_queue.put((gen_id, None), timeout=5)
            except queue.Full:
                logger.warning("Audio queue full, sentinel not queued")

        logger.info(
            f"TTS producer done | sarvam={sarvam_count} edge={edge_count} failed={failed_count}"
        )
    finally:
        if edge_loop is not None:
            try:
                edge_loop.close()
            except Exception:
                pass


def _consumer_thread(gen_id: int):
    global _stop_playback, _start_time
    first_chunk = True
    chunks_played = 0
    clock = pygame.time.Clock()

    while not _stop_playback and gen_id == _speech_gen_id:
        try:
            item = _audio_queue.get(timeout=20.0)
        except queue.Empty:
            logger.debug("Consumer timeout waiting for audio")
            break

        if isinstance(item, tuple) and len(item) == 2:
            item_gen_id, chunk = item
        else:
            item_gen_id, chunk = gen_id, item

        if item_gen_id != gen_id or gen_id != _speech_gen_id or _stop_playback:
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

        try:
            sound = pygame.mixer.Sound(BytesIO(chunk))
        except Exception as e:
            logger.error(f"Audio playback failed: {e}")
            try:
                _audio_queue.task_done()
            except ValueError:
                pass
            continue

        if _stop_playback or gen_id != _speech_gen_id:
            try:
                _audio_queue.task_done()
            except ValueError:
                pass
            break

        if first_chunk:
            reaction_time = time.time() - _start_time
            logger.info(f"First audio in {reaction_time:.2f}s")
            first_chunk = False

        sound.play()
        chunks_played += 1

        while pygame.mixer.get_busy():
            if _stop_playback or gen_id != _speech_gen_id:
                pygame.mixer.stop()
                break
            clock.tick(50)

        try:
            _audio_queue.task_done()
        except ValueError:
            pass


def speak(text: str):
    global _stop_playback, is_speaking, _start_time, _tts_limit_reached, _speech_gen_id

    if not text:
        return

    if not _audio_output_available:
        cleaned = clean_text_for_speech(text)
        if cleaned:
            logger.info(f"TTS text-only: {cleaned}")
        return

    cleaned = clean_text_for_speech(text)
    if not cleaned:
        return

    if is_speaking:
        stop_speaking()

    with _state_lock:
        _speech_gen_id += 1
        current_gen_id = _speech_gen_id
        if _tts_limit_reached and time.time() >= _sarvam_cooldown_until:
            _tts_limit_reached = False
        _stop_playback = False
        is_speaking = True
        _start_time = time.time()

    _drain_audio_queue()

    sentences = smart_split_into_sentences(cleaned)
    if not sentences:
        with _state_lock:
            if _speech_gen_id == current_gen_id:
                is_speaking = False
        return

    logger.info(f"TTS speaking | sentences={len(sentences)} chars={len(cleaned)}")

    try:
        producer = threading.Thread(
            target=_producer_thread,
            args=(sentences, current_gen_id),
            daemon=True
        )
        producer.start()
        _consumer_thread(current_gen_id)
    finally:
        with _state_lock:
            if _speech_gen_id == current_gen_id:
                is_speaking = False


def cleanup_temp():
    stop_speaking()
    try:
        _http_session.close()
    except Exception:
        pass
    if _audio_output_available:
        try:
            pygame.mixer.quit()
        except Exception:
            pass


if __name__ == "__main__":
    speak("Namaste boss! System live hai. TTS pipeline test ho raha hai.")
    speak("Yeh engine network drops ko handle karta hai. Sab smooth chal raha hai.")