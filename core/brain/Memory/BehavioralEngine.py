import os
import re
import json
import math
import time
import threading
from datetime import datetime
from pathlib import Path
from difflib import SequenceMatcher
from collections import deque
import numpy as np
from scipy.spatial.distance import cosine
from core.logger.logger import logger


class BehavioralLearningEngine:
    MOOD_LABELS = [
        "DEEP_FOCUS",
        "FRUSTRATED_URGENT",
        "CASUAL_CHILL",
        "TIRED_LOW_ENERGY"
    ]

    MOOD_ANCHOR_TEXTS = {
        "DEEP_FOCUS": "write python code build script architecture debug terminal analyze project file implementation database",
        "FRUSTRATED_URGENT": "error crash failed not working fix it fast galat hai phir se problem jaldi karo",
        "CASUAL_CHILL": "hello bhai joke song play youtube mausam relax baat karo story",
        "TIRED_LOW_ENERGY": "thak gaya neend aa rahi hai short summary bas chota sa batao kal karenge"
    }

    TASK_CLUSTERS = {
        "coding_debug": ["code", "python", "script", "bug", "error", "fix", "terminal", "git", "pip", "npm", "react", "api", "file"],
        "research_docs": ["search", "research", "arxiv", "paper", "summary", "explain", "analyze", "vault", "notes", "webpage", "youtube"],
        "comms_messaging": ["email", "mail", "gmail", "whatsapp", "telegram", "message", "msg", "bhejo", "send", "reply"],
        "system_media": ["open", "close", "volume", "brightness", "screenshot", "lock", "sleep", "play", "song"],
        "mobile_control": ["mobile", "phone", "termux", "hotspot", "transfer", "android", "camera", "location"],
        "schedule_planning": ["calendar", "schedule", "reminder", "meeting", "event", "plan"]
    }

    FRUSTRATION_MARKERS = {
        "error", "fail", "failed", "crash", "bug", "galat", "nahi", "nhi",
        "problem", "issue", "wtf", "yaar", "phir", "again", "ruk", "stop",
        "bekar", "wrong", "broken", "fix", "jaldi"
    }

    TIRED_MARKERS = {
        "thak", "tired", "sleep", "neend", "briefer", "short", "chota",
        "bas", "rehne", "kal", "late", "slow", "boring", "exhausted"
    }

    FOCUS_MARKERS = {
        "build", "create", "implement", "architecture", "analyze", "deep",
        "optimize", "refactor", "deploy", "code", "script", "database", "logic"
    }

    def __init__(self, memory_dir="Data/jarvis_memory"):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.memory_dir / "behavioral_state.json"
        self.weights_file = self.memory_dir / "behavioral_weights.npz"
        self._lock = threading.RLock()

        self.input_dim = 392
        self.num_moods = len(self.MOOD_LABELS)
        self.learning_rate = 0.035

        self.W_mood = np.zeros((self.num_moods, self.input_dim), dtype=np.float32)
        self.b_mood = np.zeros((self.num_moods,), dtype=np.float32)
        self.anchor_embeddings = {}

        self.voice_baseline = {
            "rms_snr_history": deque(maxlen=30),
            "zcr_history": deque(maxlen=30),
            "rate_history": deque(maxlen=30)
        }
        self.last_voice_packet = None
        self.recent_overrides = deque(maxlen=15)
        self.recent_system_failures = deque(maxlen=15)
        self.last_ui_edit_info = {"significant": False, "timestamp": 0.0, "note": ""}

        self.situational_habits = {}
        self.style_preferences = {
            "verbosity": "balanced",
            "verbosity_score": 0.0,
            "email_style": "professional_concise",
            "chat_style": "natural_hinglish",
            "edit_patterns": [],
            "override_lessons": []
        }
        self.trained_message_Hashes = set()
        self.last_prediction_cache = {}

        self._load_state()
        self._load_weights()

    def _get_time_bucket(self, dt=None):
        dt = dt or datetime.now()
        hr = dt.hour
        if 5 <= hr < 9:
            return "early_morning"
        if 9 <= hr < 13:
            return "morning"
        if 13 <= hr < 17:
            return "afternoon"
        if 17 <= hr < 21:
            return "evening"
        if 21 <= hr <= 23:
            return "night"
        return "late_night"

    def _detect_task_cluster(self, text):
        low = str(text or "").lower()
        best_cluster = "general_agentic"
        best_hits = 0
        for cluster, words in self.TASK_CLUSTERS.items():
            hits = sum(1 for w in words if re.search(rf"\b{re.escape(w)}\b", low))
            if hits > best_hits:
                best_hits = hits
                best_cluster = cluster
        return best_cluster

    def _ensure_anchors(self):
        with self._lock:
            if len(self.anchor_embeddings) == self.num_moods:
                return
            try:
                from core.brain.Memory.LifetimeMemory import ltm_engine
                mood_keys = list(self.MOOD_ANCHOR_TEXTS.keys())
                mood_descs = [self.MOOD_ANCHOR_TEXTS[k] for k in mood_keys]
                batch_embs = ltm_engine.embed(mood_descs, is_query=False)
                if batch_embs and len(batch_embs) == len(mood_keys):
                    for mood, emb in zip(mood_keys, batch_embs):
                        if emb is not None:
                            self.anchor_embeddings[mood] = np.array(emb, dtype=np.float32)
                    self._save_weights()
            except Exception as e:
                logger.error(f"Behavioral anchor init failed: {e}")

    def _load_weights(self):
        with self._lock:
            if self.weights_file.exists():
                try:
                    data = np.load(self.weights_file)
                    if "W_mood" in data and data["W_mood"].shape == (self.num_moods, self.input_dim):
                        self.W_mood = data["W_mood"].astype(np.float32)
                    if "b_mood" in data and data["b_mood"].shape == (self.num_moods,):
                        self.b_mood = data["b_mood"].astype(np.float32)
                    if "anchors" in data and data["anchors"].shape == (self.num_moods, 384):
                        anc_matrix = data["anchors"].astype(np.float32)
                        for idx, mood in enumerate(self.MOOD_LABELS):
                            if np.any(anc_matrix[idx]):
                                self.anchor_embeddings[mood] = anc_matrix[idx]
                except Exception as e:
                    logger.error(f"Failed to load behavioral weights: {e}")

    def _save_weights(self):
        with self._lock:
            try:
                anc_matrix = np.zeros((self.num_moods, 384), dtype=np.float32)
                for idx, mood in enumerate(self.MOOD_LABELS):
                    if mood in self.anchor_embeddings and len(self.anchor_embeddings[mood]) == 384:
                        anc_matrix[idx] = self.anchor_embeddings[mood]
                np.savez_compressed(
                    self.weights_file,
                    W_mood=self.W_mood,
                    b_mood=self.b_mood,
                    anchors=anc_matrix
                )
            except Exception as e:
                logger.error(f"Failed to save behavioral weights: {e}")

    def _load_state(self):
        with self._lock:
            if not self.state_file.exists():
                return
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.situational_habits = data.get("situational_habits", {})
                saved_prefs = data.get("style_preferences", {})
                if isinstance(saved_prefs, dict):
                    self.style_preferences.update(saved_prefs)
                hashes = data.get("trained_message_hashes", [])
                self.trained_message_Hashes = set(hashes[-5000:])
                vb = data.get("voice_baseline", {})
                for key in ("rms_snr_history", "zcr_history", "rate_history"):
                    vals = vb.get(key, [])
                    if isinstance(vals, list):
                        self.voice_baseline[key] = deque(vals[-30:], maxlen=30)
            except Exception as e:
                logger.error(f"Failed to load behavioral state: {e}")

    def save_state(self):
        with self._lock:
            try:
                payload = {
                    "situational_habits": self.situational_habits,
                    "style_preferences": self.style_preferences,
                    "trained_message_hashes": list(self.trained_message_Hashes)[-5000:],
                    "voice_baseline": {
                        "rms_snr_history": list(self.voice_baseline["rms_snr_history"]),
                        "zcr_history": list(self.voice_baseline["zcr_history"]),
                        "rate_history": list(self.voice_baseline["rate_history"])
                    },
                    "updated_at": datetime.now().isoformat()
                }
                with open(self.state_file, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
                self._save_weights()
            except Exception as e:
                logger.error(f"Failed to save behavioral state: {e}")

    def record_voice_telemetry(self, peak_rms, mean_rms, noise_floor, mean_zcr, duration_s, word_count):
        with self._lock:
            safe_floor = max(25.0, float(noise_floor or 60.0))
            rms_snr = float(mean_rms or 0.0) / safe_floor
            peak_snr = float(peak_rms or 0.0) / safe_floor
            dur = max(0.25, float(duration_s or 1.0))
            speech_rate = float(word_count or 1) / dur
            zcr_val = float(mean_zcr or 0.08)

            self.voice_baseline["rms_snr_history"].append(rms_snr)
            self.voice_baseline["zcr_history"].append(zcr_val)
            self.voice_baseline["rate_history"].append(speech_rate)

            self.last_voice_packet = {
                "rms_snr": rms_snr,
                "peak_snr": peak_snr,
                "mean_zcr": zcr_val,
                "speech_rate": speech_rate,
                "duration_s": dur,
                "word_count": int(word_count or 0),
                "timestamp": time.time()
            }

    def _compute_acoustic_scores(self):
        with self._lock:
            pkt = self.last_voice_packet
            self.last_voice_packet = None
            if not pkt or (time.time() - pkt.get("timestamp", 0)) > 18.0:
                return None, 0.0, 0.0

            snr_hist = list(self.voice_baseline["rms_snr_history"])
            zcr_hist = list(self.voice_baseline["zcr_history"])
            rate_hist = list(self.voice_baseline["rate_history"])

            mean_snr = float(np.mean(snr_hist)) if len(snr_hist) >= 3 else pkt["rms_snr"]
            std_snr = max(0.35, float(np.std(snr_hist))) if len(snr_hist) >= 3 else 1.0

            mean_zcr = float(np.mean(zcr_hist)) if len(zcr_hist) >= 3 else pkt["mean_zcr"]
            std_zcr = max(0.015, float(np.std(zcr_hist))) if len(zcr_hist) >= 3 else 0.03

            mean_rate = float(np.mean(rate_hist)) if len(rate_hist) >= 3 else pkt["speech_rate"]
            std_rate = max(0.4, float(np.std(rate_hist))) if len(rate_hist) >= 3 else 0.8

            snr_z = np.clip((pkt["rms_snr"] - mean_snr) / std_snr, -2.5, 2.5)
            zcr_z = np.clip((pkt["mean_zcr"] - mean_zcr) / std_zcr, -2.5, 2.5)
            rate_z = np.clip((pkt["speech_rate"] - mean_rate) / std_rate, -2.5, 2.5)

            arousal = float(np.clip((0.45 * snr_z + 0.35 * rate_z + 0.20 * zcr_z) / 2.5, -1.0, 1.0))
            stress = float(np.clip((0.50 * max(0.0, snr_z) + 0.30 * max(0.0, rate_z) + 0.20 * max(0.0, zcr_z)) / 2.0, 0.0, 1.0))

            scores = np.array([
                0.25 + (0.20 if abs(arousal) < 0.35 else 0.0),
                0.15 + (0.55 * stress if arousal > 0.25 else 0.0),
                0.25 + (0.25 if -0.25 <= arousal <= 0.25 and stress < 0.3 else 0.0),
                0.15 + (0.50 * abs(arousal) if arousal < -0.30 else 0.0)
            ], dtype=np.float32)

            total = float(np.sum(scores))
            if total > 0:
                scores = scores / total
            return scores, arousal, stress

    def register_live_override(self, feedback_text):
        if not feedback_text or not str(feedback_text).strip():
            return
        with self._lock:
            clean = str(feedback_text).strip()
            self.recent_overrides.append({
                "text": clean,
                "timestamp": time.time()
            })
            low = clean.lower()
            if any(w in low for w in ["short", "chota", "brief", "seedha", "faltu", "mat bol"]):
                self.style_preferences["verbosity_score"] = max(-1.0, self.style_preferences.get("verbosity_score", 0.0) - 0.35)
            elif any(w in low for w in ["detail", "explain", "poora", "deep", "elaborate"]):
                self.style_preferences["verbosity_score"] = min(1.0, self.style_preferences.get("verbosity_score", 0.0) + 0.35)

            v_score = self.style_preferences["verbosity_score"]
            if v_score <= -0.3:
                self.style_preferences["verbosity"] = "concise"
            elif v_score >= 0.3:
                self.style_preferences["verbosity"] = "detailed"
            else:
                self.style_preferences["verbosity"] = "balanced"

            lessons = self.style_preferences.get("override_lessons", [])
            if clean not in lessons:
                lessons.append(clean[:140])
                self.style_preferences["override_lessons"] = lessons[-8:]

    def record_ui_edit(self, platform, original_text, edited_text, subject_edited=False):
        with self._lock:
            orig = str(original_text or "").strip()
            edited = str(edited_text or "").strip()
            if not orig or not edited:
                self.last_ui_edit_info = {"significant": False, "timestamp": time.time(), "note": ""}
                return False

            seq_ratio = SequenceMatcher(None, orig.lower(), edited.lower()).ratio()
            if seq_ratio >= 0.85 and not subject_edited:
                self.last_ui_edit_info = {
                    "significant": False,
                    "timestamp": time.time(),
                    "note": "Minor typo fix ignored by Behavioral Engine."
                }
                return False

            len_ratio = len(edited) / max(1, len(orig))
            pattern_notes = []
            if len_ratio < 0.65:
                pattern_notes.append(f"User prefers much shorter, crisp {platform} messages")
                self.style_preferences["verbosity_score"] = max(-1.0, self.style_preferences.get("verbosity_score", 0.0) - 0.25)
            elif len_ratio > 1.35:
                pattern_notes.append(f"User prefers more detailed {platform} messages")

            hinglish_markers = {"bhai", "yaar", "kar", "kardo", "hai", "main", "aaj", "kal", "theek"}
            edited_tokens = set(re.findall(r"[a-zA-Z]+", edited.lower()))
            orig_tokens = set(re.findall(r"[a-zA-Z]+", orig.lower()))
            if len(edited_tokens.intersection(hinglish_markers)) > len(orig_tokens.intersection(hinglish_markers)):
                pattern_notes.append(f"User prefers natural Hinglish tone on {platform}")
                if platform in ("whatsapp", "telegram"):
                    self.style_preferences["chat_style"] = "casual_hinglish"

            if any(g in orig.lower() for g in ["dear ", "sincerely", "regards", "hope this email finds"]) and not any(
                g in edited.lower() for g in ["dear ", "sincerely", "regards", "hope this email finds"]
            ):
                pattern_notes.append(f"User removes overly formal greetings/sign-offs on {platform}")
                if platform == "email":
                    self.style_preferences["email_style"] = "direct_no_fluff"

            note_str = "; ".join(pattern_notes) if pattern_notes else f"User significantly rewrote {platform} draft (similarity={seq_ratio:.2f})"
            existing = self.style_preferences.get("edit_patterns", [])
            if note_str not in existing:
                existing.append(note_str)
                self.style_preferences["edit_patterns"] = existing[-8:]

            self.last_ui_edit_info = {
                "significant": True,
                "timestamp": time.time(),
                "note": note_str
            }
            return True

    def was_last_edit_significant(self):
        with self._lock:
            if (time.time() - self.last_ui_edit_info.get("timestamp", 0)) > 300:
                return False
            return bool(self.last_ui_edit_info.get("significant", False))

    def _build_feature_vector(self, query_text, emb_vec, arousal, stress):
        now = datetime.now()
        hour_angle = 2.0 * math.pi * (now.hour + now.minute / 60.0) / 24.0
        time_sin = math.sin(hour_angle)
        time_cos = math.cos(hour_angle)
        is_late_night = 1.0 if (now.hour >= 23 or now.hour < 5) else 0.0

        now_ts = time.time()
        recent_ov_count = sum(1 for item in self.recent_overrides if (now_ts - item["timestamp"]) <= 600)
        recent_err_count = sum(1 for ts in self.recent_system_failures if (now_ts - ts) <= 600)

        ov_norm = min(1.0, recent_ov_count / 3.0)
        err_norm = min(1.0, recent_err_count / 3.0)
        len_norm = min(1.0, len(str(query_text or "").split()) / 25.0)

        meta_features = np.array([
            time_sin,
            time_cos,
            is_late_night,
            ov_norm,
            err_norm,
            len_norm,
            float(arousal),
            float(stress)
        ], dtype=np.float32)

        if emb_vec is None or len(emb_vec) != 384:
            emb_arr = np.zeros((384,), dtype=np.float32)
        else:
            emb_arr = np.array(emb_vec, dtype=np.float32)

        return np.concatenate([emb_arr, meta_features])

    @staticmethod
    def _softmax(logits):
        shifted = logits - np.max(logits)
        exps = np.exp(shifted)
        return exps / np.sum(exps)

    def _predict_mood_distribution(self, query_text, emb_vec=None):
        self._ensure_anchors()
        if emb_vec is None:
            try:
                from core.brain.Memory.LifetimeMemory import ltm_engine
                emb_vec = ltm_engine.embed(query_text, is_query=False)
            except Exception:
                emb_vec = None

        acoustic_scores, arousal, stress = self._compute_acoustic_scores()
        feat_vec = self._build_feature_vector(query_text, emb_vec, arousal, stress)

        linear_logits = np.dot(self.W_mood, feat_vec) + self.b_mood
        neural_probs = self._softmax(linear_logits)

        proto_scores = np.full((self.num_moods,), 0.25, dtype=np.float32)
        if emb_vec is not None and self.anchor_embeddings:
            emb_arr = np.array(emb_vec, dtype=np.float32)
            raw_sims = []
            for mood in self.MOOD_LABELS:
                anc = self.anchor_embeddings.get(mood)
                if anc is not None:
                    sim = max(0.0, 1.0 - float(cosine(emb_arr, anc)))
                else:
                    sim = 0.25
                raw_sims.append(sim)
            proto_scores = self._softmax(np.array(raw_sims, dtype=np.float32) * 4.0)

        tokens = set(re.findall(r"[a-zA-Z]+", str(query_text or "").lower()))
        now_ts = time.time()
        recent_ov = sum(1 for item in self.recent_overrides if (now_ts - item["timestamp"]) <= 600)

        heuristic = np.array([0.28, 0.18, 0.34, 0.20], dtype=np.float32)
        if tokens.intersection(self.FRUSTRATION_MARKERS) or recent_ov > 0:
            heuristic[1] += 0.35 + min(0.25, recent_ov * 0.12)
        if tokens.intersection(self.FOCUS_MARKERS):
            heuristic[0] += 0.35
        if tokens.intersection(self.TIRED_MARKERS) or datetime.now().hour >= 23 or datetime.now().hour < 5:
            heuristic[3] += 0.25
        heuristic = heuristic / np.sum(heuristic)

        semantic_probs = 0.40 * neural_probs + 0.30 * proto_scores + 0.30 * heuristic

        if acoustic_scores is not None:
            text_appears_calm = not bool(tokens.intersection(self.FRUSTRATION_MARKERS)) and recent_ov == 0
            if text_appears_calm and acoustic_scores[1] > 0.45:
                acoustic_weight = 0.20
            else:
                acoustic_weight = 0.38
            final_probs = (1.0 - acoustic_weight) * semantic_probs + acoustic_weight * acoustic_scores
            modality = "multimodal_voice_and_text"
        else:
            final_probs = semantic_probs
            modality = "semantic_and_context_only"

        final_probs = final_probs / np.sum(final_probs)
        best_idx = int(np.argmax(final_probs))
        best_mood = self.MOOD_LABELS[best_idx]
        confidence = float(final_probs[best_idx])

        return {
            "mood": best_mood,
            "confidence": round(confidence, 3),
            "probabilities": {self.MOOD_LABELS[i]: round(float(final_probs[i]), 3) for i in range(self.num_moods)},
            "modality": modality,
            "feature_vector": feat_vec,
            "mood_index": best_idx
        }

    def _get_situational_habit_prediction(self, query_text, time_bucket, task_cluster, emb_vec=None):
        key = f"{time_bucket}::{task_cluster}"
        today = datetime.now().date()
        matched_apps = {}
        matched_tools = {}
        support_days = 0
        total_weight = 0.0

        with self._lock:
            bucket_data = self.situational_habits.get(key, {})
            if bucket_data:
                distinct_days = bucket_data.get("distinct_days", [])
                support_days = len(distinct_days)
                last_seen_str = bucket_data.get("last_seen", "")
                days_old = 0
                if last_seen_str:
                    try:
                        days_old = max(0, (today - datetime.strptime(last_seen_str, "%Y-%m-%d").date()).days)
                    except Exception:
                        days_old = 0

                decay = math.exp(-0.04 * days_old)
                raw_weight = float(bucket_data.get("weight", 0.0)) * decay

                if support_days >= 2 or raw_weight >= 3.0:
                    total_weight = raw_weight
                    for app, cnt in bucket_data.get("apps", {}).items():
                        matched_apps[app] = round(cnt * decay, 2)
                    for tool, cnt in bucket_data.get("tools", {}).items():
                        matched_tools[tool] = round(cnt * decay, 2)

        graph_signals = []
        try:
            from core.brain.Memory.LifetimeMemory import ltm_engine
            if hasattr(ltm_engine, "get_behavioral_graph_signals"):
                graph_signals = ltm_engine.get_behavioral_graph_signals(query_text, top_k=4, query_emb=emb_vec)
        except Exception:
            graph_signals = []

        top_apps = [k for k, _ in sorted(matched_apps.items(), key=lambda x: x[1], reverse=True)[:3]]
        top_tools = [k for k, _ in sorted(matched_tools.items(), key=lambda x: x[1], reverse=True)[:3]]

        habit_confidence = min(0.95, 0.35 + (0.15 * support_days) + (0.05 * min(6.0, total_weight))) if (top_apps or top_tools or graph_signals) else 0.0

        return {
            "situation_key": key,
            "habit_confidence": round(habit_confidence, 3),
            "support_days": support_days,
            "likely_apps": top_apps,
            "preferred_tools": top_tools,
            "graph_signals": graph_signals
        }

    def get_last_ephemeral_payload(self):
        with self._lock:
            if not self.last_prediction_cache:
                return {}
            return dict(self.last_prediction_cache.get("ephemeral_payload", {}))

    def predict_for_agent(self, query_text, master_history=None):
        with self._lock:
            emb_vec = None
            try:
                from core.brain.Memory.LifetimeMemory import ltm_engine
                emb_vec = ltm_engine.embed(query_text, is_query=False)
            except Exception:
                emb_vec = None

            time_bucket = self._get_time_bucket()
            task_cluster = self._detect_task_cluster(query_text)
            mood_res = self._predict_mood_distribution(query_text, emb_vec=emb_vec)
            habit_res = self._get_situational_habit_prediction(query_text, time_bucket, task_cluster, emb_vec=emb_vec)

            mood = mood_res["mood"]
            mood_conf = mood_res["confidence"]

            if mood == "FRUSTRATED_URGENT":
                recommended_emotion = "[focused]"
                execution_style = "ZERO_FLUFF_DIRECT_FIX: User appears frustrated or urgent. Skip pleasantries, do not over-explain, verify tool output strictly, and deliver immediate solution."
            elif mood == "TIRED_LOW_ENERGY":
                recommended_emotion = "[calm]"
                execution_style = "LOW_COGNITIVE_LOAD: Keep final answer brief, warm, and effortless to read."
            elif mood == "DEEP_FOCUS":
                recommended_emotion = "[focused]"
                execution_style = "TECHNICAL_PRECISION: Prioritize direct technical execution, clean code/actions, and structured output."
            else:
                recommended_emotion = "[cheerful]"
                execution_style = "BALANCED_PARTNER: Natural witty Hinglish tone with crisp execution."

            ephemeral_payload = {
                "mood": mood,
                "confidence": int(mood_conf * 100),
                "recommended_emotion": recommended_emotion,
                "execution_style": execution_style,
                "situation_key": habit_res["situation_key"],
                "habit_confidence": habit_res["habit_confidence"],
                "likely_apps": habit_res["likely_apps"] if habit_res["habit_confidence"] >= 0.65 else [],
                "preferred_tools": habit_res["preferred_tools"] if habit_res["habit_confidence"] >= 0.65 else [],
                "verbosity": self.style_preferences.get("verbosity", "balanced"),
                "email_style": self.style_preferences.get("email_style", "professional_concise"),
                "chat_style": self.style_preferences.get("chat_style", "natural_hinglish"),
                "modality": mood_res["modality"]
            }

            self.last_prediction_cache = {
                "query": query_text,
                "time_bucket": time_bucket,
                "task_cluster": task_cluster,
                "mood_index": mood_res["mood_index"],
                "feature_vector": mood_res["feature_vector"],
                "ephemeral_payload": ephemeral_payload,
                "timestamp": time.time()
            }

            lines = [
                "[BEHAVIORAL ML & MOOD TELEMETRY]",
                f"- Situation Context: TimeSlot={time_bucket} | TaskCluster={task_cluster} | InputModality={mood_res['modality']}",
                f"- Predicted User Mood: {mood} (Confidence: {int(mood_conf * 100)}%) | Recommended Emotion Tag: {recommended_emotion}",
                f"- Adaptive Execution Directive: {execution_style}",
                f"- Learned Verbosity & Comms Style: Verbosity={self.style_preferences['verbosity']} | Email={self.style_preferences['email_style']} | Chat={self.style_preferences['chat_style']}"
            ]

            if habit_res["habit_confidence"] >= 0.65:
                if habit_res["likely_apps"]:
                    lines.append(f"- Confirmed Situational App Habit ({habit_res['support_days']} distinct days): {', '.join(habit_res['likely_apps'])}")
                if habit_res["preferred_tools"]:
                    lines.append(f"- Frequently Used Tools in This Situation: {', '.join(habit_res['preferred_tools'])}")

            if habit_res["graph_signals"]:
                lines.append(f"- Reinforced LTM Behavioral Links: {' ; '.join(habit_res['graph_signals'][:3])}")

            if self.style_preferences.get("edit_patterns"):
                lines.append(f"- Learned UI Edit Preferences: {' | '.join(self.style_preferences['edit_patterns'][-3:])}")

            if self.style_preferences.get("override_lessons"):
                lines.append(f"- Past Live Override Corrections: {' | '.join(self.style_preferences['override_lessons'][-2:])}")

            return "\n".join(lines)

    def learn_from_execution(
        self,
        raw_command,
        metadata_tracker=None,
        executed_tools=None,
        had_system_failure=False,
        had_live_override=False,
        had_user_cancel=False,
        had_major_edit=False
    ):
        with self._lock:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            time_bucket = self._get_time_bucket(now)
            task_cluster = self._detect_task_cluster(raw_command)
            key = f"{time_bucket}::{task_cluster}"

            if had_system_failure:
                self.recent_system_failures.append(time.time())

            user_dissatisfied = had_live_override or had_user_cancel or had_major_edit
            smooth_success = (not user_dissatisfied) and (not had_system_failure)

            cache = self.last_prediction_cache
            self.last_prediction_cache = {}

            if cache and cache.get("feature_vector") is not None:
                feat_vec = cache["feature_vector"]
                target_idx = None
                reward_scale = 0.0
                tokens = set(re.findall(r"[a-zA-Z]+", str(raw_command or "").lower()))
                tools_set = set(executed_tools or [])

                if user_dissatisfied:
                    target_idx = 1
                    reward_scale = 1.35
                elif smooth_success:
                    if task_cluster in ("coding_debug", "research_docs") or tools_set.intersection({
                        "run_python_code", "execute_terminal_command", "file_operations", "deep_research"
                    }):
                        target_idx = 0
                        reward_scale = 0.65
                    elif time_bucket == "late_night" or tokens.intersection(self.TIRED_MARKERS):
                        target_idx = 3
                        reward_scale = 0.65
                    elif task_cluster == "system_media":
                        target_idx = 2
                        reward_scale = 0.45

                if target_idx is not None and reward_scale > 0.0:
                    logits = np.dot(self.W_mood, feat_vec) + self.b_mood
                    probs = self._softmax(logits)
                    target_vec = np.zeros((self.num_moods,), dtype=np.float32)
                    target_vec[target_idx] = 1.0

                    grad = probs - target_vec
                    self.W_mood -= (self.learning_rate * reward_scale) * np.outer(grad, feat_vec)
                    self.b_mood -= (self.learning_rate * reward_scale) * grad
                    self.W_mood = np.clip(self.W_mood, -5.0, 5.0)
                    self.b_mood = np.clip(self.b_mood, -3.0, 3.0)

            entry = self.situational_habits.get(key, {
                "weight": 0.0,
                "distinct_days": [],
                "last_seen": today_str,
                "streak_penalties": 0,
                "apps": {},
                "tools": {}
            })

            if smooth_success:
                entry["weight"] = min(25.0, float(entry.get("weight", 0.0)) + 1.0)
                entry["streak_penalties"] = 0
                days_list = entry.get("distinct_days", [])
                if today_str not in days_list:
                    days_list.append(today_str)
                    entry["distinct_days"] = days_list[-30:]
                entry["last_seen"] = today_str

                if isinstance(metadata_tracker, dict):
                    for app in metadata_tracker.get("apps_opened", []):
                        app_clean = str(app).strip()
                        if app_clean:
                            entry["apps"][app_clean] = float(entry["apps"].get(app_clean, 0.0)) + 1.0

                if executed_tools:
                    for t_name in executed_tools:
                        t_clean = str(t_name).strip()
                        if t_clean and t_clean != "complete_task":
                            entry["tools"][t_clean] = float(entry["tools"].get(t_clean, 0.0)) + 1.0

            elif user_dissatisfied and not had_system_failure:
                penalties = int(entry.get("streak_penalties", 0)) + 1
                entry["streak_penalties"] = penalties
                if penalties >= 2:
                    entry["weight"] = max(0.0, float(entry.get("weight", 0.0)) * 0.45)
                    for app_k in list(entry.get("apps", {}).keys()):
                        entry["apps"][app_k] = round(entry["apps"][app_k] * 0.5, 2)
                    for tool_k in list(entry.get("tools", {}).keys()):
                        entry["tools"][tool_k] = round(entry["tools"][tool_k] * 0.5, 2)
                else:
                    entry["weight"] = max(0.0, float(entry.get("weight", 0.0)) - 0.75)

            self.situational_habits[key] = entry
            self.save_state()

    @staticmethod
    def _extract_tools_from_events(system_events):
        extracted = []
        for evt in (system_events or []):
            s = str(evt).strip()
            if not s:
                continue
            if s.startswith("File Op"):
                extracted.append("file_operations")
            elif s.startswith("File Transfer"):
                extracted.append("file_transfer_action")
            elif s.startswith("Terminal Command"):
                extracted.append("execute_terminal_command")
            elif s == "Executed Python Code Script":
                extracted.append("run_python_code")
            elif s.startswith("Image "):
                extracted.append("image_command")
            elif s.startswith("Opened URLs") or s.startswith("System Action"):
                extracted.append("system_controller")
            elif s.startswith("email_action"):
                extracted.append("email_action")
            elif s.startswith("whatsapp_action"):
                extracted.append("whatsapp_action")
            elif s.startswith("telegram_action"):
                extracted.append("telegram_action")
            elif s.startswith("Executed "):
                t_name = s[len("Executed "):].strip()
                if t_name:
                    extracted.append(t_name)
        return extracted

    def train_from_history(self, master_history, daily_summaries=None):
        if not master_history:
            return
        with self._lock:
            learned_count = 0
            last_user_cmd = ""
            for item in master_history:
                if not isinstance(item, dict):
                    continue
                ts_str = str(item.get("timestamp", ""))
                msg_text = str(item.get("message", "")).strip()
                role = str(item.get("role", "")).upper()
                if not ts_str or not msg_text:
                    continue

                if role == "USER":
                    last_user_cmd = msg_text

                sig = f"{ts_str}::{role}::{msg_text[:50]}"
                if sig in self.trained_message_Hashes:
                    continue
                self.trained_message_Hashes.add(sig)

                meta = item.get("metadata")
                if not isinstance(meta, dict) or not (
                    meta.get("apps_opened") or meta.get("files_touched") or meta.get("system_events")
                ):
                    continue

                try:
                    dt = datetime.fromisoformat(ts_str)
                except Exception:
                    continue

                date_str = dt.strftime("%Y-%m-%d")
                time_bucket = self._get_time_bucket(dt)
                source_query = last_user_cmd if last_user_cmd else msg_text
                task_cluster = self._detect_task_cluster(source_query)
                key = f"{time_bucket}::{task_cluster}"

                entry = self.situational_habits.get(key, {
                    "weight": 0.0,
                    "distinct_days": [],
                    "last_seen": date_str,
                    "streak_penalties": 0,
                    "apps": {},
                    "tools": {}
                })
                entry["weight"] = min(25.0, float(entry.get("weight", 0.0)) + 0.6)
                if date_str not in entry["distinct_days"]:
                    entry["distinct_days"].append(date_str)
                    entry["distinct_days"] = entry["distinct_days"][-30:]
                entry["last_seen"] = max(entry.get("last_seen", date_str), date_str)

                for app in meta.get("apps_opened", []):
                    app_clean = str(app).strip()
                    if app_clean:
                        entry["apps"][app_clean] = float(entry["apps"].get(app_clean, 0.0)) + 0.8

                for t_name in self._extract_tools_from_events(meta.get("system_events", [])):
                    entry["tools"][t_name] = float(entry["tools"].get(t_name, 0.0)) + 0.8

                self.situational_habits[key] = entry
                learned_count += 1

            if learned_count > 0:
                self.save_state()
                logger.info(f"Behavioral ML Engine catch-up trained on {learned_count} historical execution records.")


behavioral_engine = BehavioralLearningEngine()