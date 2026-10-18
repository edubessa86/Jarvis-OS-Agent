import json
import os
import threading
from datetime import datetime
from pathlib import Path
from scipy.spatial.distance import cosine

from core.logger.logger import logger


class BehavioralPatternEngine:
    def __init__(self, memory_dir="Data/jarvis_memory"):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.patterns_file = self.memory_dir / "behavioral_patterns.jsonl"
        self._lock = threading.RLock()

        self.dedupe_threshold = 0.85
        self.similarity_threshold = 0.52
        self.top_k = 3
        self.recency_decay_days = 180

        self._last_query = None
        self._last_ephemeral_block = ""

        self.patterns = self._load_patterns()

        logger.info(
            f"BehavioralPatternEngine ready | patterns_loaded={len(self.patterns)}"
        )

    @staticmethod
    def _build_embed_text(pattern_entry: dict) -> str:
        raw = (pattern_entry.get("raw_statement") or "").strip()
        meaning = (pattern_entry.get("meaning") or "").strip()
        triggers = (pattern_entry.get("trigger_contexts") or "").strip()
        parts = []
        if meaning:
            parts.append(meaning)
        if triggers:
            parts.append(f"Relevant for: {triggers}")
        if raw:
            parts.append(raw)
        return ". ".join(parts)

    def _invalidate_cache(self):
        with self._lock:
            self._last_query = None
            self._last_ephemeral_block = ""

    def _load_patterns(self):
        if not self.patterns_file.exists():
            return []
        loaded = []
        try:
            with open(self.patterns_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if isinstance(entry, dict) and entry.get("raw_statement"):
                            entry.pop("_embedding", None)
                            loaded.append(entry)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"BehavioralPatternEngine load error: {e}")
            return []
        if loaded:
            self._refresh_embeddings(loaded)
        return loaded

    def _refresh_embeddings(self, patterns_list):
        if not patterns_list:
            return
        try:
            from core.brain.Memory.LifetimeMemory import ltm_engine
            texts = [self._build_embed_text(p) for p in patterns_list]
            embs = ltm_engine.embed(texts, is_query=False)
            if embs and len(embs) == len(patterns_list):
                for p, emb in zip(patterns_list, embs):
                    p["_embedding"] = emb
        except Exception as e:
            logger.error(f"BehavioralPatternEngine embed refresh error: {e}")

    def _save_patterns(self):
        with self._lock:
            try:
                tmp_path = self.patterns_file.with_suffix(".jsonl.tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    for p in self.patterns:
                        clean = {k: v for k, v in p.items() if not k.startswith("_")}
                        f.write(json.dumps(clean, ensure_ascii=False) + "\n")
                os.replace(tmp_path, self.patterns_file)
            except Exception as e:
                logger.error(f"BehavioralPatternEngine save error: {e}")

    def _upsert_pattern(self, new_pattern: dict):
        if not isinstance(new_pattern, dict):
            return
        raw = (new_pattern.get("raw_statement") or "").strip()
        meaning = (new_pattern.get("meaning") or "").strip()
        triggers = (new_pattern.get("trigger_contexts") or "").strip()
        if not raw or len(raw.split()) < 3:
            return

        embed_text = self._build_embed_text({
            "raw_statement": raw,
            "meaning": meaning,
            "trigger_contexts": triggers
        })
        try:
            from core.brain.Memory.LifetimeMemory import ltm_engine
            new_emb = ltm_engine.embed(embed_text, is_query=False)
        except Exception as e:
            logger.warning(f"BehavioralPatternEngine embed new pattern error: {e}")
            return

        if new_emb is None:
            return

        with self._lock:
            best_idx = -1
            best_sim = 0.0
            for idx, ex in enumerate(self.patterns):
                ex_emb = ex.get("_embedding")
                if not ex_emb:
                    continue
                try:
                    sim = 1.0 - float(cosine(new_emb, ex_emb))
                except Exception:
                    continue
                if sim > best_sim:
                    best_sim = sim
                    best_idx = idx

            now_iso = datetime.now().isoformat()

            if best_idx >= 0 and best_sim >= self.dedupe_threshold:
                ex = self.patterns[best_idx]
                ex["evidence_count"] = int(ex.get("evidence_count", 1)) + 1
                ex["last_mentioned"] = now_iso
                old_conf = float(ex.get("confidence", 0.5))
                ex["confidence"] = round(min(0.95, old_conf + 0.08), 3)
                updated_fields = False
                if not ex.get("meaning") and meaning:
                    ex["meaning"] = meaning
                    updated_fields = True
                if triggers and not ex.get("trigger_contexts"):
                    ex["trigger_contexts"] = triggers
                    updated_fields = True
                if updated_fields:
                    ex["_embedding"] = new_emb
                logger.info(
                    f"🧠 Behavioral pattern reinforced: '{raw[:40]}...' "
                    f"(sim={best_sim:.2f}, count={ex['evidence_count']}, conf={ex['confidence']})"
                )
            else:
                entry = {
                    "type": new_pattern.get("type", "habit"),
                    "raw_statement": raw,
                    "meaning": meaning,
                    "trigger_contexts": triggers,
                    "confidence": round(float(new_pattern.get("confidence", 0.7)), 3),
                    "evidence_count": 1,
                    "first_mentioned": now_iso,
                    "last_mentioned": now_iso,
                    "_embedding": new_emb,
                }
                self.patterns.append(entry)
                logger.info(f"🧠 Behavioral pattern added: '{raw[:50]}'")

            self._invalidate_cache()
            self._save_patterns()

    def get_relevant_patterns(self, query_text: str, mode: str = "agentic"):
        if not self.patterns or not query_text or not query_text.strip():
            return []

        try:
            from core.brain.Memory.LifetimeMemory import ltm_engine
            query_emb = ltm_engine.embed(query_text.strip(), is_query=True)
        except Exception as e:
            logger.warning(f"BehavioralPatternEngine query embed error: {e}")
            return []

        if query_emb is None:
            return []

        now = datetime.now()
        scored = []

        with self._lock:
            for p in self.patterns:
                emb = p.get("_embedding")
                if not emb:
                    continue
                try:
                    sim = 1.0 - float(cosine(query_emb, emb))
                except Exception:
                    continue
                if sim < self.similarity_threshold:
                    continue

                conf = float(p.get("confidence", 0.5))
                score = sim * (0.6 + 0.4 * conf)

                try:
                    last_dt = datetime.fromisoformat(p.get("last_mentioned", ""))
                    days_old = max(0, (now - last_dt).days)
                    decay = max(0.5, 1.0 - (days_old / self.recency_decay_days) * 0.5)
                    score *= decay
                except Exception:
                    pass

                scored.append({
                    "pattern": p,
                    "score": round(score, 3),
                    "similarity": round(sim, 3),
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:self.top_k]

    def format_ephemeral_block(
        self,
        current_command: str = "",
        mode: str = "agentic",
        recent_context: str = "",
    ) -> str:
        query_text = (current_command or "").strip()
        if not query_text:
            return ""

        with self._lock:
            if query_text == self._last_query:
                return self._last_ephemeral_block

        matches = self.get_relevant_patterns(query_text, mode=mode)
        if not matches:
            with self._lock:
                self._last_query = query_text
                self._last_ephemeral_block = ""
            return ""

        lines = [
            "[BEHAVIORAL PATTERNS — extracted from user's own past statements]",
            "These are verbatim things the user told us about themselves in past conversations.",
            "Use them ONLY if the current command relates to them. Otherwise ignore.",
            "",
        ]
        for m in matches:
            p = m["pattern"]
            raw = p.get("raw_statement", "")
            meaning = p.get("meaning", "")
            conf = int(float(p.get("confidence", 0.5)) * 100)
            count = int(p.get("evidence_count", 1))

            lines.append(f'• USER SAID: "{raw}"')
            if meaning:
                lines.append(f'  → Meaning: {meaning}')
            lines.append(
                f'  → Confidence: {conf}% | Mentioned: {count}x | '
                f'Similarity to current command: {m["similarity"]:.2f}'
            )
            lines.append("")

        result_block = "\n".join(lines).rstrip()
        with self._lock:
            self._last_query = query_text
            self._last_ephemeral_block = result_block
        return result_block

    def get_all_patterns(self):
        with self._lock:
            return [
                {k: v for k, v in p.items() if not k.startswith("_")}
                for p in self.patterns
            ]

    def get_stats(self):
        with self._lock:
            total = len(self.patterns)
            reinforced = sum(1 for p in self.patterns if int(p.get("evidence_count", 1)) >= 2)
            return {
                "total_patterns": total,
                "reinforced_patterns": reinforced,
                "file_path": str(self.patterns_file),
            }

    def clear_all(self):
        with self._lock:
            self.patterns = []
            self._invalidate_cache()
            self._save_patterns()
            logger.warning("BehavioralPatternEngine: all patterns cleared.")

    def shutdown(self):
        try:
            self._save_patterns()
            logger.info("BehavioralPatternEngine shutdown complete.")
        except Exception as e:
            logger.error(f"BehavioralPatternEngine shutdown error: {e}")


behavioral_pattern_engine = BehavioralPatternEngine()