import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from scipy.spatial.distance import cosine
import openai

from core.logger.logger import logger
from core.brain.config import (
    LTM_EXTRACTION_API_KEY,
    LTM_EXTRACTION_MODEL,
    LTM_EXTRACTION_ENDPOINT,
)


class BehavioralPatternEngine:
    """
    Behavioral Pattern Engine for Jarvis.

    Purpose:
        Extract STABLE behavioral patterns (routines, preferences, habits, contextual dislikes)
        from the user's own messages. On every command, retrieve the most semantically
        relevant patterns via cosine similarity and inject them into the ephemeral prompt.

    Difference from LTM:
        LTM        = declarative facts (identity, relations, contacts, assets, static prefs)
        Behavioral = contextual patterns (time-based routines, workflow habits, comm style)

    Storage:
        Data/jarvis_memory/behavioral_patterns.jsonl (flat list, one pattern per line).
        Each pattern's embedding is cached in-memory only, never persisted.

    Retrieval thresholds:
        FAST    → top_k=1, similarity ≥ 0.68  (strict; fewer false positives)
        AGENTIC → top_k=3, similarity ≥ 0.55  (richer personalization)
    """

    # Regex prefilter — only matching messages trigger an LLM call.
    _PREFILTER_PATTERNS = [
        r"\b(raat|subah|shaam|dopahar|morning|evening|night|afternoon|late\s*night)\b",
        r"\b\d{1,2}\s*(?:bje|bajey|baj|o'?clock|pm|am)\b",
        r"\b(roz|rozana|hamesha|aksar|usually|always|mostly|daily|every\s*day|every\s*morning|every\s*night)\b",
        r"\b(mai|main|mein|hum|mujhe|mera|meri|mere|i|my|me)\b.{0,40}\b(karta|karti|karte|prefer|like|love|hate|pasand)\b",
        r"\b(pasand\s*(?:hai|hain|hota|hoti)|prefer|like|love|hate|dislike)\b",
        r"\b(routine|habit|aadat|schedule)\b",
    ]

    # Messages starting with any of these are pure commands, not behavioral statements.
    _COMMAND_PREFIXES = (
        "open ", "close ", "play ", "kholo", "band ", "send ", "bhej",
        "write ", "run ", "execute ", "search ", "find ", "check ",
        "screenshot", "lock ", "sleep ", "volume", "brightness",
        "email ", "whatsapp ", "telegram ", "message ",
    )

    _IGNORE_WORDS = {
        "ok", "okay", "yes", "no", "thanks", "thank you", "clear",
        "done", "nice", "cool", "hmm", "acha", "hi", "hello", "hey",
    }

    def __init__(self, memory_dir="Data/jarvis_memory"):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.patterns_file = self.memory_dir / "behavioral_patterns.jsonl"
        self._lock = threading.RLock()
        self._extract_lock = threading.Lock()

        self.patterns = self._load_patterns()

        # Tuning knobs
        self.dedupe_threshold = 0.85
        self.similarity_threshold_fast = 0.68
        self.similarity_threshold_agentic = 0.55
        self.top_k_fast = 1
        self.top_k_agentic = 3
        self.recency_decay_days = 180

        try:
            self.client = openai.OpenAI(
                api_key=LTM_EXTRACTION_API_KEY,
                base_url=LTM_EXTRACTION_ENDPOINT,
            ) if LTM_EXTRACTION_API_KEY else None
        except Exception as e:
            logger.error(f"BehavioralPatternEngine client init failed: {e}")
            self.client = None

        logger.info(
            f"BehavioralPatternEngine ready | patterns_loaded={len(self.patterns)}"
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
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
            texts = [p["raw_statement"] for p in patterns_list]
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

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------
    def _looks_behavioral(self, message: str) -> bool:
        if not message or not message.strip():
            return False
        low = message.lower().strip()
        if len(low.split()) < 4:
            return False
        for prefix in self._COMMAND_PREFIXES:
            if low.startswith(prefix):
                return False
        if low in self._IGNORE_WORDS:
            return False
        return any(re.search(p, low) for p in self._PREFILTER_PATTERNS)

    def observe_user_message(self, message: str, history: str = ""):
        """Non-blocking entry point. Regex prefilter → async LLM extraction."""
        if not message or not message.strip():
            return
        logger.debug(f"🧠 Behavioral observe: '{message[:60]}'")
        if not self._looks_behavioral(message):
            logger.debug(f"🧠 Behavioral prefilter: SKIP (no routine/preference signal)")
            return
        logger.info(f"🧠 Behavioral prefilter: PASSED -> extracting async | msg='{message[:60]}'")
        try:
            threading.Thread(
                target=self._extract_async,
                args=(message.strip(), history or ""),
                daemon=True,
            ).start()
        except Exception as e:
            logger.error(f"BehavioralPatternEngine thread spawn error: {e}")

    def _extract_async(self, message: str, history: str):
        if not self.client:
            return
        with self._extract_lock:
            try:
                recent_history_snippet = (history or "")[-500:] if history else "None"

                prompt = f"""You are the Behavioral Pattern Extractor for Jarvis (an AI assistant).

Your ONLY job: Extract STABLE behavioral patterns about the USER from their message.
Behavioral patterns describe HOW the user works, WHEN they do things, or their CONTEXTUAL preferences.

== NEVER EXTRACT THESE (they belong to another system, LTM) ==
- Identity facts: "Mera naam Rahul hai", "Mai 24 saal ka hu", "Mai Delhi me rehta hu"
- Relationships: "Rahul mera bhai hai", "Kaif mera boss hai"
- Contact info: "Mera number 98765... hai", "Mera email x@y.com hai"
- Assets: "Mera phone iPhone 14 hai", "Mere paas Swift hai"
- Static preferences with no context: "Mujhe Hindi pasand hai", "Mera favourite color blue hai"
- Skills: "Mujhe Python aata hai", "Mai React jaanta hu"

== EXTRACT THESE (behavioral patterns) ==
- Time-based routines: "Raat ko 8 bje project jarvis pe kaam karta hu", "Subah 6 bje uthta hu"
- Frequency patterns: "Har subah walk karta hu", "Roz raat ko code likhta hu"
- Contextual habits: "Kaam karte waqt music sunta hu", "Code likhne se pehle design sochta hu"
- Communication style: "Email me formal tone nahi chahiye", "Short replies pasand hai"
- Conditional dislikes: "Subah meetings nahi karna chahta"

== DECISION TEST ==
Ask yourself: Can you pose one of these questions about the statement?
- "Kab?" (when?)
- "Kitni baar?" (how often?)
- "Kis situation me?" (in what context?)

If YES to any -> behavioral pattern -> EXTRACT.
If NO to all -> skip (belongs to LTM or irrelevant).

== FEW-SHOT EXAMPLES (learn from these) ==
EXTRACT (routine with time):
  IN:  "bhai mai raat 8 bje project jarvis pe kaam karta hu"
  OUT: has_patterns=true, patterns=[{{type:"routine", raw_statement:"bhai mai raat 8 bje project jarvis pe kaam karta hu", meaning:"User works on Project Jarvis around 8 PM at night", confidence:0.75}}]

EXTRACT (frequency habit):
  IN:  "har subah 6 bje walk pe jata hu"
  OUT: has_patterns=true, patterns=[{{type:"routine", raw_statement:"har subah 6 bje walk pe jata hu", meaning:"User walks every morning around 6 AM", confidence:0.75}}]

EXTRACT (communication preference):
  IN:  "email me formal tone bilkul nahi chahiye, short rakho hamesha"
  OUT: has_patterns=true, patterns=[{{type:"preference", raw_statement:"email me formal tone bilkul nahi chahiye, short rakho hamesha", meaning:"User prefers short, non-formal emails always", confidence:0.8}}]

EXTRACT (contextual habit):
  IN:  "kaam karte waqt music sunta hu"
  OUT: has_patterns=true, patterns=[{{type:"habit", raw_statement:"kaam karte waqt music sunta hu", meaning:"User listens to music while working", confidence:0.7}}]

SKIP (identity, goes to LTM):
  IN:  "mera naam Rahul hai"
  OUT: has_patterns=false

SKIP (static preference, no context, goes to LTM):
  IN:  "mujhe Hindi pasand hai"
  OUT: has_patterns=false

SKIP (one-time plan):
  IN:  "aaj raat movie dekhunga"
  OUT: has_patterns=false

SKIP (temporary state):
  IN:  "aaj thak gaya hu"
  OUT: has_patterns=false

== RULES ==
1. raw_statement MUST be verbatim user words. DO NOT paraphrase.
2. If no stable behavioral pattern found -> has_patterns=false.
3. One-time plans ("aaj raat movie dekhunga") -> NOT behavioral.
4. Temporary states ("aaj thak gaya hu") -> NOT behavioral.
5. Hinglish and English are treated EQUALLY. Extract from Hinglish just like English.
6. A routine with time markers (raat, subah, 8 bje, har subah, hamesha) is ALWAYS behavioral - even if it also mentions a project/app name.
7. Project/task names ("project jarvis", "GhostVault") in a routine context are PART of the behavioral pattern, not LTM facts.

RECENT CONVERSATION:
{recent_history_snippet}

USER MESSAGE:
"{message}"

Return STRICT JSON:
{{
  "has_patterns": true/false,
  "patterns": [
    {{
      "type": "routine|preference|habit|dislike",
      "raw_statement": "verbatim user quote",
      "meaning": "one-line English interpretation",
      "confidence": 0.6
    }}
  ]
}}
"""
                response = self.client.chat.completions.create(
                    model=LTM_EXTRACTION_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a precise behavioral pattern extractor. Output strictly valid JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.0,
                    response_format={"type": "json_object"},
                    max_tokens=600,
                )

                raw_text = response.choices[0].message.content.strip()
                clean_text = re.sub(r'^```json\n|```$', '', raw_text, flags=re.MULTILINE).strip()
                data = json.loads(clean_text)

                logger.info(
                    f"🧠 Behavioral LLM response | has_patterns={data.get('has_patterns')} "
                    f"| patterns_count={len(data.get('patterns', []))}"
                )

                if not data.get("has_patterns"):
                    logger.debug(f"🧠 Behavioral skipped by LLM. Reasoning (if any): {data.get('reasoning', 'none')}")
                    return

                for p in data.get("patterns", []):
                    logger.debug(f"🧠 Behavioral upserting: {p}")
                    self._upsert_pattern(p)

            except Exception as e:
                logger.error(f"BehavioralPatternEngine extraction error: {e}")

    def _upsert_pattern(self, new_pattern: dict):
        raw = (new_pattern.get("raw_statement") or "").strip()
        if not raw or len(raw.split()) < 3:
            return

        try:
            from core.brain.Memory.LifetimeMemory import ltm_engine
            new_emb = ltm_engine.embed(raw, is_query=False)
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
                if not ex.get("meaning") and new_pattern.get("meaning"):
                    ex["meaning"] = new_pattern["meaning"]
                logger.info(
                    f"🧠 Behavioral pattern reinforced: '{raw[:40]}...' "
                    f"(sim={best_sim:.2f}, count={ex['evidence_count']}, conf={ex['confidence']})"
                )
            else:
                entry = {
                    "type": new_pattern.get("type", "habit"),
                    "raw_statement": raw,
                    "meaning": (new_pattern.get("meaning") or "").strip(),
                    "confidence": round(float(new_pattern.get("confidence", 0.5)), 3),
                    "evidence_count": 1,
                    "first_mentioned": now_iso,
                    "last_mentioned": now_iso,
                    "_embedding": new_emb,
                }
                self.patterns.append(entry)
                logger.info(f"🧠 Behavioral pattern added: '{raw[:50]}'")

            self._save_patterns()

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------
    def get_relevant_patterns(self, query_text: str, mode: str = "agentic"):
        if not self.patterns or not query_text or not query_text.strip():
            return []

        try:
            from core.brain.Memory.LifetimeMemory import ltm_engine
            query_emb = ltm_engine.embed(query_text.strip(), is_query=False)
        except Exception as e:
            logger.warning(f"BehavioralPatternEngine query embed error: {e}")
            return []

        if query_emb is None:
            return []

        threshold = (
            self.similarity_threshold_fast
            if mode == "fast"
            else self.similarity_threshold_agentic
        )
        top_k = self.top_k_fast if mode == "fast" else self.top_k_agentic

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
                if sim < threshold:
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
        return scored[:top_k]

    def format_ephemeral_block(
        self,
        current_command: str = "",
        mode: str = "agentic",
        recent_context: str = "",
    ) -> str:
        if not current_command and not recent_context:
            return ""

        query_text = (current_command or "").strip()
        if recent_context and "No recent conversation" not in recent_context:
            query_text = f"{recent_context.strip()[-300:]} {query_text}".strip()

        matches = self.get_relevant_patterns(query_text, mode=mode)
        if not matches:
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

        return "\n".join(lines).rstrip()

    # ------------------------------------------------------------------
    # Utilities / Debug
    # ------------------------------------------------------------------
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
            self._save_patterns()
            logger.warning("BehavioralPatternEngine: all patterns cleared.")

    def shutdown(self):
        try:
            self._save_patterns()
            logger.info("BehavioralPatternEngine shutdown complete.")
        except Exception as e:
            logger.error(f"BehavioralPatternEngine shutdown error: {e}")


behavioral_pattern_engine = BehavioralPatternEngine()