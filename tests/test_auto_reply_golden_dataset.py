"""Golden dataset extraction tests.

The extractor reads two live sources, so these tests build a small transcript
database and a matching evidence ledger instead of touching the real state
root. They cover the failure modes found while building it: a self-only SQL
filter that emptied every prompt window, one answer split across several
sends, machine summary blocks, and a ledger row with no inbound text
(2026-09-17).
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from scripts.auto_reply_golden_dataset import (
    APPROVAL_FILENAME,
    DEFAULT_MERGE_GAP_SECONDS,
    QUALITY_APPROVED,
    QUALITY_HUMAN_AUTHORED,
    QUALITY_MODEL_GENERATED,
    QUALITY_UNREVIEWED,
    GoldenPair,
    _connect_readonly,
    _db_has_transcript,
    _is_teachable,
    extract_golden_dataset,
    gold_quality,
    is_gold_quality,
    iter_evidence_pairs,
    iter_self_pairs,
    load_quality_approvals,
    main as golden_main,
    parse_timestamp,
    record_approval,
    resolve_inbound_messages,
    write_dataset,
)


def _build_transcript(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE context_messages (
            id INTEGER PRIMARY KEY,
            source TEXT,
            chat TEXT,
            date TEXT,
            user_name TEXT,
            message TEXT
        );
        CREATE TABLE context_live_events (
            source TEXT,
            chat_id INTEGER,
            log_id INTEGER,
            sent_at INTEGER,
            sender_name TEXT,
            context_message_id INTEGER
        );
        """
    )
    rows = [
        (1, "room-a", "2026-01-01 10:00:00", "문승현", "이거 어떻게 생각해?"),
        (2, "room-a", "2026-01-01 10:00:30", "최연우", "좋은데?"),
        (3, "room-a", "2026-01-01 10:00:40", "최연우", "일단 해보고 판단하자"),
        (4, "room-a", "2026-01-01 11:00:00", "현준", "그건 좀 아닌듯"),
        (5, "room-a", "2026-01-01 11:00:20", "최연우", "그럼 다른 방법 찾아보자"),
        (6, "room-a", "2026-01-01 12:00:00", "현준", "[설명자료] 누가:최연우 무엇을:요약 어떻게:자동 왜:전달"),
        (7, "room-a", "2026-01-01 12:00:30", "최연우", "[설명자료] 누가:최연우 무엇을:요약 어떻게:자동 왜:전달"),
        (8, "room-a", "2026-01-01 13:00:00", "현준", "ㅋㅋㅋㅋㅋㅋㅋㅋ"),
        (9, "room-a", "2026-01-01 13:00:10", "최연우", "ㅋㅋㅋㅋㅋㅋㅋㅋ"),
        (10, "room-a", "2026-01-01 14:00:00", "현준", "링크 봤어?"),
        (11, "room-a", "2026-01-01 14:00:20", "최연우", "https://example.com/only-link"),
        (12, "room-a", "2026-01-01 15:00:00", "현준", "GeekNews 올라왔네"),
        (13, "room-a", "2026-01-01 15:00:10", "최연우", "GeekNews TOP5 · 2026-01-01 1. 소식 https://news.hada.io/topic?id=1"),
        (14, "room-a", "2026-01-01 16:00:00", "문승현", "그래서 결론은?"),
        (15, "room-a", "2026-01-01 16:00:20", "최연우", "결론은 그냥 진행하는걸로"),
    ]
    connection.executemany(
        "INSERT INTO context_messages (id, chat, date, user_name, message) VALUES (?,?,?,?,?)",
        rows,
    )
    connection.execute(
        "INSERT INTO context_live_events (source, chat_id, log_id, sent_at, sender_name, context_message_id) "
        "VALUES ('local', 1, 555001, 1789000000, '문승현', 14)"
    )
    connection.commit()
    connection.close()


def _write_evidence(path: Path) -> None:
    records = [
        {
            "recorded_at": "2026-01-01T16:05:00+0900",
            "chat": "room-a",
            "author": "문승현",
            "log_id": 555001,
            "status": "sent",
            "reply": "진행하고 결과 보면서 조정하자",
            "model": "test/model",
        },
        {
            "recorded_at": "2026-01-01T16:06:00+0900",
            "chat": "room-a",
            "author": "문승현",
            "log_id": 555002,
            "status": "sent",
            "reply": "ㅋㅋㅋㅋㅋ",
            "model": "test/model",
        },
        {
            "recorded_at": "2026-01-01T16:07:00+0900",
            "chat": "room-a",
            "author": "문승현",
            "log_id": 555003,
            "status": "skipped",
            "reply": "보내면 안 되는 답장",
            "model": "test/model",
        },
    ]
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


class TestIsolatedReadOnlyConnection(unittest.TestCase):
    def _write_source_db(self, root: Path) -> Path:
        db = root / "source.sqlite3"
        connection = sqlite3.connect(db)
        try:
            connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO sample (value) VALUES ('copied row')")
            connection.commit()
        finally:
            connection.close()
        return db

    def test_isolated_copy_is_read_only_and_removed_after_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._write_source_db(Path(tmp))
            with _connect_readonly(db) as connection:
                copied_path = Path(connection.execute("PRAGMA database_list").fetchone()[2])
                copied_dir = copied_path.parent
                self.assertNotEqual(copied_path.resolve(), db.resolve())
                self.assertTrue(copied_path.exists())
                self.assertEqual(
                    connection.execute("SELECT value FROM sample").fetchone()[0],
                    "copied row",
                )
                self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("INSERT INTO sample (value) VALUES ('blocked write')")
            self.assertFalse(copied_dir.exists(), "temporary copy must be removed after close")

    def test_isolated_copy_reads_while_source_database_is_exclusively_locked(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._write_source_db(Path(tmp))
            holder = sqlite3.connect(db)
            try:
                holder.execute("BEGIN EXCLUSIVE")
                with _connect_readonly(db) as connection:
                    copied_path = Path(connection.execute("PRAGMA database_list").fetchone()[2])
                    self.assertNotEqual(copied_path.resolve(), db.resolve())
                    self.assertEqual(
                        connection.execute("SELECT value FROM sample").fetchone()[0],
                        "copied row",
                    )
            finally:
                holder.rollback()
                holder.close()


class TestGoldenFilters(unittest.TestCase):
    def test_placeholder_and_affect_are_not_teachable(self):
        for text in ("(이모티콘)", "https://example.com/x", "ㅋㅋㅋㅋㅋㅋ", "ㅠㅠㅠㅠ", "....."):
            with self.subTest(text=text):
                self.assertFalse(_is_teachable(text))

    def test_summary_block_and_feed_post_are_not_teachable(self):
        for text in (
            "[설명자료] 누가:최연우 무엇을:요약",
            "무엇을:요약 어떻게:자동",
            "GeekNews TOP5 · 2026-01-01 1. 소식",
        ):
            with self.subTest(text=text):
                self.assertFalse(_is_teachable(text))

    def test_link_dominated_message_is_not_teachable(self):
        self.assertFalse(_is_teachable("봐 https://example.com/very/long/path/that/dominates"))

    def test_normal_sentence_is_teachable(self):
        self.assertTrue(_is_teachable("일단 해보고 판단하자"))


class TestSelfPairs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db = self.root / "context.sqlite3"
        _build_transcript(self.db)
        self.connection = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        self.addCleanup(self.connection.close)

    def _pairs(self, **kwargs):
        defaults = dict(
            self_authors=["최연우"],
            rooms=None,
            window_size=6,
            min_completion=4,
            max_completion=400,
            limit=0,
        )
        defaults.update(kwargs)
        return list(iter_self_pairs(self.connection, **defaults))

    def test_window_is_built_from_other_speakers(self):
        pairs = self._pairs()
        self.assertTrue(pairs, "self pairs must not be empty")
        for pair in pairs:
            self.assertTrue(pair.window, "every pair needs a prompt window")
            self.assertIn("이거 어떻게 생각해?", pairs[0].prompt)

    def test_consecutive_self_messages_merge_into_one_answer(self):
        pairs = self._pairs()
        merged = [p for p in pairs if p.completion.startswith("좋은데?")]
        self.assertEqual(len(merged), 1)
        self.assertIn("일단 해보고 판단하자", merged[0].completion)

    def test_run_breaks_when_another_speaker_interrupts(self):
        pairs = self._pairs()
        completions = [p.completion for p in pairs]
        self.assertIn("그럼 다른 방법 찾아보자", completions)

    def test_gap_larger_than_merge_window_splits_the_run(self):
        # With a one second merge window the two sends are separate answers.
        # The first keeps its prompt; the continuation has nothing in front of
        # it any more, so it is dropped rather than learned as a bare line.
        pairs = self._pairs(merge_gap=1.0)
        completions = [p.completion for p in pairs]
        self.assertIn("좋은데?", completions)
        self.assertNotIn("일단 해보고 판단하자", completions)
        merged = [p for p in pairs if p.completion.startswith("좋은데?")]
        self.assertIn("이거 어떻게 생각해?", merged[0].prompt)

    def test_summary_blocks_are_not_learned(self):
        pairs = self._pairs()
        for pair in pairs:
            self.assertNotIn("설명자료", pair.completion)
            self.assertNotIn("설명자료", pair.prompt)

    def test_feed_post_is_not_learned(self):
        pairs = self._pairs()
        for pair in pairs:
            self.assertNotIn("GeekNews", pair.completion)

    def test_affect_only_answer_is_dropped(self):
        pairs = self._pairs()
        for pair in pairs:
            self.assertNotEqual(pair.completion.strip("ㅋ "), "")

    def test_limit_stops_early(self):
        pairs = self._pairs(limit=2)
        self.assertEqual(len(pairs), 2)

    def test_room_filter_excludes_other_rooms(self):
        pairs = self._pairs(rooms=["없는방"])
        self.assertEqual(pairs, [])


class TestEvidencePairs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db = self.root / "context.sqlite3"
        _build_transcript(self.db)
        self.evidence = self.root / "reply-evidence.jsonl"
        _write_evidence(self.evidence)

    def _pairs(self, **kwargs):
        connection = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        self.addCleanup(connection.close)
        lookup = resolve_inbound_messages(connection, [555001, 555002])
        defaults = dict(
            window_size=6,
            min_completion=4,
            max_completion=400,
            limit=0,
            inbound_lookup=lookup,
        )
        defaults.update(kwargs)
        return list(iter_evidence_pairs(self.evidence, **defaults))

    def test_a_delivered_row_is_not_gold_without_a_verdict(self):
        # 555001 is a real sentence and 555002 is affect only, so the ledger
        # holds one trainable reply. Both sent rows are gated before the
        # language filters run, and delivery alone is not a verdict.
        counters: dict = {}
        pairs = self._pairs(counters=counters)
        self.assertEqual(pairs, [])
        self.assertEqual(counters["unreviewed_skipped"], 2)

    def test_an_approved_row_becomes_gold_with_its_provenance(self):
        path = self.root / APPROVAL_FILENAME
        record_approval(path, status="approved", log_id=555001, reviewer="문승현")
        pairs = self._pairs(approvals=load_quality_approvals(path))
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].completion, "진행하고 결과 보면서 조정하자")
        self.assertEqual(pairs[0].prompt, "그래서 결론은?")
        self.assertEqual(pairs[0].source, "auto_reply_sent")
        self.assertEqual(pairs[0].quality, QUALITY_APPROVED)
        self.assertEqual(pairs[0].log_id, 555001)
        self.assertEqual(pairs[0].reviewer, "문승현")
        self.assertTrue(is_gold_quality(pairs[0].quality))

    def test_unreviewed_rows_are_kept_only_when_asked_for(self):
        counters: dict = {}
        pairs = self._pairs(require_approval=False, counters=counters)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].quality, QUALITY_MODEL_GENERATED)
        self.assertFalse(is_gold_quality(pairs[0].quality))
        self.assertEqual(counters["unreviewed_kept"], 1)
        self.assertNotIn("approved", counters)

    def test_a_rejected_row_is_dropped_even_in_the_open_mode(self):
        path = self.root / APPROVAL_FILENAME
        record_approval(path, status="rejected", log_id=555001, reviewer="문승현")
        counters: dict = {}
        pairs = self._pairs(
            approvals=load_quality_approvals(path),
            require_approval=False,
            counters=counters,
        )
        self.assertEqual(pairs, [])
        self.assertEqual(counters["rejected"], 1)

    def test_inbound_is_resolved_through_the_live_event_join(self):
        connection = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        self.addCleanup(connection.close)
        lookup = resolve_inbound_messages(connection, [555001])
        self.assertEqual(lookup[555001]["message"], "그래서 결론은?")
        self.assertEqual(lookup[555001]["chat"], "room-a")

    def test_join_failure_leaves_the_prompt_empty_and_skips(self):
        pairs = list(
            iter_evidence_pairs(
                self.evidence,
                window_size=6,
                min_completion=4,
                max_completion=400,
                limit=0,
                inbound_lookup={},
                require_approval=False,
            )
        )
        self.assertEqual(pairs, [], "no inbound text and no window means no pair")

    def test_missing_ledger_yields_nothing(self):
        pairs = list(
            iter_evidence_pairs(
                self.root / "absent.jsonl",
                window_size=6,
                min_completion=4,
                max_completion=400,
                limit=0,
            )
        )
        self.assertEqual(pairs, [])


class TestExtraction(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.state = self.root / "bujamentor"
        self.room = self.state / "rooms" / "1"
        self.room.mkdir(parents=True)
        self.db = self.state / "context.sqlite3"
        _build_transcript(self.db)
        _write_evidence(self.room / "reply-evidence.jsonl")

    def test_transcript_probe_rejects_a_prompt_only_database(self):
        other = self.root / "prompt-only.sqlite3"
        connection = sqlite3.connect(other)
        connection.execute("CREATE TABLE context_operator_prompts (id INTEGER)")
        connection.commit()
        connection.close()
        self.assertFalse(_db_has_transcript(other))
        self.assertTrue(_db_has_transcript(self.db))


    def test_copy_failure_is_fail_closed_and_never_opens_live_database(self):
        real_connect = sqlite3.connect
        opened: list[str] = []

        def tracked_connect(target, *args, **kwargs):
            opened.append(str(target))
            return real_connect(target, *args, **kwargs)

        with mock.patch(
            "scripts.auto_reply_golden_dataset.shutil.copy2",
            side_effect=OSError("copy refused"),
        ), mock.patch(
            "scripts.auto_reply_golden_dataset.sqlite3.connect",
            side_effect=tracked_connect,
        ):
            with self.assertRaisesRegex(
                sqlite3.OperationalError,
                "isolated read-only snapshot unavailable",
            ):
                with _connect_readonly(self.db):
                    self.fail("copy failure must not yield a connection")
        self.assertEqual(opened, [])

        with mock.patch(
            "scripts.auto_reply_golden_dataset.shutil.copy2",
            side_effect=OSError("copy refused"),
        ), mock.patch(
            "scripts.auto_reply_golden_dataset.sqlite3.connect",
            side_effect=tracked_connect,
        ):
            self.assertFalse(_db_has_transcript(self.db))
        self.assertEqual(opened, [])

    def test_extraction_combines_both_sources_and_dedupes(self):
        record_approval(
            self.state / "golden" / APPROVAL_FILENAME,
            status="approved",
            log_id=555001,
            reviewer="문승현",
        )
        pairs, stats = extract_golden_dataset(
            state_root=self.state,
            context_db=self.db,
            self_authors=["최연우"],
            window_size=6,
        )
        self.assertGreater(stats["self_rows"], 0)
        self.assertEqual(stats["evidence_rows"], 1)
        self.assertEqual(stats["evidence_approved"], 1)
        self.assertEqual(stats["approvals_loaded"], 1)
        self.assertTrue(stats["require_approval"])
        self.assertEqual(stats["total_pairs"], len(pairs))
        ids = [p.pair_id for p in pairs]
        self.assertEqual(len(ids), len(set(ids)), "pair ids must be unique")
        approved = [p for p in pairs if p.source == "auto_reply_sent"]
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0].quality, QUALITY_APPROVED)
        self.assertEqual(approved[0].reviewer, "문승현")
        self.assertEqual(approved[0].log_id, 555001)

    def test_extraction_without_a_verdict_keeps_only_human_answers(self):
        pairs, stats = extract_golden_dataset(
            state_root=self.state,
            context_db=self.db,
            self_authors=["최연우"],
            window_size=6,
        )
        self.assertGreater(stats["self_rows"], 0)
        self.assertEqual(stats["evidence_rows"], 0)
        # Both sent rows are gated before the language filters run: 555001 is a
        # real sentence and 555002 is affect only, and neither has a verdict.
        self.assertEqual(stats["evidence_unreviewed_skipped"], 2)
        self.assertTrue(all(p.source == "self_history" for p in pairs))

    def test_extraction_can_opt_into_unreviewed_evidence(self):
        pairs, stats = extract_golden_dataset(
            state_root=self.state,
            context_db=self.db,
            self_authors=["최연우"],
            window_size=6,
            require_approval=False,
        )
        self.assertEqual(stats["evidence_rows"], 1)
        self.assertEqual(stats["evidence_unreviewed_kept"], 1)
        self.assertFalse(stats["require_approval"])
        model_rows = [p for p in pairs if p.source == "auto_reply_sent"]
        self.assertEqual(len(model_rows), 1)
        self.assertEqual(model_rows[0].quality, QUALITY_MODEL_GENERATED)

    def test_missing_context_db_drops_rows_whose_inbound_cannot_be_resolved(self):
        # Without the transcript the ledger's log id cannot be resolved, and
        # this ledger has no inline message, so the row has no prompt to train
        # on. Dropping it is correct; a pair without a question is not data.
        pairs, stats = extract_golden_dataset(
            state_root=self.state,
            context_db=self.root / "absent.sqlite3",
            self_authors=["최연우"],
            require_approval=False,
        )
        self.assertEqual(stats["self_rows"], 0)
        self.assertIn("context_error", stats)
        self.assertEqual(stats["evidence_rows"], 0)
        self.assertEqual(pairs, [])

    def test_inline_message_survives_a_missing_context_db(self):
        inline = self.root / "inline-evidence.jsonl"
        inline.write_text(
            json.dumps(
                {
                    "recorded_at": "2026-01-02T10:00:00+0900",
                    "chat": "room-a",
                    "status": "sent",
                    "message": "내일 일정 어떻게 돼?",
                    "reply": "오전에 회의 하나 있고 오후는 비어 있어",
                },
                ensure_ascii=False,
            )
            + chr(10),
            encoding="utf-8",
        )
        pairs, stats = extract_golden_dataset(
            state_root=self.root / "empty-state",
            context_db=self.root / "absent.sqlite3",
            self_authors=["최연우"],
        )
        self.assertEqual(pairs, [])
        # Now point the same extraction at a state root holding that ledger.
        state = self.root / "inline-state"
        (state / "rooms" / "1").mkdir(parents=True)
        (state / "rooms" / "1" / "reply-evidence.jsonl").write_text(
            inline.read_text(encoding="utf-8"), encoding="utf-8"
        )
        pairs, stats = extract_golden_dataset(
            state_root=state,
            context_db=self.root / "absent.sqlite3",
            self_authors=["최연우"],
            require_approval=False,
        )
        self.assertEqual(stats["evidence_rows"], 1)
        self.assertEqual(pairs[0].prompt, "내일 일정 어떻게 돼?")
        self.assertEqual(pairs[0].completion, "오전에 회의 하나 있고 오후는 비어 있어")
        self.assertEqual(pairs[0].quality, QUALITY_MODEL_GENERATED)

    def test_write_dataset_emits_jsonl_and_summary(self):
        pairs = [
            GoldenPair(
                source="self_history",
                room="room-a",
                prompt="질문",
                completion="답변입니다",
                pair_id="abc",
            )
        ]
        output = self.root / "out" / "golden.jsonl"
        summary = write_dataset(pairs, output, {"total_pairs": 1})
        self.assertTrue(output.exists())
        record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(record["completion"], "답변입니다")
        self.assertEqual(record["quality"], QUALITY_HUMAN_AUTHORED)
        self.assertEqual(record["schema_version"], 2)
        self.assertEqual(summary["output"], str(output))
        self.assertTrue(output.with_suffix(".summary.json").exists())

    def test_write_dataset_is_atomic_on_rewrite(self):
        output = self.root / "out" / "golden.jsonl"
        write_dataset([], output, {"total_pairs": 0})
        pairs = [
            GoldenPair(
                source="self_history",
                room="room-a",
                prompt="질문",
                completion="새 답변",
                pair_id="xyz",
            )
        ]
        write_dataset(pairs, output, {"total_pairs": 1})
        lines = output.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["completion"], "새 답변")
        self.assertFalse(output.with_suffix(".jsonl.tmp").exists())


NL = chr(10)


class TestQualityApprovals(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.path = self.root / APPROVAL_FILENAME

    def test_a_verdict_is_read_back_by_log_id(self):
        record_approval(self.path, status="approved", log_id=555001, reviewer="문승현")
        approvals = load_quality_approvals(self.path)
        self.assertEqual(approvals.approved, 1)
        self.assertEqual(approvals.verdicts, 1)
        verdict = approvals.lookup({"log_id": 555001})
        self.assertEqual(verdict["status"], "approved")
        self.assertEqual(verdict["reviewer"], "문승현")
        self.assertTrue(verdict["reviewed_at"])

    def test_log_id_forms_share_one_key(self):
        record_approval(self.path, status="approved", log_id=555001)
        approvals = load_quality_approvals(self.path)
        for candidate in (555001, 555001.0, "555001"):
            with self.subTest(candidate=candidate):
                self.assertIsNotNone(approvals.lookup({"log_id": candidate}))
        self.assertIsNone(approvals.lookup({"log_id": 555002}))
        self.assertIsNone(approvals.lookup({}))

    def test_an_event_id_can_carry_the_verdict(self):
        record_approval(self.path, status="approved", event_id="evt-9")
        approvals = load_quality_approvals(self.path)
        self.assertIsNotNone(approvals.lookup({"event_id": "evt-9"}))
        self.assertIsNone(approvals.lookup({"event_id": "evt-10"}))

    def test_the_last_verdict_wins(self):
        record_approval(self.path, status="approved", log_id=555001)
        record_approval(self.path, status="rejected", log_id=555001, note="다시 보니 어색함")
        approvals = load_quality_approvals(self.path)
        self.assertEqual(approvals.approved, 1)
        self.assertEqual(approvals.rejected, 1)
        self.assertEqual(approvals.lookup({"log_id": 555001})["status"], "rejected")
        self.assertFalse(is_gold_quality(gold_quality({"log_id": 555001}, approvals)))

    def test_broken_lines_are_counted_not_fatal(self):
        self.path.write_text(
            NL.join(
                [
                    "not json",
                    json.dumps({"status": "maybe", "log_id": 1}),
                    json.dumps(["list"]),
                    json.dumps({"status": "approved", "log_id": 2}),
                ]
            )
            + NL,
            encoding="utf-8",
        )
        approvals = load_quality_approvals(self.path)
        self.assertEqual(approvals.approved, 1)
        self.assertEqual(approvals.malformed, 3)

    def test_a_missing_ledger_is_empty_not_an_error(self):
        approvals = load_quality_approvals(self.root / "absent.jsonl")
        self.assertEqual(approvals.verdicts, 0)
        self.assertIsNone(approvals.lookup({"log_id": 1}))
        self.assertEqual(load_quality_approvals(None).verdicts, 0)

    def test_a_verdict_needs_a_key_and_a_known_status(self):
        with self.assertRaises(ValueError):
            record_approval(self.path, status="maybe", log_id=1)
        with self.assertRaises(ValueError):
            record_approval(self.path, status="approved")
        self.assertFalse(self.path.exists(), "a rejected write must not create the ledger")

    def test_quality_classification_covers_each_source(self):
        cases = [
            ({"source": "self_history"}, QUALITY_HUMAN_AUTHORED),
            ({"source": "auto_reply_sent"}, QUALITY_MODEL_GENERATED),
            ({"quality": "approved"}, QUALITY_APPROVED),
            ({"quality_approved": True}, QUALITY_APPROVED),
            ({"human_reviewed": True}, QUALITY_APPROVED),
            ({}, QUALITY_UNREVIEWED),
        ]
        for record, expected in cases:
            with self.subTest(record=record):
                self.assertEqual(gold_quality(record), expected)
        self.assertTrue(is_gold_quality(QUALITY_HUMAN_AUTHORED))
        self.assertTrue(is_gold_quality(QUALITY_APPROVED))
        self.assertFalse(is_gold_quality(QUALITY_MODEL_GENERATED))
        self.assertFalse(is_gold_quality(QUALITY_UNREVIEWED))

    def test_an_explicit_rejection_beats_the_inline_flag(self):
        record_approval(self.path, status="rejected", log_id=7)
        approvals = load_quality_approvals(self.path)
        self.assertEqual(
            gold_quality({"log_id": 7, "quality": "approved"}, approvals),
            QUALITY_UNREVIEWED,
        )


class TestTimestamp(unittest.TestCase):
    def test_offset_and_z_forms_are_absolute(self):
        self.assertEqual(parse_timestamp("2026-01-01T16:05:00+0900"), 1767251100.0)
        self.assertEqual(parse_timestamp("2026-01-01T07:05:00Z"), 1767251100.0)

    def test_the_naive_transcript_form_is_read_and_ordered(self):
        # The naive form is interpreted in the machine zone, so only ordering
        # and magnitude are asserted.
        first = parse_timestamp("2026-01-01 10:00:30")
        self.assertGreater(first, 1767225600.0)
        self.assertLess(first, 1767312000.0)
        self.assertLess(first, parse_timestamp("2026-01-01 11:00:30"))

    def test_unreadable_and_implausible_clocks_are_unknown(self):
        for value in (
            "",
            None,
            "어제",
            "1970-01-01 00:00:00",
            "2999-01-01 00:00:00",
            float("nan"),
            float("inf"),
            0,
        ):
            with self.subTest(value=value):
                self.assertEqual(parse_timestamp(value), 0.0)


class TestCommandLine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.state = self.root / "bujamentor"
        self.room = self.state / "rooms" / "1"
        self.room.mkdir(parents=True)
        self.db = self.state / "context.sqlite3"
        _build_transcript(self.db)
        _write_evidence(self.room / "reply-evidence.jsonl")

    def _run(self, argv):
        out = StringIO()
        with redirect_stdout(out):
            code = golden_main(argv)
        return code, out.getvalue()

    def _summary(self):
        path = self.state / "golden" / "reply-golden.summary.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_list_candidates_shows_the_rows_without_a_verdict(self):
        code, out = self._run(
            [
                "--state-root", str(self.state),
                "--context-db", str(self.db),
                "--list-candidates",
            ]
        )
        self.assertEqual(code, 0)
        payloads = [json.loads(line) for line in out.splitlines() if line.strip()]
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["log_id"], 555001)
        self.assertEqual(payloads[0]["prompt"], "그래서 결론은?")
        self.assertEqual(payloads[0]["completion"], "진행하고 결과 보면서 조정하자")
        self.assertFalse((self.state / "golden" / "reply-golden.jsonl").exists())

    def test_approve_log_id_records_the_verdict_and_changes_the_dataset(self):
        code, out = self._run(
            [
                "--state-root", str(self.state),
                "--context-db", str(self.db),
                "--approve-log-id", "555001",
                "--reviewer", "문승현",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("품질 판정", out)
        approvals = load_quality_approvals(self.state / "golden" / APPROVAL_FILENAME)
        self.assertEqual(approvals.approved, 1)
        summary = self._summary()
        self.assertEqual(summary["evidence_rows"], 1)
        self.assertEqual(summary["evidence_approved"], 1)

    def test_reject_log_id_keeps_the_row_out_of_the_dataset(self):
        record_approval(
            self.state / "golden" / APPROVAL_FILENAME,
            status="rejected",
            log_id=555001,
        )
        code, _ = self._run(
            ["--state-root", str(self.state), "--context-db", str(self.db)]
        )
        self.assertEqual(code, 0)
        summary = self._summary()
        self.assertEqual(summary["evidence_rows"], 0)
        self.assertEqual(summary["evidence_rejected_skipped"], 1)

    def test_unreviewed_evidence_flag_is_recorded_in_the_summary(self):
        code, _ = self._run(
            [
                "--state-root", str(self.state),
                "--context-db", str(self.db),
                "--include-unreviewed-evidence",
            ]
        )
        self.assertEqual(code, 0)
        summary = self._summary()
        self.assertFalse(summary["require_approval"])
        self.assertEqual(summary["evidence_rows"], 1)
        self.assertEqual(summary["evidence_unreviewed_kept"], 1)


if __name__ == "__main__":
    unittest.main()
