import os
import json
import threading
import time
import re
import networkx as nx
import numpy as np
from datetime import datetime
from pathlib import Path
from sentence_transformers import SentenceTransformer
from scipy.spatial.distance import cosine
from core.logger.logger import logger


SINGULAR_RELATIONS = {
    "LIVES_IN",
    "LOCATED_IN",
    "AGE",
    "HAS_AGE",
    "FATHER",
    "MOTHER",
    "SPOUSE",
    "WORKS_AT",
    "WORKS_AS",
    "PROFESSION",
    "COLLEGE",
    "STUDIES_AT",
    "DEGREE",
    "CURRENT_PROJECT",
    "HAS_PHONE",
    "HAS_EMAIL",
    "LAPTOP",
    "USES_LAPTOP",
}


class LifetimeMemoryEngine:
    def __init__(self):
        self.db_path = Path("Data/jarvis_memory/lifetime_graph.json")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.graph = nx.DiGraph()
        self._lock = threading.RLock()
        self._embed_lock = threading.RLock()

        logger.info("⏳ Loading Semantic Embedding Model (BAAI/bge-small-en-v1.5) for LTM...")
        self.embedder = SentenceTransformer("BAAI/bge-small-en-v1.5")
        self.node_embeddings = {}
        self.relation_embeddings = {}

        self._load_graph()
        self._start_auto_cleanup()

    def _compute_all_embeddings(self):
        nodes = list(self.graph.nodes())
        if nodes:
            with self._embed_lock:
                embeddings = self.embedder.encode(
                    nodes, normalize_embeddings=True, show_progress_bar=False
                )
            for node, emb in zip(nodes, embeddings):
                self.node_embeddings[node] = emb

        unique_relations = set()
        for u, v, data in self.graph.edges(data=True):
            if "relation" in data and data["relation"]:
                unique_relations.add(str(data["relation"]).upper())

        if unique_relations:
            rel_list = list(unique_relations)
            with self._embed_lock:
                rel_embeddings = self.embedder.encode(
                    rel_list, normalize_embeddings=True, show_progress_bar=False
                )
            for rel, emb in zip(rel_list, rel_embeddings):
                self.relation_embeddings[rel] = emb

    def _get_embedding(self, text: str):
        with self._embed_lock:
            return self.embedder.encode(
                [str(text)], normalize_embeddings=True, show_progress_bar=False
            )[0]

    def embed(self, texts, is_query=False):
        if texts is None:
            return None
        single = isinstance(texts, str)
        if single:
            texts = [texts]
        if not texts:
            return [] if not single else None

        prefix = "Represent this sentence for searching relevant passages: "
        processed = []
        for t in texts:
            if t is None or not str(t).strip():
                processed.append(" ")
            elif is_query:
                processed.append(prefix + str(t))
            else:
                processed.append(str(t))

        try:
            with self._embed_lock:
                embeddings = self.embedder.encode(
                    processed,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                    batch_size=32,
                )
            if single:
                return embeddings[0].tolist()
            return [e.tolist() for e in embeddings]
        except Exception as e:
            logger.error(f"LTM embed failed: {e}")
            return None

    @staticmethod
    def _is_token_match(query_str: str, target_str: str) -> bool:
        """
        Strict whole-word token match (prevents 'Ram' matching inside 'Telegram').
        """
        q_clean = query_str.strip().lower()
        t_clean = target_str.strip().lower()
        if not q_clean or not t_clean:
            return False
        if q_clean == t_clean:
            return True
        q_tokens = {tok for tok in re.findall(r"\w+", q_clean) if len(tok) >= 3}
        t_tokens = {tok for tok in re.findall(r"\w+", t_clean) if len(tok) >= 3}
        if not q_tokens or not t_tokens:
            return False
        return q_tokens.issubset(t_tokens) or t_tokens.issubset(q_tokens)

    def _relation_matches(self, edge_rel: str, requested_rel: str, threshold: float = 0.78) -> bool:
        if not requested_rel:
            return True
        e_rel = (edge_rel or "").strip().upper()
        r_rel = (requested_rel or "").strip().upper()
        if not e_rel:
            return False
        if e_rel == r_rel:
            return True

        e_tokens = {t for t in e_rel.split("_") if len(t) >= 3 and t not in {"HAS", "IS", "THE", "FOR"}}
        r_tokens = {t for t in re.split(r"[_\s]+", r_rel) if len(t) >= 3 and t not in {"HAS", "IS", "THE", "FOR"}}
        if e_tokens and r_tokens and (e_tokens & r_tokens):
            return True

        try:
            e_emb = self.relation_embeddings.get(e_rel)
            if e_emb is None:
                e_emb = self._get_embedding(e_rel.replace("_", " "))
                self.relation_embeddings[e_rel] = e_emb
            r_emb = self.relation_embeddings.get(r_rel)
            if r_emb is None:
                r_emb = self._get_embedding(r_rel.replace("_", " "))
                self.relation_embeddings[r_rel] = r_emb
            sim = 1.0 - float(cosine(e_emb, r_emb))
            return sim >= threshold
        except Exception:
            return False

    def get_all_node_names(self, limit=100):
        with self._lock:
            nodes = sorted(self.graph.degree, key=lambda x: x[1], reverse=True)
            return [n[0] for n in nodes[:limit]]

    def get_all_relations(self, limit=50):
        with self._lock:
            relations = set()
            for u, v, data in self.graph.edges(data=True):
                if not data.get("archived", False) and "relation" in data:
                    relations.add(data["relation"])
            return list(relations)[:limit]

    def _deduplicate_relation(self, rel, threshold=0.86):
        if not rel:
            return rel
        rel = rel.upper().strip().replace(" ", "_")

        with self._lock:
            if rel in self.relation_embeddings:
                return rel

            rel_emb = self._get_embedding(rel.replace("_", " "))
            best_match = None
            best_sim = -1.0

            for ex_rel, ex_emb in self.relation_embeddings.items():
                sim = 1.0 - float(cosine(rel_emb, ex_emb))
                if sim > best_sim:
                    best_sim = sim
                    best_match = ex_rel

            if best_match and best_sim >= threshold:
                logger.info(f"🧠 LTM Semantic Deduplication: Merged '{rel}' -> '{best_match}' (Sim: {best_sim:.2f})")
                return best_match

            self.relation_embeddings[rel] = rel_emb
            return rel

    def _load_graph(self):
        if self.db_path.exists():
            try:
                with open(self.db_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.graph = nx.node_link_graph(data)
            except Exception as e:
                logger.error(f"Error loading graph: {e}")
                self.graph = nx.DiGraph()
        else:
            self.graph = nx.DiGraph()
        self._compute_all_embeddings()

    def _save_graph(self):
        with self._lock:
            try:
                data = nx.node_link_data(self.graph)
                with open(self.db_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.error(f"Error saving graph: {e}")

    def _start_auto_cleanup(self):
        def cleanup_loop():
            while True:
                time.sleep(86400)
                self._archive_old_edges()

        threading.Thread(target=cleanup_loop, daemon=True).start()

    def _archive_old_edges(self):
        with self._lock:
            now = datetime.now()
            to_archive = []
            for u, v, data in self.graph.edges(data=True):
                last_seen = data.get("last_seen")
                if last_seen:
                    try:
                        last_date = datetime.strptime(last_seen, "%Y-%m-%d")
                        if (now - last_date).days > 180:
                            to_archive.append((u, v))
                    except Exception as e:
                        logger.error(f"Date parse error in archive: {e}")
            for u, v in to_archive:
                self.graph.edges[u, v]["archived"] = True
            if to_archive:
                self._save_graph()

    def record_triplet(
        self,
        source,
        relation,
        target,
        date_str=None,
        metadata=None,
        inverse=None,
        is_singular: bool = False,
    ):
        if not source or not relation or not target:
            return

        src = str(source).strip().title()
        tgt = str(target).strip().title()
        rel = self._deduplicate_relation(str(relation).strip())

        if not src or not rel or not tgt:
            return
        date_str = date_str or datetime.now().strftime("%Y-%m-%d")
        singular_flag = bool(is_singular or rel in SINGULAR_RELATIONS)

        with self._lock:
            if src not in self.node_embeddings:
                self.node_embeddings[src] = self._get_embedding(src)
            if tgt not in self.node_embeddings:
                self.node_embeddings[tgt] = self._get_embedding(tgt)

            # Auto-supersede older conflicting target for singular relations
            if singular_flag and self.graph.has_node(src):
                for _, old_tgt, old_data in list(self.graph.out_edges(src, data=True)):
                    if old_tgt != tgt and old_data.get("relation") == rel and not old_data.get("archived", False):
                        self.graph.edges[src, old_tgt]["archived"] = True
                        logger.info(
                            f"🧠 LTM Conflict Resolved: Archived outdated [{src}] --({rel})--> [{old_tgt}] in favor of [{tgt}]"
                        )

            edge_data = self.graph.get_edge_data(src, tgt, default=None)
            if edge_data is not None and edge_data.get("relation") == rel:
                self.graph.edges[src, tgt]["weight"] = edge_data.get("weight", 1) + 1
                self.graph.edges[src, tgt]["count"] = edge_data.get("count", 1) + 1
                self.graph.edges[src, tgt]["last_seen"] = date_str
                if metadata:
                    self.graph.edges[src, tgt]["metadata"] = metadata
                if "first_seen" not in edge_data:
                    self.graph.edges[src, tgt]["first_seen"] = date_str
                if "archived" in self.graph.edges[src, tgt]:
                    del self.graph.edges[src, tgt]["archived"]
            else:
                self.graph.add_edge(
                    src,
                    tgt,
                    relation=rel,
                    date=date_str,
                    weight=1,
                    count=1,
                    first_seen=date_str,
                    last_seen=date_str,
                    metadata=metadata,
                )

            if inverse and isinstance(inverse, dict):
                inv_tgt = str(inverse.get("target", "")).strip().title()
                inv_rel = self._deduplicate_relation(str(inverse.get("relation", "")).strip())

                if inv_rel and inv_tgt:
                    if inv_tgt not in self.node_embeddings:
                        self.node_embeddings[inv_tgt] = self._get_embedding(inv_tgt)
                    inv_edge_data = self.graph.get_edge_data(tgt, inv_tgt, default=None)
                    if inv_edge_data is not None and inv_edge_data.get("relation") == inv_rel:
                        self.graph.edges[tgt, inv_tgt]["weight"] = inv_edge_data.get("weight", 1) + 1
                        self.graph.edges[tgt, inv_tgt]["last_seen"] = date_str
                        if "archived" in self.graph.edges[tgt, inv_tgt]:
                            del self.graph.edges[tgt, inv_tgt]["archived"]
                    else:
                        self.graph.add_edge(
                            tgt,
                            inv_tgt,
                            relation=inv_rel,
                            date=date_str,
                            weight=1,
                            count=1,
                            first_seen=date_str,
                            last_seen=date_str,
                            metadata=metadata,
                        )

        self._save_graph()

    def _find_matching_nodes(self, entity_str: str, threshold: float = 0.85):
        ent_clean = entity_str.strip()
        if not ent_clean:
            return []

        ent_emb = self._get_embedding(ent_clean)
        matched = []

        for node, node_emb in self.node_embeddings.items():
            if self._is_token_match(ent_clean, node):
                matched.append((node, 1.0))
                continue
            sim = 1.0 - float(cosine(ent_emb, node_emb))
            if sim >= threshold:
                matched.append((node, sim))

        matched.sort(key=lambda x: x[1], reverse=True)
        return [m[0] for m in matched[:3]]

    def search_lifetime_memory(self, queries, top_k=5, threshold=0.85):
        if not queries:
            return "Observation: Query empty."

        query_items = []
        if isinstance(queries, str):
            query_items.append({"entity": queries.strip(), "relation": ""})
        elif isinstance(queries, list):
            for q in queries:
                if isinstance(q, dict):
                    ent = str(q.get("entity") or "").strip()
                    rel = str(q.get("relation") or "").strip()
                    if ent or rel:
                        query_items.append({"entity": ent, "relation": rel})
                elif str(q).strip():
                    query_items.append({"entity": str(q).strip(), "relation": ""})

        if not query_items:
            return "Observation: No clear entities provided."

        has_non_user_entity = any(
            item["entity"].lower() != "user" and item["entity"] != ""
            for item in query_items
        )

        results = []
        today = datetime.now().date()

        with self._lock:
            for item in query_items:
                ent = item["entity"]
                req_rel = item["relation"]

                # Prevent hub-node explosion when 'User' is passed without a specific relation alongside other entities
                if ent.lower() == "user" and not req_rel and has_non_user_entity:
                    continue

                matched_nodes = self._find_matching_nodes(ent, threshold=threshold) if ent else []

                # Also allow relation-driven lookup if entity wasn't matched directly or if entity is 'User'
                candidate_edges = []
                if matched_nodes:
                    for m_node in matched_nodes:
                        for u, v, data in self.graph.out_edges(m_node, data=True):
                            candidate_edges.append((u, v, data))
                        for u, v, data in self.graph.in_edges(m_node, data=True):
                            candidate_edges.append((u, v, data))
                elif req_rel:
                    for u, v, data in self.graph.edges(data=True):
                        candidate_edges.append((u, v, data))

                # Also check if 'ent' itself matches a relation name (e.g., user passed entity='father')
                if not candidate_edges and ent:
                    for u, v, data in self.graph.edges(data=True):
                        if self._relation_matches(data.get("relation", ""), ent, threshold=0.82):
                            candidate_edges.append((u, v, data))

                for u, v, data in candidate_edges:
                    if data.get("archived", False):
                        continue

                    edge_rel = data.get("relation", "RELATES_TO")

                    # Surgical relation filter!
                    if req_rel and not self._relation_matches(edge_rel, req_rel, threshold=0.78):
                        continue

                    weight = float(data.get("weight", 1))
                    count = int(data.get("count", 1))
                    last_seen = data.get("last_seen", data.get("date", ""))
                    metadata = data.get("metadata") or {}
                    source_msg = metadata.get("source_message", "")

                    days_diff = 0
                    if last_seen:
                        try:
                            last_date = datetime.strptime(last_seen, "%Y-%m-%d").date()
                            days_diff = (today - last_date).days
                        except Exception:
                            pass

                    time_penalty = 0.5 if days_diff > 180 else 1.0
                    count_boost = min(1.0 + (count - 1) * 0.15, 2.0)
                    rel_boost = 2.0 if (req_rel and self._relation_matches(edge_rel, req_rel, threshold=0.82)) else 1.0

                    adjusted_weight = weight * time_penalty * count_boost * rel_boost

                    res_text = f"[{u}] --({edge_rel})--> [{v}]"
                    if source_msg:
                        res_text += f" (Context: {source_msg})"

                    results.append({
                        "text": res_text,
                        "weight": adjusted_weight,
                    })

        if not results:
            return "Observation: Found nothing in Graph Memory for these entities/relations."

        results.sort(key=lambda x: x["weight"], reverse=True)
        unique = []
        seen = set()
        for r in results:
            if r["text"] not in seen:
                seen.add(r["text"])
                unique.append(r["text"])

        max_items = 8 if len(query_items) == 1 else 12
        res_str = "\n".join(unique[:max_items])
        return f"Observation: Found relations in Graph Memory:\n{res_str}"


ltm_engine = LifetimeMemoryEngine()