#!/usr/bin/env python3
"""Evaluate Alden Korean GraphRAG retrieval on bounded synthetic fixtures.

The default modes are fully offline. ``bm25`` runs the production BM25 and
keyword-fallback path with dense retrieval forced unavailable. ``fixture-rrf``
runs the same production query path and production RRF merger with a fixed,
synthetic dense ranking from the fixture. It does not claim encoder quality.

``live-rrf`` is explicit opt-in. It builds an isolated temporary dense index
from the same synthetic corpus and calls the configured loopback embedding
adapter. No Kakao database, persistent search index, queue, or configuration is
opened or changed by this evaluator.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import inspect
import itertools
import json
import math
import os
import platform
import sqlite3
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import auto_reply_knowledge_graph as KG  # noqa: E402


DEFAULT_FIXTURE_DIR = ROOT / "tests" / "fixtures" / "alden-retrieval"
DEFAULT_KS = (1, 3, 5)
RRF_TUNING_GRID = (
    (1.0, 0.25),
    (1.0, 0.5),
    (1.0, 0.75),
    (1.0, 1.0),
    (0.75, 1.0),
    (0.5, 1.0),
    (0.25, 1.0),
)
MODES = ("bm25", "fixture-rrf", "live-rrf")
MAX_ENTITIES = 64
MAX_RELATIONS = 64
MAX_QUERIES = 64
MAX_TEXT_LENGTH = 2_000


class FixtureError(ValueError):
    """The bounded synthetic fixture is malformed or unsafe."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FixtureError(f"cannot read fixture {path.name}: {error}") from error
    if not isinstance(raw, dict):
        raise FixtureError(f"fixture {path.name} must contain an object")
    return raw


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded_string(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise FixtureError(f"{field} must be a string")
    text = value.strip()
    if not text and not allow_empty:
        raise FixtureError(f"{field} must not be empty")
    if len(text) > MAX_TEXT_LENGTH:
        raise FixtureError(f"{field} exceeds {MAX_TEXT_LENGTH} characters")
    return text


def _numeric_id(value: Any, field: str, *, allow_zero: bool = False) -> str:
    if type(value) not in (int, str):
        raise FixtureError(f"{field} requires a canonical numeric ID")
    text = str(value)
    if len(text)>19 or not text.isascii() or not text.isdigit() or text != str(int(text)) or not (0 if allow_zero else 1) <= int(text) < 2**63:
        raise FixtureError(f"{field} requires a canonical 64-bit numeric ID")
    return text


def load_fixture(fixture_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    corpus_path = fixture_dir / "corpus.json"
    queries_path = fixture_dir / "queries.json"
    corpus = _load_json(corpus_path)
    judgments = _load_json(queries_path)
    if corpus.get("schema_version") != 1 or judgments.get("schema_version") != 1:
        raise FixtureError("only fixture schema_version=1 is supported")
    if corpus.get("synthetic") is not True or judgments.get("synthetic") is not True:
        raise FixtureError("fixtures must explicitly declare synthetic=true")

    entities = corpus.get("entities")
    relations = corpus.get("relations")
    queries = judgments.get("queries")
    rooms = corpus.get("rooms")
    if not isinstance(entities, list) or not 1 <= len(entities) <= MAX_ENTITIES:
        raise FixtureError(f"entities must contain 1..{MAX_ENTITIES} items")
    if not isinstance(relations, list) or len(relations) > MAX_RELATIONS:
        raise FixtureError(f"relations must contain at most {MAX_RELATIONS} items")
    if not isinstance(queries, list) or not 1 <= len(queries) <= MAX_QUERIES:
        raise FixtureError(f"queries must contain 1..{MAX_QUERIES} items")
    if not isinstance(rooms, list) or len(rooms) > 16:
        raise FixtureError("rooms must contain at most 16 items")

    entity_ids: set[str] = set()
    snapshot = corpus.get("snapshot")
    snapshot_prefix = ""
    if snapshot is not None:
        if not isinstance(snapshot, dict) or snapshot.get("account") != "a" * 64:
            raise FixtureError("snapshot must use the reserved synthetic account")
        snapshot_prefix = "kakao:" + snapshot["account"] + ":"
        for key, maximum in (("rooms", 16), ("authors", 64), ("messages", 512)):
            if not isinstance(snapshot.get(key), list) or len(snapshot[key]) > maximum:
                raise FixtureError(f"snapshot {key} exceeds its bounded fixture limit")
        room_ids, author_ids = set(), set()
        for row in snapshot["rooms"]:
            if not isinstance(row, dict):
                raise FixtureError("snapshot room requires a numeric ID")
            room_id = _numeric_id(row.get("chat_id"), "snapshot chat_id")
            if room_id in room_ids:raise FixtureError("duplicate snapshot room ID")
            room_ids.add(room_id)
            _bounded_string(row.get("label"), "snapshot room label", allow_empty=True)
            entity_ids.add("chat:" + snapshot_prefix + "room:" + room_id)
        for row in snapshot["authors"]:
            if not isinstance(row, dict):
                raise FixtureError("snapshot author requires a numeric ID")
            author_id = _numeric_id(row.get("author_id"), "snapshot author_id", allow_zero=True)
            if author_id in author_ids:raise FixtureError("duplicate snapshot author ID")
            author_ids.add(author_id)
            _bounded_string(row.get("label"), "snapshot author label", allow_empty=True)
            if author_id != "0":entity_ids.add("person:" + snapshot_prefix + "actor:" + author_id)
        message_ids = set()
        for row in snapshot["messages"]:
            if not isinstance(row, dict):raise FixtureError("snapshot message must be an object")
            room_id = _numeric_id(row.get("chat_id"), "snapshot message chat_id")
            author_id = _numeric_id(row.get("author_id"), "snapshot message author_id", allow_zero=True)
            log_id = _numeric_id(row.get("log_id"), "snapshot log_id")
            if room_id not in room_ids or author_id not in author_ids:
                raise FixtureError("snapshot message must refer to declared numeric IDs")
            if (room_id, log_id) in message_ids:raise FixtureError("duplicate snapshot message ID")
            message_ids.add((room_id, log_id))
            if "is_self" in row and type(row["is_self"]) is not bool:raise FixtureError("snapshot is_self must be boolean")
            if "type" in row and (type(row["type"]) is not int or not 0 <= row["type"] <= 255):raise FixtureError("snapshot message type is invalid")
            _bounded_string(row.get("text"), "snapshot message text")
            if KG._message_datetime_kst(row.get("date")) is None:
                raise FixtureError("snapshot message date must be parseable")
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict):
            raise FixtureError(f"entities[{index}] must be an object")
        entity_id = _bounded_string(entity.get("entity_id"), f"entities[{index}].entity_id")
        if entity_id in entity_ids:
            raise FixtureError(f"duplicate entity_id: {entity_id}")
        entity_ids.add(entity_id)
        for field in ("name", "category", "description"):
            _bounded_string(entity.get(field), f"entities[{index}].{field}")
        for field in ("aliases", "facts"):
            values = entity.get(field)
            if not isinstance(values, list) or len(values) > 16:
                raise FixtureError(f"entities[{index}].{field} must be a list of at most 16")
            for item in values:
                _bounded_string(item, f"entities[{index}].{field}")
        evidence = entity.get("evidence")
        if not isinstance(evidence, dict):
            raise FixtureError(f"entities[{index}].evidence must be an object")
        evidence_ids = evidence.get("source_event_ids")
        if not isinstance(evidence_ids, list) or len(evidence_ids) > KG.MAX_EVIDENCE_PER_NODE:
            raise FixtureError(f"entities[{index}] has invalid source_event_ids")
        for evidence_id in evidence_ids:
            value = _bounded_string(evidence_id, f"entities[{index}].source_event_ids")
            if not value.startswith("synthetic:"):
                raise FixtureError("all evidence IDs must use the synthetic: namespace")

    # Product seeding happens before fixture upserts, so judgments may forbid
    # an actually present built-in node even when the fixture doesn't replace it.
    entity_ids.update(item["entity_id"] for item in KG.DEFAULT_ENTITIES)
    query_ids: set[str] = set()
    splits: Counter[str] = Counter()
    for index, query in enumerate(queries):
        if not isinstance(query, dict):
            raise FixtureError(f"queries[{index}] must be an object")
        query_id = _bounded_string(query.get("id"), f"queries[{index}].id")
        if query_id in query_ids:
            raise FixtureError(f"duplicate query id: {query_id}")
        query_ids.add(query_id)
        split = _bounded_string(query.get("split"), f"queries[{index}].split")
        if split not in {"dev", "heldout"}:
            raise FixtureError(f"query {query_id} has unsupported split {split}")
        splits[split] += 1
        _bounded_string(query.get("text"), f"queries[{index}].text")
        relevant = query.get("relevant")
        forbidden = query.get("forbidden")
        dense = query.get("fixture_dense_ranking")
        if not isinstance(relevant, dict) or not isinstance(forbidden, list) or not isinstance(dense, list):
            raise FixtureError(f"query {query_id} has malformed judgments")
        relevant_ids = set(relevant)
        forbidden_ids = {str(item) for item in forbidden}
        dense_ids = {str(item) for item in dense}
        unknown = (relevant_ids | forbidden_ids | dense_ids) - entity_ids
        if unknown:
            raise FixtureError(f"query {query_id} references unknown entities: {sorted(unknown)}")
        if relevant_ids & forbidden_ids:
            raise FixtureError(f"query {query_id} marks an entity relevant and forbidden")
        for entity_id, grade in relevant.items():
            if not isinstance(grade, int) or not 1 <= grade <= 3:
                raise FixtureError(f"query {query_id} grade for {entity_id} must be 1..3")
        expected = query.get("expected_evidence_ids", [])
        if not isinstance(expected, list):
            raise FixtureError(f"query {query_id} expected_evidence_ids must be a list")
        if any(not (str(item).startswith("synthetic:") or (snapshot_prefix and str(item).startswith(snapshot_prefix))) for item in expected):
            raise FixtureError(f"query {query_id} has a non-synthetic evidence ID")
    if not splits["dev"] or not splits["heldout"]:
        raise FixtureError("fixtures must keep separate non-empty dev and heldout splits")

    for index, relation in enumerate(relations):
        if not isinstance(relation, dict):
            raise FixtureError(f"relations[{index}] must be an object")
        source = _bounded_string(relation.get("source_id"), f"relations[{index}].source_id")
        target = _bounded_string(relation.get("target_id"), f"relations[{index}].target_id")
        if source not in entity_ids or target not in entity_ids:
            raise FixtureError(f"relations[{index}] references an unknown entity")
        evidence_id = _bounded_string(
            relation.get("evidence_message_id", ""),
            f"relations[{index}].evidence_message_id",
            allow_empty=True,
        )
        if evidence_id and not evidence_id.startswith("synthetic:"):
            raise FixtureError("relation evidence IDs must use the synthetic: namespace")
    return corpus, judgments


def _write_snapshot_fixture(root: Path, snapshot: dict[str, Any]) -> None:
    """Publish only declared synthetic rows; no Kakao source or account plist."""
    account = snapshot["account"]
    folder = root / "knowledge" / "corpus" / account
    folder.mkdir(parents=True)
    with sqlite3.connect(folder / "context.sqlite3") as db:
        db.executescript("""CREATE TABLE corpus_meta(key TEXT,value TEXT);
            CREATE TABLE context_message_topics(message_id INTEGER,topic TEXT);
            CREATE TABLE context_topic_stats(chat TEXT,topic TEXT,message_count INTEGER);
            CREATE TABLE alden_rooms(chat TEXT,chat_id TEXT,label TEXT);
            CREATE TABLE alden_authors(author_id TEXT,label TEXT);
            CREATE TABLE alden_messages(id INTEGER PRIMARY KEY,chat TEXT,chat_id TEXT,log_id TEXT,author_id TEXT,user_name TEXT,message TEXT,date,is_self INTEGER,message_type INTEGER,source TEXT);
            CREATE VIEW context_messages AS SELECT *,X'' AS vector FROM alden_messages;
            CREATE VIRTUAL TABLE context_messages_fts USING fts5(message,user_name,chat,content='alden_messages',content_rowid='id');""")
        db.executemany("INSERT INTO corpus_meta VALUES(?,?)", [("account",account),("snapshot","synthetic-eval")])
        names = {str(row["author_id"]):row["label"] for row in snapshot["authors"]}
        db.executemany("INSERT INTO alden_authors VALUES(?,?)", names.items())
        for room in snapshot["rooms"]:
            key = f"kakao:{account}:room:{room['chat_id']}"
            db.execute("INSERT INTO alden_rooms VALUES(?,?,?)", (key,str(room["chat_id"]),room["label"]))
        for row in snapshot["messages"]:
            key = f"kakao:{account}:room:{row['chat_id']}"
            db.execute("INSERT INTO alden_messages(chat,chat_id,log_id,author_id,user_name,message,date,is_self,message_type,source) VALUES(?,?,?,?,?,?,?,?,?,?)",(key,str(row["chat_id"]),str(row["log_id"]),str(row["author_id"]),names[str(row["author_id"])],row["text"],row["date"],int(bool(row.get("is_self"))),1,key))
        db.execute("INSERT INTO context_messages_fts(context_messages_fts) VALUES('rebuild')")
    (folder.parent / "current.json").write_text(json.dumps({"schema_version":1,"account":account,"snapshot":"synthetic-eval"}))


def _write_synthetic_graph(root: Path, corpus: dict[str, Any]) -> dict[str, int]:
    if corpus.get("snapshot"):
        _write_snapshot_fixture(root, corpus["snapshot"])
    catalog = {"rooms": corpus["rooms"]}
    (root / "menubar-room-catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
    try:
        # Product queries call ensure_seeded. Seed before a live dense refresh so
        # that the graph watermark and dense index cannot diverge on first use.
        KG.ensure_seeded(conn)
        for entity in corpus["entities"]:
            conn.execute(
                "INSERT INTO kg_entities"
                " (entity_id,name,category,aliases_json,description,key_facts_json,"
                " importance,evidence_json,updated_at) VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(entity_id) DO UPDATE SET name=excluded.name,"
                " category=excluded.category,aliases_json=excluded.aliases_json,"
                " description=excluded.description,key_facts_json=excluded.key_facts_json,"
                " importance=excluded.importance,evidence_json=excluded.evidence_json,"
                " updated_at=excluded.updated_at",
                (
                    entity["entity_id"],
                    entity["name"],
                    entity["category"],
                    json.dumps(entity["aliases"], ensure_ascii=False),
                    entity["description"],
                    json.dumps(entity["facts"], ensure_ascii=False),
                    int(entity.get("importance", 50)),
                    json.dumps(entity["evidence"], ensure_ascii=False),
                    int(entity["updated_at"]),
                ),
            )
        for relation in corpus["relations"]:
            conn.execute(
                "INSERT INTO kg_relations"
                " (source_id,relation,target_id,subject_id,relation_type,object_id,"
                " room_id,valid_from,valid_to,evidence_message_id,context,weight,"
                " evidence_json,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(source_id,relation,target_id) DO UPDATE SET"
                " room_id=excluded.room_id,valid_from=excluded.valid_from,"
                " valid_to=excluded.valid_to,evidence_message_id=excluded.evidence_message_id,"
                " context=excluded.context,weight=excluded.weight,updated_at=excluded.updated_at",
                (
                    relation["source_id"],
                    relation["relation"],
                    relation["target_id"],
                    relation["source_id"],
                    relation["relation"],
                    relation["target_id"],
                    relation.get("room_id", ""),
                    relation.get("valid_from", ""),
                    relation.get("valid_to", ""),
                    relation.get("evidence_message_id", ""),
                    relation["context"],
                    int(relation.get("weight", 50)),
                    json.dumps(
                        {
                            "kind": "ledger",
                            "source_event_ids": [relation.get("evidence_message_id")]
                            if relation.get("evidence_message_id")
                            else [],
                            "chat_id": relation.get("room_id", ""),
                            "confirmed_at": relation.get("valid_from") or None,
                            "retracted": False,
                        },
                        ensure_ascii=False,
                    ),
                    int(relation["updated_at"]),
                ),
            )
        if corpus.get("snapshot"):
            KG.index_chat_entities(conn, root)
            KG.index_person_entities(conn, root)
            KG.index_membership_relations(conn, root)
        KG.write_meta(conn, "last_indexed_at", str(corpus["watermark"]))
        conn.commit()
        entity_count = int(conn.execute("SELECT COUNT(*) FROM kg_entities").fetchone()[0])
        relation_count = int(conn.execute("SELECT COUNT(*) FROM kg_relations").fetchone()[0])
        return {"entities_with_product_seeds": entity_count, "relations_with_product_seeds": relation_count}
    finally:
        conn.close()


def recall_at_k(ranked: list[str], relevant: dict[str, int], k: int) -> float | None:
    if not relevant:
        return None
    return len(set(ranked[:k]) & set(relevant)) / len(relevant)


def ndcg_at_k(ranked: list[str], relevant: dict[str, int], k: int) -> float | None:
    if not relevant:
        return None

    def dcg(grades: Iterable[int]) -> float:
        return sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(grades))

    actual = dcg([int(relevant.get(entity_id, 0)) for entity_id in ranked[:k]])
    ideal = dcg(sorted((int(value) for value in relevant.values()), reverse=True)[:k])
    return actual / ideal if ideal else 0.0


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _mean(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return _round(statistics.fmean(clean)) if clean else None


def _percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    rank = max(1, math.ceil(len(ordered) * fraction))
    return round(ordered[rank - 1], 3)


def _mode_scope(mode: str) -> dict[str, Any]:
    if mode == "bm25":
        return {
            "production_bm25": True,
            "production_rrf": False,
            "encoder_invoked": False,
            "dense_ranking_source": "none_dense_forced_unavailable",
        }
    if mode == "fixture-rrf":
        return {
            "production_bm25": True,
            "production_rrf": True,
            "encoder_invoked": False,
            "dense_ranking_source": "fixed_synthetic_fixture",
        }
    return {
        "production_bm25": True,
        "production_rrf": True,
        "encoder_invoked": True,
        "dense_ranking_source": "configured_loopback_embedding_adapter",
    }


def _evaluate_query(
    root: Path,
    corpus: dict[str, Any],
    query: dict[str, Any],
    *,
    mode: str,
    ks: tuple[int, ...],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter_ns()
    with contextlib.ExitStack() as stack:
        if mode == "bm25":
            stack.enter_context(
                mock.patch.object(
                    KG,
                    "_dense_ann_query",
                    side_effect=RuntimeError("dense disabled by offline evaluator"),
                )
            )
            stack.enter_context(mock.patch.object(KG, "_trace_graph"))
        elif mode == "fixture-rrf":
            dense_hits = [
                (str(entity_id), 1.0 / rank)
                for rank, entity_id in enumerate(query["fixture_dense_ranking"], start=1)
            ]
            stack.enter_context(
                mock.patch.object(
                    KG,
                    "_dense_ann_query",
                    return_value=(dense_hits, str(corpus["watermark"])),
                )
            )
        selected_policy = dict(policy or {})
        ranked_policy = {key:value for key,value in selected_policy.items() if key in {"rrf_weights","rrf_k","candidate_limit"}}
        ranked = KG._query_knowledge_ranked(
            query["text"],
            state_root=root,
            chat_id=query.get("chat_id"),
            also=query.get("also"),
            participant_id=query.get("participant_id"),
            time_from=query.get("time_from"),
            time_to=query.get("time_to"),
            **ranked_policy,
        )
        public_started = time.perf_counter_ns()
        bundle = KG.retrieve_knowledge_bundle(
            query["text"],
            state_root=root,
            chat_id=query.get("chat_id"),
            also=query.get("also"),
            participant_id=query.get("participant_id"),
            time_from=query.get("time_from"),
            time_to=query.get("time_to"),
            **selected_policy,
        )
        elapsed_ms = (time.perf_counter_ns() - public_started) / 1_000_000

    candidates = [str(item) for item in ranked.get("candidates") or []]
    relevant = {str(key): int(value) for key, value in query["relevant"].items()}
    forbidden = {str(item) for item in query.get("forbidden", [])}
    expected_evidence = {str(item) for item in query.get("expected_evidence_ids", [])}
    returned_evidence = {str(item) for item in ranked.get("evidence_ids") or []}
    relation_facts = [str(item) for item in ranked.get("relation_facts") or []]
    relation_provenance = list(ranked.get("relation_provenance") or [])
    bundle_facts = [str(item) for item in bundle.get("facts") or []]
    bundle_candidate_provenance = list(bundle.get("candidate_provenance") or [])
    bundle_relation_provenance = list(bundle.get("relation_provenance") or [])
    bundle_fact_provenance = list(bundle.get("fact_provenance") or [])
    bundle_evidence = {str(item) for item in bundle.get("evidence_ids") or []}
    max_k = max(ks)
    forbidden_hits = [item for item in candidates[:max_k] if item in forbidden]
    bundle_candidate_ids = {
        str(item.get("entity_id"))
        for item in bundle_candidate_provenance
        if isinstance(item, dict) and str(item.get("entity_id") or "")
    }
    bundle_relation_entity_ids = {
        str(item.get(field))
        for item in bundle_relation_provenance
        if isinstance(item, dict)
        for field in ("source_id", "target_id")
        if str(item.get(field) or "")
    }
    forbidden_context_hits = sorted(
        forbidden & (bundle_candidate_ids | bundle_relation_entity_ids)
    )
    forbidden_fact_hits = [
        needle
        for needle in query.get("forbidden_fact_substrings", [])
        if any(str(needle) in fact for fact in bundle_facts)
    ]
    provenance_evidence = {
        str(value)
        for item in bundle_candidate_provenance + bundle_relation_provenance
        if isinstance(item, dict)
        for value in item.get("source_event_ids") or []
        if str(value)
    }
    bundle_boundary_errors: list[str] = []
    if len(bundle_facts) != len(bundle_fact_provenance):
        bundle_boundary_errors.append("fact_provenance_length_mismatch")
    if len(bundle_facts) != len(bundle_candidate_provenance) + len(
        bundle_relation_provenance
    ):
        bundle_boundary_errors.append("typed_provenance_length_mismatch")
    if bundle_evidence != provenance_evidence:
        bundle_boundary_errors.append("evidence_provenance_mismatch")
    missing_evidence = sorted(expected_evidence - returned_evidence)
    expected_mode = KG.SEARCH_MODE_BM25_ONLY if mode == "bm25" else KG.SEARCH_MODE_RRF
    violations: list[dict[str, Any]] = []
    if forbidden_hits:
        violations.append({"code": "forbidden_entity_returned", "values": forbidden_hits})
    if forbidden_context_hits:
        violations.append(
            {"code": "forbidden_context_entity_returned", "values": forbidden_context_hits}
        )
    if forbidden_fact_hits:
        violations.append({"code": "forbidden_fact_returned", "values": forbidden_fact_hits})
    if bundle_boundary_errors:
        violations.append(
            {"code": "bundle_provenance_boundary_mismatch", "values": bundle_boundary_errors}
        )
    if missing_evidence:
        violations.append({"code": "expected_evidence_missing", "values": missing_evidence})
    if str(ranked.get("search_mode")) != expected_mode:
        violations.append(
            {
                "code": "search_mode_mismatch",
                "values": [str(ranked.get("search_mode")), expected_mode],
            }
        )

    return {
        "id": query["id"],
        "split": query["split"],
        "tags": list(query.get("tags", [])),
        "text": query["text"],
        "relevant": relevant,
        "forbidden": sorted(forbidden),
        "candidates": candidates[:max(10, max_k)],
        "candidate_provenance": list(ranked.get("candidate_provenance") or [])[: max(10, max_k)],
        "bm25_candidates": list(ranked.get("bm25_candidates") or []),
        "dense_candidates": list(ranked.get("dense_candidates") or []),
        "entity_facts": list(ranked.get("entity_facts") or [])[:10],
        "relation_facts": relation_facts[:10],
        "relation_provenance": relation_provenance[:10],
        "public_bundle": {
            "facts": bundle_facts,
            "candidate_provenance": bundle_candidate_provenance,
            "relation_provenance": bundle_relation_provenance,
            "fact_provenance": bundle_fact_provenance,
            "evidence_ids": sorted(bundle_evidence),
            "boundary_errors": bundle_boundary_errors,
            "retrieval_policy": bundle.get("retrieval_policy"),
            "context_chars": sum(len(fact) for fact in bundle_facts),
            "context_recall": recall_at_k(sorted(bundle_candidate_ids | bundle_relation_entity_ids), relevant, len(bundle_candidate_ids | bundle_relation_entity_ids)) if relevant else None,
        },
        "returned_evidence_ids": sorted(returned_evidence),
        "expected_evidence_ids": sorted(expected_evidence),
        "metrics": {
            str(k): {
                "recall": _round(recall_at_k(candidates, relevant, k)),
                "ndcg": _round(ndcg_at_k(candidates, relevant, k)),
                "forbidden_hits": [item for item in candidates[:k] if item in forbidden],
            }
            for k in ks
        },
        "search_mode": str(ranked.get("search_mode") or ""),
        "bm25_count": int(ranked.get("bm25_count") or 0),
        "dense_count": int(ranked.get("dense_count") or 0),
        "rrf_weights": dict(ranked.get("rrf_weights") or {}),
        "elapsed_ms": round(elapsed_ms, 3),
        "violations": violations,
    }


def _aggregate(results: list[dict[str, Any]], ks: tuple[int, ...]) -> dict[str, Any]:
    positive = [result for result in results if result["relevant"]]
    forbidden_checks = [result for result in results if result["forbidden"]]
    evidence_checks = [result for result in results if result["expected_evidence_ids"]]
    return {
        "queries": len(results),
        "positive_queries": len(positive),
        "context_macro_recall": _mean(result["public_bundle"]["context_recall"] for result in positive),
        "negative_queries": len(results) - len(positive),
        "metrics": {
            str(k): {
                "macro_recall": _mean(result["metrics"][str(k)]["recall"] for result in positive),
                "macro_ndcg": _mean(result["metrics"][str(k)]["ndcg"] for result in positive),
                "forbidden_query_rate": _round(
                    sum(bool(result["metrics"][str(k)]["forbidden_hits"]) for result in forbidden_checks)
                    / len(forbidden_checks)
                )
                if forbidden_checks
                else None,
            }
            for k in ks
        },
        "evidence_check_pass_rate": _round(
            sum(
                set(result["expected_evidence_ids"]) <= set(result["returned_evidence_ids"])
                for result in evidence_checks
            )
            / len(evidence_checks)
        )
        if evidence_checks
        else None,
        "forbidden_fact_query_count": sum(
            any(item["code"] == "forbidden_fact_returned" for item in result["violations"])
            for result in results
        ),
        "whole_context_forbidden_query_rate": _round(
            sum(
                any(
                    item["code"]
                    in {"forbidden_context_entity_returned", "forbidden_fact_returned"}
                    for item in result["violations"]
                )
                for result in forbidden_checks
            )
            / len(forbidden_checks)
        )
        if forbidden_checks
        else None,
        "public_bundle_boundary_pass_rate": _round(
            sum(not result["public_bundle"]["boundary_errors"] for result in results)
            / len(results)
        )
        if results
        else None,
        "search_mode_counts": dict(Counter(result["search_mode"] for result in results)),
        "latency_ms": {
            "n": len(results),
            "p50": _percentile((result["elapsed_ms"] for result in results), 0.50),
            "p95": _percentile((result["elapsed_ms"] for result in results), 0.95),
        },
    }


def _grouped_metrics(
    results: list[dict[str, Any]], ks: tuple[int, ...], field: str
) -> dict[str, Any]:
    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        values = result.get(field)
        if isinstance(values, list):
            for value in values:
                groups[str(value)].append(result)
        else:
            groups[str(values)].append(result)
    return {name: _aggregate(items, ks) for name, items in sorted(groups.items())}


def _rrf_ranking_from_result(
    result: dict[str, Any], weights: tuple[float, float]
) -> list[str]:
    recency = {
        str(item["entity_id"]): int(item.get("updated_at") or 0)
        for item in result.get("candidate_provenance") or []
        if isinstance(item, dict) and item.get("entity_id")
    }
    return [
        entity_id
        for entity_id, _score in KG._rrf_merge(
            [(str(entity_id), 0.0) for entity_id in result["bm25_candidates"]],
            [(str(entity_id), 0.0) for entity_id in result["dense_candidates"]],
            weights=weights,
            recency=recency,
        )
    ]


def _rrf_weight_metrics(
    results: list[dict[str, Any]],
    weights: tuple[float, float],
    ks: tuple[int, ...],
) -> dict[str, Any]:
    rows = []
    for result in results:
        ranking = _rrf_ranking_from_result(result, weights)
        relevant = result["relevant"]
        forbidden = set(result["forbidden"])
        rows.append(
            {
                "ranking": ranking,
                "relevant": relevant,
                "forbidden": forbidden,
            }
        )
    positive = [row for row in rows if row["relevant"]]
    forbidden_checks = [row for row in rows if row["forbidden"]]
    return {
        "queries": len(rows),
        "positive_queries": len(positive),
        "metrics": {
            str(k): {
                "macro_recall": _mean(
                    recall_at_k(row["ranking"], row["relevant"], k) for row in positive
                ),
                "macro_ndcg": _mean(
                    ndcg_at_k(row["ranking"], row["relevant"], k) for row in positive
                ),
                "forbidden_query_rate": _round(
                    sum(bool(set(row["ranking"][:k]) & row["forbidden"]) for row in forbidden_checks)
                    / len(forbidden_checks)
                )
                if forbidden_checks
                else None,
            }
            for k in ks
        },
    }


def _tune_live_rrf_weights(
    results: list[dict[str, Any]], ks: tuple[int, ...]
) -> dict[str, Any]:
    dev = [result for result in results if result["split"] == "dev"]
    heldout = [result for result in results if result["split"] == "heldout"]
    objective_k = 3 if 3 in ks else max(ks)
    grid = []
    for weights in RRF_TUNING_GRID:
        metrics = _rrf_weight_metrics(dev, weights, ks)
        grid.append({"weights": {"bm25": weights[0], "dense": weights[1]}, "dev": metrics})

    def _selection_key(item: dict[str, Any]) -> tuple[float, ...]:
        weights = item["weights"]
        at_objective = item["dev"]["metrics"][str(objective_k)]
        at_one = item["dev"]["metrics"][str(min(ks))]
        return (
            float(at_objective["macro_ndcg"] or 0.0),
            float(at_objective["macro_recall"] or 0.0),
            float(at_one["macro_ndcg"] or 0.0),
            float(at_one["macro_recall"] or 0.0),
            -abs(float(weights["bm25"]) - 1.0)
            - abs(float(weights["dense"]) - 1.0),
        )

    selected = max(grid, key=_selection_key)
    selected_weights = (
        float(selected["weights"]["bm25"]),
        float(selected["weights"]["dense"]),
    )
    production_defaults = (float(KG.RRF_BM25_WEIGHT), float(KG.RRF_DENSE_WEIGHT))
    return {
        "tuning_split": "dev",
        "tuning_queries": len(dev),
        "heldout_split": "heldout",
        "heldout_queries": len(heldout),
        "objective_order": [
            f"macro_ndcg@{objective_k}",
            f"macro_recall@{objective_k}",
            f"macro_ndcg@{min(ks)}",
            f"macro_recall@{min(ks)}",
            "distance_from_1_1",
        ],
        "grid": grid,
        "selected_weights": selected["weights"],
        "selected_dev": selected["dev"],
        "selected_heldout": _rrf_weight_metrics(heldout, selected_weights, ks),
        "production_defaults_at_run": {
            "bm25": production_defaults[0],
            "dense": production_defaults[1],
        },
        "production_defaults_match_selected": selected_weights == production_defaults,
        "promotion": "analysis_only_bounded_synthetic_dev",
    }


def _source_ref(function: Any) -> dict[str, Any]:
    lines, start = inspect.getsourcelines(function)
    return {
        "file": str(Path(inspect.getsourcefile(function) or "").relative_to(ROOT)),
        "function": function.__name__,
        "line": start,
        "lines": len(lines),
    }


def _production_observations(mode_reports: dict[str, Any]) -> list[dict[str, Any]]:
    by_mode = {
        mode: {result["id"]: result for result in report["query_results"]}
        for mode, report in mode_reports.items()
    }
    observations: list[dict[str, Any]] = [
        {
            "code": "rrf_weights_bounded",
            "status": "confirmed_by_source",
            "source": _source_ref(KG._rrf_merge),
            "reason": f"The merger accepts explicit BM25/dense weights, requires a positive sum, and rejects non-finite or out-of-range values outside {KG.RRF_WEIGHT_MIN}..{KG.RRF_WEIGHT_MAX}.",
        },
        {
            "code": "candidate_provenance_preserved",
            "status": "confirmed_by_source",
            "source": _source_ref(KG._query_knowledge_ranked),
            "reason": "Each returned candidate carries its source kind, event IDs, room, confirmation time, retraction state, and updated_at value.",
        },
        {
            "code": "relation_provenance_preserved",
            "status": "confirmed_by_source",
            "source": _source_ref(KG._query_knowledge_ranked),
            "reason": "Each returned relation carries both endpoint IDs, evidence source and room, validity interval, retraction state, and updated_at value.",
        },
        {
            "code": "public_bundle_reuses_filtered_provenance",
            "status": "confirmed_by_source",
            "source": _source_ref(KG.retrieve_knowledge_bundle),
            "reason": "The public bundle now slices facts, evidence IDs, and entity/relation provenance from the same already-filtered ranked records; focus traversal cannot replace them with raw database relations.",
        },
    ]

    bm25 = by_mode.get("bm25", {})
    fixture_rrf = by_mode.get("fixture-rrf", {})
    spacing_id = "dev-spacing-joined"
    if spacing_id in bm25 and spacing_id in fixture_rrf:
        bm25_hit = bm25[spacing_id]["metrics"]["1"]["recall"]
        rrf_hit = fixture_rrf[spacing_id]["metrics"]["1"]["recall"]
        if bm25_hit == 0.0 and rrf_hit == 1.0:
            observations.append(
                {
                    "code": "joined_spacing_requires_dense_rescue",
                    "status": "observed",
                    "source": _source_ref(KG._fts_query_terms),
                    "query_ids": [spacing_id],
                    "reason": "The joined Korean form misses the spaced lexical form in BM25/keyword fallback; fixed dense ranking rescues it.",
                }
            )

    issue_specs = (
        ("heldout-retracted-entity", "retracted_entity_not_filtered", "Entity evidence is loaded after ranking, but retracted=true is not used to reject candidates."),
        ("heldout-latest-entity", "stale_nonretracted_candidate_remains", "Recency breaks equal-score ties, but an older non-retracted alias remains eligible without an explicit valid_to or stable identity link; the evaluator does not guess that the two aliases are identical."),
        ("heldout-provenance-room-leak", "entity_provenance_room_not_scoped", "Room filtering uses person/chat entity IDs; a global topic from another evidence room can pass."),
        ("heldout-relation-time-scope", "entity_time_scope_not_applied", "Relation validity is filtered, but old time entities remain eligible candidates."),
    )
    for query_id, code, reason in issue_specs:
        affected = [
            mode
            for mode, results in by_mode.items()
            if query_id in results
            and any(
                item["code"] == "forbidden_entity_returned"
                for item in results[query_id]["violations"]
            )
        ]
        if affected:
            observations.append(
                {
                    "code": code,
                    "status": "observed",
                    "source": _source_ref(KG._query_knowledge_ranked),
                    "query_ids": [query_id],
                    "modes": affected,
                    "reason": reason,
                }
            )
    return observations


def _tune_retrieval_policy(root: Path, corpus: dict[str, Any], judgments: dict[str, Any], ks: tuple[int,...]) -> dict[str,Any]:
    """Run real public requests; select on dev only, then verify heldout once.

    Exact local encoder outputs are memoized per input within this fixture
    run. The production ANN/filter/fusion/bundle path still runs for each
    policy; no fixture ranking replaces a real encoder result.
    """
    dev = [q for q in judgments["queries"] if q["split"] == "dev"]
    heldout = [q for q in judgments["queries"] if q["split"] == "heldout"]
    original = KG._local_dense_embeddings
    cache: dict[tuple[Any,...],Any] = {}
    calls = 0
    hits = 0
    def cached(texts, **kwargs):
        nonlocal calls, hits
        key = (tuple(texts), kwargs.get("input_type",KG.DENSE_INPUT_TYPE_QUERY), kwargs.get("model_id"))
        if key not in cache:
            calls += 1
            cache[key] = original(texts, **kwargs)
        else:
            hits += 1
        return cache[key]
    defaults = {"rrf_k":KG.RRF_K,"candidate_limit":KG.RETRIEVAL_CANDIDATE_LIMIT,"max_context_chars":KG.RETRIEVAL_CONTEXT_CHARS,"rrf_weights":(KG.RRF_BM25_WEIGHT,KG.RRF_DENSE_WEIGHT)}
    policies = [defaults]
    for k, count, budget, weights in itertools.product((20,60,100),(12,40,80),(512,2048,8000),((1.0,0.5),(1.0,1.0),(0.5,1.0))):
        p = {"rrf_k":k,"candidate_limit":count,"max_context_chars":budget,"rrf_weights":weights}
        if p not in policies:policies.append(p)
    grid = []
    with mock.patch.object(KG,"_local_dense_embeddings",side_effect=cached):
        for policy in policies:
            rows = [_evaluate_query(root,corpus,q,mode="live-rrf",ks=ks,policy=policy) for q in dev]
            grid.append({"policy":policy,"dev":_aggregate(rows,ks)})
        def selection_key(row):
            summary=row["dev"];rank=summary["metrics"]["3" if 3 in ks else str(max(ks))]
            policy=row["policy"]
            distance=sum(policy[key]!=defaults[key] for key in defaults)
            return (-float(summary["whole_context_forbidden_query_rate"] or 0),float(summary["context_macro_recall"] or 0),float(rank["macro_ndcg"] or 0),float(rank["macro_recall"] or 0),-distance)
        selected=max(grid,key=selection_key)
        verification=[_evaluate_query(root,corpus,q,mode="live-rrf",ks=ks,policy=selected["policy"]) for q in heldout]
    return {"tuning_split":"dev","tuning_queries":len(dev),"heldout_split":"heldout","heldout_queries":len(heldout),"selection_order":["whole_context_safety","context_recall","ndcg_at_3","recall_at_3","distance_from_defaults"],"grid":grid,"selected_policy":selected["policy"],"selected_dev":selected["dev"],"selected_heldout":_aggregate(verification,ks),"heldout_results":verification,"encoder_actual_calls":calls,"encoder_memoized_hits":hits,"scope":"real local embeddings memoized by exact input; real production public requests for every policy; heldout never used for selection"}


def evaluate_fixture(
    fixture_dir: Path = DEFAULT_FIXTURE_DIR,
    *,
    modes: Iterable[str] = ("bm25", "fixture-rrf"),
    ks: tuple[int, ...] = DEFAULT_KS,
    tune_policy: bool = False,
) -> dict[str, Any]:
    load_average_start = tuple(round(value, 2) for value in os.getloadavg())
    requested_modes = tuple(dict.fromkeys(str(mode) for mode in modes))
    if not requested_modes or any(mode not in MODES for mode in requested_modes):
        raise ValueError(f"modes must be selected from {MODES}")
    if not ks or any(k <= 0 or k > 20 for k in ks):
        raise ValueError("k values must be in 1..20")
    ks = tuple(sorted(set(int(k) for k in ks)))
    corpus, judgments = load_fixture(fixture_dir)
    mode_reports: dict[str, Any] = {}

    for mode in requested_modes:
        with tempfile.TemporaryDirectory(prefix="alden-retrieval-eval-") as temp_dir:
            root = Path(temp_dir)
            graph_counts = _write_synthetic_graph(root, corpus)
            dense_refresh: dict[str, Any] | None = None
            if mode == "live-rrf":
                conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
                refresh_started = time.perf_counter_ns()
                try:
                    dense_refresh = dict(KG.refresh_dense_index(conn, root))
                finally:
                    conn.close()
                dense_refresh["elapsed_ms"] = round(
                    (time.perf_counter_ns() - refresh_started) / 1_000_000, 3
                )
                dense_refresh["model_id"] = KG._active_dense_embedding_model()
                dense_refresh["endpoint"] = KG._dense_endpoint_identity()
                if dense_refresh.get("status") != "indexed":
                    raise RuntimeError(
                        "live dense refresh failed: "
                        + json.dumps(dense_refresh, ensure_ascii=False, sort_keys=True)
                    )
            query_results = [
                _evaluate_query(root, corpus, query, mode=mode, ks=ks)
                for query in judgments["queries"]
            ]
            mode_reports[mode] = {
                "scope": _mode_scope(mode),
                "graph_counts": graph_counts,
                "dense_refresh": dense_refresh,
                "overall": _aggregate(query_results, ks),
                "by_split": _grouped_metrics(query_results, ks, "split"),
                "by_tag": _grouped_metrics(query_results, ks, "tags"),
                "query_results": query_results,
            }
            if mode == "live-rrf":
                mode_reports[mode]["weight_tuning"] = _tune_live_rrf_weights(
                    query_results, ks
                )
                if tune_policy:
                    mode_reports[mode]["policy_tuning"] = _tune_retrieval_policy(root,corpus,judgments,ks)

    report: dict[str, Any] = {
        "schema_version": 1,
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "dataset": "bounded synthetic Korean GraphRAG retrieval",
            "fixture_entities": len(corpus["entities"]),
            "fixture_relations": len(corpus["relations"]),
            "queries": len(judgments["queries"]),
            "splits": dict(Counter(query["split"] for query in judgments["queries"])),
            "k_values": list(ks),
            "original_kakao_db_accessed": False,
            "persistent_index_accessed": False,
            "writes": "isolated temporary directories plus explicitly requested report paths",
        },
        "fixture": {
            "directory": str(fixture_dir.relative_to(ROOT)) if fixture_dir.is_relative_to(ROOT) else str(fixture_dir),
            "corpus_sha256": _sha256(fixture_dir / "corpus.json"),
            "queries_sha256": _sha256(fixture_dir / "queries.json"),
        },
        "runtime": {
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "logical_cpu_count": os.cpu_count(),
            "load_average_start_1m_5m_15m": list(load_average_start),
            "load_average_end_1m_5m_15m": [round(value, 2) for value in os.getloadavg()],
            "production_file": str(Path(KG.__file__).relative_to(ROOT)),
            "production_sha256": _sha256(Path(KG.__file__)),
            "search_index_version": KG.SEARCH_INDEX_VERSION,
        },
        "modes": mode_reports,
    }
    report["production_observations"] = _production_observations(mode_reports)
    if "bm25" in mode_reports and "fixture-rrf" in mode_reports:
        report["offline_comparison"] = {
            str(k): {
                metric: _round(
                    float(mode_reports["fixture-rrf"]["overall"]["metrics"][str(k)][metric])
                    - float(mode_reports["bm25"]["overall"]["metrics"][str(k)][metric])
                )
                for metric in ("macro_recall", "macro_ndcg")
            }
            for k in ks
        }
    if "bm25" in mode_reports and "live-rrf" in mode_reports:
        bm25_latency = mode_reports["bm25"]["overall"]["latency_ms"]
        live_latency = mode_reports["live-rrf"]["overall"]["latency_ms"]
        report["live_comparison"] = {
            "quality_delta": {
                str(k): {
                    metric: _round(
                        float(mode_reports["live-rrf"]["overall"]["metrics"][str(k)][metric])
                        - float(mode_reports["bm25"]["overall"]["metrics"][str(k)][metric])
                    )
                    for metric in ("macro_recall", "macro_ndcg")
                }
                for k in ks
            },
            "latency_delta_ms": {
                percentile: round(
                    float(live_latency[percentile]) - float(bm25_latency[percentile]), 3
                )
                for percentile in ("p50", "p95")
            },
            "latency_ratio": {
                percentile: round(
                    float(live_latency[percentile]) / float(bm25_latency[percentile]), 3
                )
                for percentile in ("p50", "p95")
            },
        }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    scope = report["scope"]
    ks = [int(value) for value in scope["k_values"]]
    artifacts = report.get("artifacts") or {}
    selected_modes = " ".join(
        f"--mode {mode}" for mode in report.get("modes", {})
    )
    json_output = artifacts.get(
        "json", "docs/architecture/alden-retrieval-eval-20260930.json"
    )
    markdown_output = artifacts.get(
        "markdown", "docs/architecture/alden-retrieval-eval-20260930.md"
    )
    lines = [
        "# Alden Korean GraphRAG retrieval evaluation — " + report["evaluated_at_utc"][:10],
        "",
        "## Scope",
        "",
        f"This report evaluates {scope['queries']} private-data-free synthetic queries "
        f"({scope['splits']['dev']} dev, {scope['splits']['heldout']} held-out) over "
        f"{scope['fixture_entities']} fixture entities and {scope['fixture_relations']} relations.",
        "The evaluator did not open a Kakao database, active queue, persistent graph, or production configuration.",
        "",
        "`fixture-rrf` exercises the production BM25 path and production RRF merger with a fixed synthetic dense ranking. "
        "It does not invoke E5 and is not evidence of actual embedding quality. `live-rrf` is an explicit opt-in mode.",
        "",
        "## Reproduction",
        "",
        "```bash",
        "python3 scripts/evaluate_alden_retrieval.py \\",
        f"  --fixture-dir {report['fixture']['directory']} {selected_modes} \\",
        f"  --output {json_output} \\",
        f"  --markdown-output {markdown_output}",
        "python3 -m unittest tests.test_alden_retrieval_eval",
        "```",
        "",
        "## Results",
        "",
        "Recall and nDCG are macro averages over positive queries. Forbidden@3 measures candidate leakage in the top three; Whole-context leak inspects the bounded public bundle's candidate provenance, relation endpoints, and forbidden relation text.",
        "",
        "| Mode | Split | n | Recall@1 | nDCG@1 | Recall@3 | nDCG@3 | Context recall | Forbidden@3 | Whole-context leak | p50 ms | p95 ms |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode, mode_report in report["modes"].items():
        for split in ("overall", "dev", "heldout"):
            metrics = mode_report["overall"] if split == "overall" else mode_report["by_split"][split]
            at1 = metrics["metrics"].get("1", {})
            at3 = metrics["metrics"].get("3", metrics["metrics"].get(str(ks[-1]), {}))
            lines.append(
                f"| {mode} | {split} | {metrics['queries']} | {at1.get('macro_recall')} | "
                f"{at1.get('macro_ndcg')} | {at3.get('macro_recall')} | {at3.get('macro_ndcg')} | "
                f"{metrics['context_macro_recall']} | {at3.get('forbidden_query_rate')} | {metrics['whole_context_forbidden_query_rate']} | "
                f"{metrics['latency_ms']['p50']} | "
                f"{metrics['latency_ms']['p95']} |"
            )

    if "live-rrf" in report["modes"]:
        refresh = report["modes"]["live-rrf"]["dense_refresh"]
        tuning = report["modes"]["live-rrf"]["weight_tuning"]
        comparison = report.get("live_comparison", {})
        lines.extend(
            [
                "",
                "## Live E5 readback",
                "",
                f"The existing loopback service reported `{refresh['model_id']}`. The evaluator indexed "
                f"{refresh['indexed']} synthetic/product-seed entities in an isolated temporary database in "
                f"{refresh['elapsed_ms']} ms without restarting the service.",
                f"Against BM25, live RRF quality deltas were {comparison.get('quality_delta', {})}; "
                f"latency deltas were {comparison.get('latency_delta_ms', {})} ms "
                f"(ratios {comparison.get('latency_ratio', {})}).",
                f"RRF weight selection used only {tuning['tuning_queries']} dev queries. "
                f"It selected {tuning['selected_weights']}; the separate "
                f"{tuning['heldout_queries']}-query held-out result is "
                f"{tuning['selected_heldout']['metrics']}. This bounded selection is "
                "recorded as analysis only and does not by itself promote production defaults.",
            ]
        )
        policy = report["modes"]["live-rrf"].get("policy_tuning")
        if policy:
            lines.extend(["", "## Bounded policy selection", "",
                f"Only the {policy['tuning_queries']} dev queries selected from {len(policy['grid'])} combinations of weights, RRF k, candidate count and text budget. Selected: `{policy['selected_policy']}`.",
                f"The {policy['heldout_queries']} held-out queries were evaluated once after selection; context recall: {policy['selected_heldout']['context_macro_recall']}, whole-context leak: {policy['selected_heldout']['whole_context_forbidden_query_rate']}.",
                "Exact E5 outputs are memoized only during this policy grid. Grid replay timings exclude repeated encoder calls and must not be reported as fresh-query or production percentiles. The results table above uses unmemoized public requests."])

    lines.extend(["", "## Observed production gaps", ""])
    for observation in report["production_observations"]:
        source = observation["source"]
        modes = ", ".join(observation.get("modes", []))
        suffix = f" Modes: {modes}." if modes else ""
        lines.append(
            f"- `{observation['code']}` — {observation['reason']} "
            f"Source: `{source['file']}:{source['line']}` (`{source['function']}`).{suffix}"
        )

    lines.extend(["", "## Exact limitations", ""])
    lines.extend(
        [
            "- The corpus is synthetic and bounded; it does not estimate production traffic prevalence.",
            "- Offline fixture RRF validates fusion, filtering, provenance aggregation, and ranking metrics. It does not validate the E5 encoder, ANN recall, or live model latency.",
            "- Search latency here measures a tiny temporary SQLite graph in the current process. It is a regression signal, not an installed-app or production percentile.",
            f"- Host load averages during this run were {report['runtime']['load_average_start_1m_5m_15m']} at start and {report['runtime']['load_average_end_1m_5m_15m']} at end across {report['runtime']['logical_cpu_count']} logical CPUs; no GPU attribution was collected.",
            "- Candidate, relation, and per-fact provenance are structured in the public bundle. Whole-context leakage checks bundle relation endpoint IDs and forbidden relation text; free-form entity fact text is linked through its candidate provenance.",
            (
                "- The live dense result is one bounded run against the already-ready loopback adapter. "
                "It writes only a temporary synthetic index and does not establish production-traffic percentiles."
                if "live-rrf" in report["modes"]
                else "- A live dense run can be scheduled after competing GPU benchmarks finish: add `--mode live-rrf`. That mode writes only a temporary index but invokes the loopback embedding adapter."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _parse_ks(raw: str) -> tuple[int, ...]:
    try:
        values = tuple(sorted({int(item.strip()) for item in raw.split(",") if item.strip()}))
    except ValueError as error:
        raise argparse.ArgumentTypeError("--k must be comma-separated integers") from error
    if not values or any(value <= 0 or value > 20 for value in values):
        raise argparse.ArgumentTypeError("--k values must be in 1..20")
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--mode", choices=MODES, action="append", dest="modes")
    parser.add_argument("--k", type=_parse_ks, default=DEFAULT_KS)
    parser.add_argument("--output", type=Path, help="Write the structured JSON report")
    parser.add_argument("--markdown-output", type=Path, help="Write the Markdown summary")
    parser.add_argument("--json", action="store_true", help="Also print the JSON report")
    parser.add_argument("--tune-policy", action="store_true", help="Select bounded RRF/candidate/context settings on dev with real local embeddings")
    arguments = parser.parse_args(argv)

    try:
        report = evaluate_fixture(
            arguments.fixture_dir.resolve(),
            modes=arguments.modes or ("bm25", "fixture-rrf"),
            ks=arguments.k,
            tune_policy=arguments.tune_policy,
        )
    except (FixtureError, RuntimeError, ValueError) as error:
        parser.error(str(error))

    def _display_path(path: Path | None) -> str | None:
        if path is None:
            return None
        resolved = path.resolve()
        try:
            return str(resolved.relative_to(ROOT))
        except ValueError:
            return str(resolved)

    report["artifacts"] = {
        key: value
        for key, value in {
            "json": _display_path(arguments.output),
            "markdown": _display_path(arguments.markdown_output),
        }.items()
        if value is not None
    }

    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if arguments.markdown_output:
        arguments.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        arguments.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    if arguments.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for mode, mode_report in report["modes"].items():
            overall = mode_report["overall"]
            at3 = overall["metrics"].get("3", overall["metrics"][str(max(arguments.k))])
            print(
                f"{mode}: n={overall['queries']} recall@3={at3['macro_recall']} "
                f"ndcg@3={at3['macro_ndcg']} forbidden@3={at3['forbidden_query_rate']} "
                f"p50={overall['latency_ms']['p50']}ms p95={overall['latency_ms']['p95']}ms"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
