import os
import sys
import time
import math
import array
import shutil
import zipfile
import threading
import queue
import urllib.request
import pyaudio
import json
import webrtcvad
from collections import deque
from vosk import Model as VoskModel, KaldiRecognizer, SetLogLevel
from dotenv import load_dotenv

from core.logger.logger import logger
from core.voice.stt_status import update_stt_status
from core.voice import interrupt
from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions

load_dotenv()
SetLogLevel(-1)

AZURE_SPEECH_KEY = "fake_key_for_ondevice"
AZURE_SPEECH_REGION = "eastasia"
AZURE_KEYWORD_MODEL_PATH = "Data/model/azure/jarvis.table"
AZURE_TTS_VOICE = "hi-IN-MadhurNeural"

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")

if not DEEPGRAM_API_KEY:
    logger.error("DEEPGRAM_API_KEY missing in .env file!")
else:
    logger.info("Deepgram API Key found.")

deepgram = DeepgramClient(DEEPGRAM_API_KEY) if DEEPGRAM_API_KEY else None
update_stt_status("idle", "")

VOSK_MODEL_NAME = "vosk-model-small-en-in-0.4"
VOSK_MODEL_URL = f"https://alphacephei.com/vosk/models/{VOSK_MODEL_NAME}.zip"
VOSK_MODEL_PATH = "Data/model/vosk-model-small"
VOSK_DOWNLOAD_DIR = "Data/model/_download"


def download_vosk_model():
    os.makedirs(VOSK_DOWNLOAD_DIR, exist_ok=True)
    zip_path = os.path.join(VOSK_DOWNLOAD_DIR, f"{VOSK_MODEL_NAME}.zip")

    logger.info(f"Downloading Vosk model from {VOSK_MODEL_URL} ...")

    last_percent = [-1]

    def _reporthook(block_num, block_size, total_size):
        if total_size > 0:
            downloaded = block_num * block_size
            percent = min(100, downloaded * 100 // total_size)
            if percent != last_percent[0]:
                last_percent[0] = percent
                bar_length = 30
                filled = int(bar_length * percent // 100)
                bar = "█" * filled + "░" * (bar_length - filled)
                sys.stdout.write(f"\r  Vosk model download: [{bar}] {percent}%")
                sys.stdout.flush()

    try:
        urllib.request.urlretrieve(VOSK_MODEL_URL, zip_path, reporthook=_reporthook)
        sys.stdout.write("\n")
        sys.stdout.flush()
        logger.info("Vosk model download complete. Extracting...")

        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(VOSK_DOWNLOAD_DIR)

        extracted_path = os.path.join(VOSK_DOWNLOAD_DIR, VOSK_MODEL_NAME)

        if not os.path.exists(extracted_path):
            for item in os.listdir(VOSK_DOWNLOAD_DIR):
                full = os.path.join(VOSK_DOWNLOAD_DIR, item)
                if os.path.isdir(full) and item.startswith("vosk-model"):
                    extracted_path = full
                    break

        if not os.path.exists(extracted_path):
            raise RuntimeError("Extracted Vosk model folder not found.")

        if os.path.exists(VOSK_MODEL_PATH):
            shutil.rmtree(VOSK_MODEL_PATH)

        os.makedirs(os.path.dirname(VOSK_MODEL_PATH), exist_ok=True)
        shutil.move(extracted_path, VOSK_MODEL_PATH)
        logger.info(f"Vosk model ready at: {VOSK_MODEL_PATH}")

    except Exception as e:
        sys.stdout.write("\n")
        sys.stdout.flush()
        logger.error(f"Failed to download Vosk model: {e}", exc_info=True)
        raise
    finally:
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except Exception:
                pass
        if os.path.exists(VOSK_DOWNLOAD_DIR):
            try:
                shutil.rmtree(VOSK_DOWNLOAD_DIR)
            except Exception:
                pass


class UnifiedVoiceAssistant:
    def __init__(self):
        self.mic_available = False
        self.running = True
        self.command_queue = queue.Queue()
        self.vosk_recognizer = None
        self.audio = None
        self.stream = None
        self.dg_connection = None
        self.is_awake = False
        self.connection_established = False
        self.RATE = 16000
        self.CHUNK = 480

        self.use_azure_wake = False
        self.azure_speechsdk = None
        self.azure_speech_config = None
        self.azure_keyword_model = None
        self.azure_recognizer = None
        self.azure_push_stream = None
        self.azure_wake_detected = threading.Event()
        self.azure_needs_restart = threading.Event()
        self.azure_matched_text = "jarvis (azure)"

        self.frame_history = deque(maxlen=45)
        self._hp_prev_x = 0.0
        self._hp_prev_y = 0.0
        self._pre_prev_y = 0.0
        self._noise_floor_rms = 60.0
        self._agc_gain = 1.2
        self._last_reset_time = 0.0

        try:
            self.vad = webrtcvad.Vad(2)
            self.strict_vad = webrtcvad.Vad(3)
            self.MAX_SILENCE_TIMEOUT = 1.0
            self.MAX_WAIT_TIMEOUT = 4.0

            self.WAKE_WORDS = ["jarvis", "hey jarvis"]
            self.DECOY_WORDS = [
                "hello", "computer", "hi", "okay", "yes", "no", "stop",
                "test", "mike", "testing", "one",
                "two", "three", "alpha", "beta", "noise", "background", "something"
            ]

            self._init_wake_engine()

            self.audio = pyaudio.PyAudio()
            self.stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.RATE,
                input=True,
                frames_per_buffer=self.CHUNK
            )

            self.current_transcript = ""
            self.live_text = ""
            self.command_done = threading.Event()
            self.last_speech_time = 0
            self.wake_time = 0
            self.has_spoken = False

            self.mic_available = True
            logger.info("Microphone initialized successfully. Voice input ENABLED.")

        except Exception as e:
            self.mic_available = False
            logger.warning(f"Microphone not available — voice input DISABLED. Reason: {e}")

    def _init_wake_engine(self):
        if os.path.exists(AZURE_KEYWORD_MODEL_PATH):
            try:
                import azure.cognitiveservices.speech as speechsdk
                self.azure_speechsdk = speechsdk
                self.azure_speech_config = speechsdk.SpeechConfig(
                    subscription=AZURE_SPEECH_KEY,
                    region=AZURE_SPEECH_REGION
                )
                self.azure_keyword_model = speechsdk.KeywordRecognitionModel(AZURE_KEYWORD_MODEL_PATH)
                self.use_azure_wake = True
                logger.info(f"✅ Azure wake word engine ENABLED | model={AZURE_KEYWORD_MODEL_PATH}")
                return
            except Exception as e:
                logger.warning(f"⚠️ Azure wake word init failed: {e}. Falling back to Vosk.")
                self.use_azure_wake = False

        if not os.path.exists(VOSK_MODEL_PATH):
            logger.warning(f"Vosk model not found at '{VOSK_MODEL_PATH}'. Auto-downloading...")
            download_vosk_model()

        self.vosk_model = VoskModel(VOSK_MODEL_PATH)
        grammar = json.dumps(self.WAKE_WORDS + self.DECOY_WORDS + ["[unk]"])
        self.vosk_recognizer = KaldiRecognizer(self.vosk_model, self.RATE, grammar)
        logger.info("✅ Vosk wake word engine ENABLED (fallback)")

    def _reset_dsp_state(self):
        self._hp_prev_x = 0.0
        self._hp_prev_y = 0.0
        self._pre_prev_y = 0.0
        self.frame_history.clear()

    def _analyze_and_condition_audio(self, pcm_data):
        try:
            is_speech_v2 = self.vad.is_speech(pcm_data, self.RATE)
        except Exception:
            is_speech_v2 = False

        try:
            is_speech_v3 = self.strict_vad.is_speech(pcm_data, self.RATE)
        except Exception:
            is_speech_v3 = False

        samples = array.array("h")
        samples.frombytes(pcm_data)
        n_samples = len(samples)
        if n_samples == 0:
            return pcm_data

        sum_sq = 0.0
        zero_crossings = 0
        prev_raw = samples[0]

        hp_prev_x = self._hp_prev_x
        hp_prev_y = self._hp_prev_y
        pre_prev_y = self._pre_prev_y
        hp_alpha = 0.967
        pre_coeff = 0.14

        filtered = [0.0] * n_samples

        for i in range(n_samples):
            x = float(samples[i])
            sum_sq += x * x
            if (x >= 0.0 and prev_raw < 0.0) or (x < 0.0 and prev_raw >= 0.0):
                zero_crossings += 1
            prev_raw = x

            hp_y = hp_alpha * (hp_prev_y + x - hp_prev_x)
            hp_prev_x = x
            hp_prev_y = hp_y

            out_y = hp_y - pre_coeff * pre_prev_y
            pre_prev_y = hp_y
            filtered[i] = out_y

        self._hp_prev_x = hp_prev_x
        self._hp_prev_y = hp_prev_y
        self._pre_prev_y = pre_prev_y

        raw_rms = math.sqrt(sum_sq / n_samples)
        zcr = zero_crossings / float(n_samples)

        if not is_speech_v2 and raw_rms < max(450.0, self._noise_floor_rms * 1.8):
            self._noise_floor_rms = (0.96 * self._noise_floor_rms) + (0.04 * max(25.0, raw_rms))

        if is_speech_v2 or raw_rms > (self._noise_floor_rms * 1.4):
            if raw_rms > 60.0:
                desired_gain = 2300.0 / raw_rms
                desired_gain = max(1.0, min(2.6, desired_gain))
                self._agc_gain = (0.75 * self._agc_gain) + (0.25 * desired_gain)
        else:
            self._agc_gain = (0.90 * self._agc_gain) + (0.10 * 1.0)

        gain = self._agc_gain
        out_samples = array.array("h", [0] * n_samples)
        cond_sum_sq = 0.0

        for i in range(n_samples):
            val = int(filtered[i] * gain)
            if val > 32767:
                val = 32767
            elif val < -32768:
                val = -32768
            out_samples[i] = val
            cond_sum_sq += float(val) * float(val)

        cond_rms = math.sqrt(cond_sum_sq / n_samples)
        self.frame_history.append((is_speech_v2, is_speech_v3, raw_rms, cond_rms, zcr))
        return out_samples.tobytes()

    def _extract_duration_ms(self, result):
        dur = getattr(result, "duration", 0)
        if hasattr(dur, "total_seconds"):
            return dur.total_seconds() * 1000.0
        if isinstance(dur, (int, float)) and dur > 0:
            if dur > 10000:
                return float(dur) / 10000.0
            return float(dur)
        return 0.0

    def _verify_azure_wake(self, text, duration_ms):
        now = time.time()
        if (now - self._last_reset_time) < 0.35:
            logger.debug("Azure wake rejected: post-reset cooldown active.")
            return False

        clean_text = (text or "").strip().lower()
        if clean_text and "jarvis" not in clean_text:
            logger.debug(f"Azure wake rejected: mismatched keyword text '{clean_text}'.")
            return False

        if duration_ms > 0.0 and (duration_ms < 210.0 or duration_ms > 1950.0):
            logger.debug(f"Azure wake rejected: invalid duration {duration_ms:.1f}ms.")
            return False

        frames = list(self.frame_history)
        if len(frames) < 6:
            return True

        v2_count = 0
        v3_count = 0
        max_contiguous_v2 = 0
        current_run = 0
        gap_allowance = 0
        speech_rms_values = []
        speech_zcr_values = []

        for is_v2, is_v3, raw_rms, cond_rms, zcr in frames:
            if is_v2:
                v2_count += 1
                current_run += 1
                gap_allowance = 0
                speech_rms_values.append(raw_rms)
                speech_zcr_values.append(zcr)
                if current_run > max_contiguous_v2:
                    max_contiguous_v2 = current_run
            else:
                if current_run > 0 and gap_allowance == 0:
                    gap_allowance = 1
                else:
                    current_run = 0
                    gap_allowance = 0
            if is_v3:
                v3_count += 1

        if v2_count < 6 or v3_count < 2 or max_contiguous_v2 < 4:
            logger.debug(
                f"Azure wake rejected: insufficient VAD density "
                f"(v2={v2_count}, v3={v3_count}, run={max_contiguous_v2})."
            )
            return False

        if not speech_rms_values:
            return False

        peak_rms = max(speech_rms_values)
        mean_rms = sum(speech_rms_values) / len(speech_rms_values)
        mean_zcr = sum(speech_zcr_values) / len(speech_zcr_values)

        min_peak_required = max(130.0, self._noise_floor_rms * 1.32)
        min_mean_required = max(85.0, self._noise_floor_rms * 1.12)

        if peak_rms < min_peak_required or mean_rms < min_mean_required:
            logger.debug(
                f"Azure wake rejected: low vocal SNR "
                f"(peak={peak_rms:.1f}, mean={mean_rms:.1f}, floor={self._noise_floor_rms:.1f})."
            )
            return False

        if mean_zcr < 0.012 or mean_zcr > 0.46:
            logger.debug(f"Azure wake rejected: out-of-bounds ZCR ({mean_zcr:.3f}).")
            return False

        return True

    def _start_azure_listening(self):
        if not self.use_azure_wake or self.azure_speechsdk is None:
            return
        try:
            self._reset_dsp_state()
            audio_format = self.azure_speechsdk.audio.AudioStreamFormat(
                samples_per_second=self.RATE,
                bits_per_sample=16,
                channels=1
            )
            self.azure_push_stream = self.azure_speechsdk.audio.PushAudioInputStream(stream_format=audio_format)
            audio_config = self.azure_speechsdk.AudioConfig(stream=self.azure_push_stream)

            self.azure_recognizer = self.azure_speechsdk.SpeechRecognizer(
                speech_config=self.azure_speech_config,
                audio_config=audio_config
            )

            def on_recognized(evt):
                if evt.result.reason == self.azure_speechsdk.ResultReason.RecognizedKeyword:
                    detected_text = getattr(evt.result, "text", "") or "jarvis"
                    duration_ms = self._extract_duration_ms(evt.result)
                    if self._verify_azure_wake(detected_text, duration_ms):
                        self.azure_matched_text = f"{detected_text} (azure)"
                        logger.info(
                            f"🎯 Azure wake word detected | text='{detected_text}' | duration={duration_ms:.0f}ms"
                        )
                        self.azure_wake_detected.set()
                    else:
                        self.azure_needs_restart.set()

            def on_canceled(evt):
                cancellation = evt.result.cancellation_details
                logger.debug(f"Azure keyword canceled | reason={cancellation.reason}")
                if not self.is_awake and not self.azure_wake_detected.is_set():
                    self.azure_needs_restart.set()

            self.azure_recognizer.recognized.connect(on_recognized)
            self.azure_recognizer.canceled.connect(on_canceled)
            self.azure_wake_detected.clear()
            self.azure_needs_restart.clear()
            self.azure_recognizer.start_keyword_recognition(self.azure_keyword_model)

            silence_preroll = b"\x00" * (self.CHUNK * 2 * 6)
            self.azure_push_stream.write(silence_preroll)
            logger.info("🎧 Azure keyword recognition started.")
        except Exception as e:
            logger.error(f"❌ Azure keyword recognition start failed: {e}")
            self.use_azure_wake = False

    def _stop_azure_listening(self):
        if self.azure_push_stream is not None:
            try:
                self.azure_push_stream.close()
            except Exception:
                pass
            finally:
                self.azure_push_stream = None

        if self.azure_recognizer is not None:
            try:
                self.azure_recognizer.recognized.disconnect_all()
                self.azure_recognizer.canceled.disconnect_all()
                self.azure_recognizer.stop_keyword_recognition()
            except Exception as e:
                logger.debug(f"Azure recognizer stop error: {e}")
            finally:
                self.azure_recognizer = None

    def start(self):
        if not self.mic_available:
            logger.warning("🎤 Mic not available. Skipping audio loop start.")
            return
        logger.info("🎧 Wake word listener thread starting...")
        self.listen_thread = threading.Thread(target=self._audio_loop, daemon=True)
        self.listen_thread.start()
        engine_name = "Azure (on-device)" if self.use_azure_wake else "Vosk"
        logger.info(f"✅ Wake word listener active | engine={engine_name} | word='Jarvis' / 'Hey Jarvis'")

    def play_wake_sound(self):
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_OK)
        except Exception as e:
            logger.error(f"Wake sound error: {e}")

    def _flush_audio_buffer(self):
        if not self.stream:
            return
        try:
            available = self.stream.get_read_available()
            if available > 0:
                self.stream.read(available, exception_on_overflow=False)
        except Exception as e:
            logger.error(f"Buffer flush error: {e}")

    def _setup_deepgram(self):
        self.current_transcript = ""
        self.live_text = ""
        self.command_done.clear()
        assistant = self

        try:
            self.dg_connection = deepgram.listen.websocket.v("1")

            def on_message(dg_self, result, **kwargs):
                sentence = result.channel.alternatives[0].transcript
                if sentence:
                    if result.is_final:
                        assistant.current_transcript += " " + sentence
                        assistant.live_text = assistant.current_transcript.strip()
                    else:
                        assistant.live_text = (assistant.current_transcript + " " + sentence).strip()
                    update_stt_status("listening", assistant.live_text)

                if getattr(result, 'speech_final', False) and assistant.live_text.strip():
                    assistant.command_done.set()

            def on_utterance_end(dg_self, utterance_end, **kwargs):
                if assistant.live_text.strip():
                    assistant.command_done.set()

            def on_error(dg_self, error, **kwargs):
                logger.error(f"Deepgram Error: {error}")
                assistant.command_done.set()

            self.dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
            self.dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)
            self.dg_connection.on(LiveTranscriptionEvents.Error, on_error)

            options = LiveOptions(
                model="nova-2",
                language="hi",
                keywords=["Jarvis:4", "Mindly:3", "Llama:2", "Gemini:2", "Kaif:3", "Youtube", "Google", "ansari"],
                smart_format=True,
                interim_results=True,
                vad_events=True,
                endpointing=800,
                utterance_end_ms="1000",
                encoding="linear16",
                channels=1,
                sample_rate=self.RATE,
            )

            if not self.dg_connection.start(options):
                logger.error("❌ Failed to start Deepgram websocket connection.")
                return False

            logger.info("🔗 Deepgram websocket connected successfully.")
            return True

        except Exception as e:
            logger.error(f"❌ Deepgram setup exception: {e}", exc_info=True)
            return False

    def _check_wake_word(self, text):
        clean_text = text.lower().strip()
        if not clean_text:
            return False
        for wake in self.WAKE_WORDS:
            if wake in clean_text:
                return True
        return False

    def _audio_loop(self):
        if not self.mic_available:
            return

        if self.use_azure_wake:
            self._start_azure_listening()

        while self.running:
            try:
                pcm_data = self.stream.read(self.CHUNK, exception_on_overflow=False)

                if not self.is_awake:
                    triggered = False
                    matched_text = ""

                    if self.use_azure_wake:
                        if self.azure_needs_restart.is_set():
                            self.azure_needs_restart.clear()
                            self._stop_azure_listening()
                            self._start_azure_listening()

                        conditioned_pcm = self._analyze_and_condition_audio(pcm_data)

                        if self.azure_push_stream is not None:
                            try:
                                self.azure_push_stream.write(conditioned_pcm)
                            except Exception as e:
                                logger.error(f"Azure push stream write failed: {e}")
                                self._stop_azure_listening()
                                self._start_azure_listening()
                                continue

                        if self.azure_wake_detected.is_set():
                            self.azure_wake_detected.clear()
                            triggered = True
                            matched_text = self.azure_matched_text
                    else:
                        is_speech = self.vad.is_speech(pcm_data, self.RATE)

                        if is_speech:
                            if self.vosk_recognizer.AcceptWaveform(pcm_data):
                                res = json.loads(self.vosk_recognizer.Result())
                                text = res.get("text", "")
                            else:
                                res = json.loads(self.vosk_recognizer.PartialResult())
                                text = res.get("partial", "")

                            if self._check_wake_word(text):
                                triggered = True
                                matched_text = text

                    if triggered:
                        logger.info(f"🎯 Wake word detected | matched_text='{matched_text}'")

                        try:
                            from core.voice import tts
                            tts.stop_speaking()
                        except Exception:
                            pass

                        interrupt.set_interrupt()
                        self._flush_audio_buffer()

                        if self.use_azure_wake:
                            self._stop_azure_listening()

                        if self.vosk_recognizer:
                            self.vosk_recognizer.Reset()

                        self.play_wake_sound()
                        update_stt_status("connecting", "Listening...")

                        if not self.connection_established or self.dg_connection is None:
                            logger.info("🔌 Establishing Deepgram connection...")
                            if self._setup_deepgram():
                                self.connection_established = True
                                logger.info("✅ Deepgram connection established.")
                            else:
                                logger.warning("⚠️ Deepgram connection failed. Returning to wake word mode.")
                                self.connection_established = False
                                self._last_reset_time = time.time()
                                if self.use_azure_wake:
                                    self._start_azure_listening()
                                elif self.vosk_recognizer:
                                    self.vosk_recognizer.Reset()
                                continue

                        self.wake_time = time.time()
                        self.last_speech_time = time.time()
                        self.has_spoken = False
                        self.is_awake = True
                        logger.info("👂 User command listening STARTED (max wait 4.0s, silence timeout 1.0s).")
                        update_stt_status("listening", "Listening...")

                else:
                    is_speech = self.vad.is_speech(pcm_data, self.RATE)
                    current_time = time.time()

                    if is_speech:
                        self.last_speech_time = current_time
                        self.has_spoken = True

                    if not self.has_spoken and (current_time - self.wake_time > self.MAX_WAIT_TIMEOUT):
                        logger.info("⏱️ No speech detected within 4.0s after wake word. Ending command capture.")
                        self.command_done.set()

                    elif self.has_spoken and (current_time - self.last_speech_time > self.MAX_SILENCE_TIMEOUT):
                        logger.info("⏱️ Silence timeout (1.0s) reached after speech. Finalizing command.")
                        self.command_done.set()

                    if self.dg_connection:
                        try:
                            self.dg_connection.send(pcm_data)
                        except Exception as e:
                            logger.error(f"❌ Lost Deepgram connection while sending audio: {e}")
                            self._force_sleep_reset()
                            continue

                    if self.command_done.is_set():
                        self.process_final_command()

            except Exception as e:
                logger.error(f"Audio loop error: {e}")
                time.sleep(0.01)

    def _force_sleep_reset(self):
        logger.warning("🔄 Force sleep reset triggered. Returning to wake word mode.")
        self.dg_connection = None
        self.connection_established = False
        self.is_awake = False
        self._last_reset_time = time.time()
        self._flush_audio_buffer()

        if self.vosk_recognizer:
            self.vosk_recognizer.Reset()

        if self.use_azure_wake:
            self._stop_azure_listening()
            self._start_azure_listening()

        update_stt_status("idle", "")
        logger.info("💤 Back to idle. Listening for wake word...")

    def process_final_command(self):
        full_command = self.live_text.lower().strip()
        ignore_words = ["", "okay", "okay.", "jarvis", "jarvis.", "thanks", "thank you",
                        "hmm", "haan", "ah", "uh", "theek hai", "hello", "ha"]

        logger.info("🛑 User command listening STOPPED. Processing transcript...")
        logger.info(f"📝 Raw transcript received: '{full_command}'")

        self.is_awake = False

        try:
            if self.dg_connection:
                self.dg_connection.finish()
        except Exception as e:
            logger.error(f"❌ Error finishing Deepgram connection: {e}")
        finally:
            self.dg_connection = None
            self.connection_established = False

        self._last_reset_time = time.time()
        self._flush_audio_buffer()

        if self.vosk_recognizer:
            self.vosk_recognizer.Reset()

        if self.use_azure_wake:
            self._stop_azure_listening()
            self._start_azure_listening()

        interrupt.clear_interrupt()

        if full_command and full_command not in ignore_words and len(full_command) > 3:
            logger.info(f"✅ Valid command accepted -> '{full_command}'")
            update_stt_status("understanding", full_command)
            self.command_queue.put(full_command)
        else:
            logger.info(f"🚫 Command rejected (empty/ignore-list/too short) -> returning to wake mode.")
            update_stt_status("idle", "")
            self.command_queue.put("")

        logger.info("💤 Idle. Waiting for next wake word 'Jarvis'...")
        time.sleep(0.1)

    def get_command(self, is_retry=False):
        command = None
        while self.running:
            try:
                command = self.command_queue.get(timeout=0.5)
                break
            except queue.Empty:
                continue

        if not self.running or command is None:
            return ""

        if command:
            logger.info(f"📤 Command delivered to processor: '{command}'")

        return command

    def stop(self):
        logger.info("🛑 Stopping voice engine...")
        self.running = False

        self._stop_azure_listening()

        try:
            if self.dg_connection:
                self.dg_connection.finish()
        except Exception:
            pass
        try:
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
        except Exception:
            pass
        try:
            if self.audio:
                self.audio.terminate()
        except Exception:
            pass
        logger.info("✅ Voice engine stopped.")


try:
    engine = UnifiedVoiceAssistant()
except Exception as e:
    logger.critical(f"Failed to initialize voice engine: {e}. Falling back to dummy engine.")

    class _DummyEngine:
        mic_available = False

        def __init__(self):
            self.mic_available = False
            self.running = False

        def start(self):
            pass

        def stop(self):
            pass

        def get_command(self, is_retry=False):
            return ""

    engine = _DummyEngine()


def start_background_wake_word_listener():
    engine.start()


def listen():
    if not getattr(engine, 'mic_available', False):
        return ""
    return engine.get_command()


if __name__ == "__main__":
    try:
        start_background_wake_word_listener()
        logger.info("Voice module ready")
        while True:
            command = listen()
            if command:
                pass
    except KeyboardInterrupt:
        engine.stop()
    except Exception as e:
        logger.error(f"Main loop fatal error: {e}", exc_info=True)
        engine.stop()