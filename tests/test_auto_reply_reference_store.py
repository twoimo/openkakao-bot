"""Lecture-style reference packs harvested into hashed vector memory."""

from __future__ import annotations

import os
import struct
import sys
import threading
import time
import types

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "scripts" / "auto_reply_reference_store.py"
MENUBAR = ROOT / "scripts" / "auto-reply-menubar.py"
SWIFT = ROOT / "macos" / "AutoReplyMenu" / "main.swift"

# auto-reply-worker.py imports its sibling modules by bare name.
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ReferenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = load("auto_reply_reference_store", STORE)

    def test_quality_gate_keeps_lecture_and_drops_noise(self):
        lecture = [
            "1. 한국은 중국이다.",
            "그 근거가 궁금해?",
            "2. 채권 발행을 통해 돈을 찍어낸다",
            "이해가 안가지 왜 그런지 설명간다.",
            "중요한 개념이니 아주 상세하게 풀게",
            "3. 채권: 정부나 기관만이 발행하는 빚문서",
        ]
        score = self.store.quality_score(
            texts=lecture,
            image_count=1,
            message_count=6,
            other_speaker_chars=0,
            author_chars=sum(len(item) for item in lecture),
        )
        self.assertGreaterEqual(score, self.store.MIN_QUALITY_SCORE)
        noise = self.store.quality_score(
            texts=["<gajae-code-system-prompt> You are GJC"],
            image_count=0,
            message_count=1,
            other_speaker_chars=0,
            author_chars=40,
        )
        self.assertEqual(noise, 0)

    def test_harvest_stores_who_what_how_why_and_embeds(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "context.sqlite3"
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE context_messages(id INTEGER PRIMARY KEY, source TEXT, chat TEXT, date TEXT, user_name TEXT, message TEXT, vector BLOB)"
            )
            connection.execute(
                "CREATE TABLE context_message_topics(message_id INTEGER, topic TEXT, PRIMARY KEY(message_id, topic))"
            )
            rows = [
                (1, "live", "부자멘토멘티", "2026-08-20 10:00:00", "문승현", "사진 차트 캡처"),
                (2, "live", "부자멘토멘티", "2026-08-20 10:00:08", "문승현", "대아티아이 기술적 분석. 한줄 결론은 단기 조정은 나왔지만 아직 20일선 위에 있고 MACD도 살아 있어서 상승 추세 내 눌림목으로 볼 수 있어."),
                (3, "live", "부자멘토멘티", "2026-08-20 10:00:16", "문승현", "이유는 4,900원 돌파 실패 시 박스권 가능성이 커서 그래. 매수는 천천히."),
                (4, "live", "부자멘토멘티", "2026-08-20 10:00:20", "현준", "ㅇㅇ"),
                (5, "live", "부자멘토멘티", "2026-08-20 10:01:00", "주원", "고마워"),
            ]
            connection.executemany(
                "INSERT INTO context_messages(id, source, chat, date, user_name, message, vector) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(*row, b"\x00" * 512) for row in rows],
            )
            connection.commit()
            connection.close()
            harvested = self.store.harvest_reference_packs(db)
            self.assertGreaterEqual(harvested["stored"], 1)
            listed = self.store.collect_reference_list(db, harvest=False)
            self.assertEqual(listed["source"], "references")
            self.assertGreaterEqual(listed["count"], 1)
            row = listed["rows"][0]
            self.assertEqual(row["user_name"], "문승현")
            self.assertIn("[설명자료]", row["message"])
            self.assertIn("누가:문승현", row["message"])
            self.assertIn("무엇을:", row["message"])
            self.assertIn("어떻게:", row["message"])
            self.assertIn("왜:", row["message"])
            self.assertEqual(row["kind"], "reference")
            self.assertFalse(row["editable"])
            self.assertEqual(row["vector_dim"], 128)
            self.assertTrue(row["vector_preview"].startswith("128차원"))
            self.assertIn("stocks", row["topics"])
            connection = sqlite3.connect(db)
            synthesized = connection.execute(
                "SELECT message FROM context_messages WHERE message LIKE ?",
                ("%[설명자료]%",),
            ).fetchone()
            connection.close()
            self.assertIsNotNone(synthesized)
            self.assertIn("누가:문승현", synthesized[0])
            self.assertIn("핵심:", synthesized[0])
            self.assertNotEqual(
                synthesized[0].split("핵심:", 1)[-1].strip(),
                "\n".join(
                    [
                        "사진 차트 캡처",
                        "대아티아이 기술적 분석. 한줄 결론은 단기 조정은 나왔지만 아직 20일선 위에 있고 MACD도 살아 있어서 상승 추세 내 눌림목으로 볼 수 있어.",
                        "이유는 4,900원 돌파 실패 시 박스권 가능성이 커서 그래. 매수는 천천히.",
                    ]
                ),
            )

    def test_harvest_flushes_before_slow_cli_enrichment(self):
        """A slow enrich must not keep the shared context DB RESERVED.

        Every room's `context-sync-local` writes this same database. Holding
        the implicit write transaction across the CLI image analysis made each
        room fail with "database is locked" and restarted the auto-reply group
        in a loop (2026-09-18).
        """
        store = self.store
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "context.sqlite3"
            image = Path(temporary) / "shot.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n")
            connection = sqlite3.connect(db)
            store.ensure_reference_schema(connection)
            connection.commit()
            lecture = (
                "1. 대아티아이 기술적 분석을 아주 상세하게 설명한다. "
                "단기 조정은 나왔지만 아직 20일선 위에 있고 MACD도 살아 있어서 "
                "상승 추세 내 눌림목으로 볼 수 있다. "
                "2. 이유는 4,900원 돌파 실패 시 박스권 가능성이 커서 그렇다. "
                "3. 매수는 천천히 나눠서 들어가는 게 좋고 손절선은 20일선이다. "
                "중요한 개념이니 아주 상세하게 풀어서 정리해 두고 다음에도 "
                "같은 기준으로 판단할 수 있게 남겨 둔다."
            )
            events = [
                {
                    "log_id": 10,
                    "sender": "문승현",
                    "sent_at": 1000,
                    "date": "2026-09-18 10:00:00",
                    "message": "사진 차트 캡처",
                },
                {
                    "log_id": 11,
                    "sender": "문승현",
                    "sent_at": 1005,
                    "date": "2026-09-18 10:00:05",
                    "message": lecture,
                },
                {
                    "log_id": 12,
                    "sender": "문승현",
                    "sent_at": 1010,
                    "date": "2026-09-18 10:00:10",
                    "message": lecture,
                },
                {
                    "log_id": 20,
                    "sender": "현준",
                    "sent_at": 5000,
                    "date": "2026-09-18 11:00:00",
                    "message": "사진 매매 일지",
                },
                {
                    "log_id": 21,
                    "sender": "현준",
                    "sent_at": 5005,
                    "date": "2026-09-18 11:00:05",
                    "message": lecture.replace("대아티아이", "삼성전자").replace(
                        "4,900원", "72,000원"
                    ),
                },
                {
                    "log_id": 22,
                    "sender": "현준",
                    "sent_at": 5010,
                    "date": "2026-09-18 11:00:10",
                    "message": lecture.replace("대아티아이", "삼성전자").replace(
                        "4,900원", "72,000원"
                    ),
                },
            ]
            blocked: list[str] = []
            probes: list[int] = []

            def probe(*_args, **_kwargs):
                probes.append(1)
                other = sqlite3.connect(db, timeout=0.0, isolation_level=None)
                try:
                    other.execute("BEGIN IMMEDIATE")
                    other.execute("ROLLBACK")
                except sqlite3.OperationalError as error:
                    blocked.append(str(error))
                finally:
                    other.close()
                return {"what": "요약", "how": "", "why": ""}

            stored, _scanned = store._harvest_event_stream(
                connection,
                events,
                checkpoint_key="probe",
                source="live",
                chat="부자멘토멘티",
                chat_id=417780809780519,
                encode_blob=store.encode_vector_blob,
                now="2026-09-18 10:00:00",
                image_loader=lambda _cluster: [image],
                analyzer=probe,
                vision_left=[4, 4],
            )
            connection.close()
        self.assertEqual(stored, 2)
        self.assertEqual(len(probes), 2)
        self.assertEqual(blocked, [])

    def test_local_group_read_flushes_pending_pack_writes(self):
        """A group's CLI read must not run inside the caller's transaction."""
        store = self.store
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "context.sqlite3"
            connection = sqlite3.connect(db)
            store.ensure_reference_schema(connection)
            # A pack write the caller has not committed yet.
            connection.execute(
                "INSERT INTO context_retrieval_meta(key, value) VALUES ('pending-pack', '1')"
            )
            blocked: list[str] = []
            reads: list[int] = []

            def local_messages(chat_id: int) -> list[dict]:
                reads.append(chat_id)
                other = sqlite3.connect(db, timeout=0.0, isolation_level=None)
                try:
                    other.execute("BEGIN IMMEDIATE")
                    other.execute("ROLLBACK")
                except sqlite3.OperationalError as error:
                    blocked.append(str(error))
                finally:
                    other.close()
                return []

            stored, scanned = store._harvest_local_groups(
                connection,
                encode_blob=store.encode_vector_blob,
                now="2026-09-18 10:00:00",
                local_groups=lambda: [
                    {
                        "chat_type": store.LOCAL_GROUP_CHAT_TYPE,
                        "members": store.LOCAL_GROUP_MIN_MEMBERS,
                        "chat_id": 417780809780519,
                        "title": "부자멘토멘티",
                        "last_updated_at": 1,
                    }
                ],
                local_messages=local_messages,
            )
            connection.close()
        self.assertEqual(reads, [417780809780519])
        self.assertEqual(blocked, [])
        self.assertEqual((stored, scanned), (0, 0))

    def test_seed_prompt_defaults_inserts_missing_instruction_45(self):
        prompts = load(
            "auto_reply_operator_prompt_store",
            ROOT / "scripts" / "auto_reply_operator_prompt_store.py",
        )

        connection = sqlite3.connect(":memory:")
        connection.execute(
            """
            CREATE TABLE context_operator_prompts(
                id INTEGER PRIMARY KEY,
                prompt_key TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                kind TEXT NOT NULL,
                body TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                enabled INTEGER NOT NULL,
                builtin INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                source TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO context_operator_prompts(
                prompt_key, title, kind, body, sort_order, enabled, builtin, updated_at, source
            ) VALUES (?, ?, ?, ?, 0, 1, 1, ?, ?)
            """,
            ("instruction.1", "old", "instruction", "keep me", "2026-08-21 00:00:00", "test"),
        )
        added = prompts.seed_prompt_defaults(connection)
        self.assertGreaterEqual(added, 1)
        row = connection.execute(
            "SELECT title, body FROM context_operator_prompts WHERE prompt_key = ?",
            ("instruction.45",),
        ).fetchone()
        kept = connection.execute(
            "SELECT body FROM context_operator_prompts WHERE prompt_key = ?",
            ("instruction.1",),
        ).fetchone()
        connection.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "설명 자료 근거")
        self.assertIn("[설명자료]", row[1])
        self.assertEqual(kept[0], "keep me")

    @staticmethod
    def _prompt_store():
        return load(
            "auto_reply_operator_prompt_store",
            ROOT / "scripts" / "auto_reply_operator_prompt_store.py",
        )

    @staticmethod
    def _create_prompt_table(connection, *, body_nullable=False, with_builtin=True):
        body_clause = "body TEXT" + ("" if body_nullable else " NOT NULL")
        builtin_clause = "builtin INTEGER NOT NULL," if with_builtin else ""
        connection.execute(
            "CREATE TABLE context_operator_prompts("
            " id INTEGER PRIMARY KEY,"
            " prompt_key TEXT NOT NULL UNIQUE,"
            " title TEXT NOT NULL,"
            " kind TEXT NOT NULL,"
            f" {body_clause},"
            " sort_order INTEGER NOT NULL,"
            " enabled INTEGER NOT NULL,"
            f" {builtin_clause}"
            " updated_at TEXT NOT NULL,"
            " source TEXT NOT NULL)"
        )

    @staticmethod
    def _insert_prompt(
        connection,
        prompt_key,
        *,
        body="기존 본문",
        title="기존 제목",
        sort_order=0,
        enabled=1,
        builtin=1,
        updated_at="2026-08-21 05:34:11",
        source="menubar-operator",
    ):
        connection.execute(
            """
            INSERT INTO context_operator_prompts(
                prompt_key, title, kind, body, sort_order, enabled, builtin, updated_at, source
            ) VALUES (?, ?, 'instruction', ?, ?, ?, ?, ?, ?)
            """,
            (prompt_key, title, body, sort_order, enabled, builtin, updated_at, source),
        )

    @staticmethod
    def _default_item(prompts, key):
        return next(item for item in prompts.load_prompt_defaults() if item["key"] == key)

    def test_seed_prompt_defaults_refreshes_drifted_builtin_body(self):
        prompts = self._prompt_store()
        default = self._default_item(prompts, "instruction.17")
        connection = sqlite3.connect(":memory:")
        self._create_prompt_table(connection)
        self._insert_prompt(
            connection,
            "instruction.17",
            title="옛 제목",
            body="If inbound accuses this account of being AI/봇, write a curious question asking which part felt off.",
            sort_order=17,
        )
        row_id = connection.execute("SELECT id FROM context_operator_prompts").fetchone()[0]
        prompts.seed_prompt_defaults(connection)
        row = connection.execute(
            "SELECT id, title, body, sort_order, enabled, builtin, source, updated_at "
            "FROM context_operator_prompts WHERE prompt_key = ?",
            ("instruction.17",),
        ).fetchone()
        connection.close()
        self.assertEqual(row[0], row_id)
        self.assertEqual(row[1], default["title"])
        self.assertNotIn("question asking which part felt off", row[2])
        self.assertEqual(row[2], default["body"])
        self.assertEqual(row[3], 17)
        self.assertEqual(row[4], 1)
        self.assertEqual(row[5], 1)
        self.assertEqual(row[6], "menubar-operator")
        self.assertNotEqual(row[7], "2026-08-21 05:34:11")

    def test_seed_prompt_defaults_leaves_custom_rows_untouched(self):
        prompts = self._prompt_store()
        connection = sqlite3.connect(":memory:")
        self._create_prompt_table(connection)
        self._insert_prompt(
            connection,
            "custom.style_tell_avoid",
            body="운영자가 직접 쓴 본문",
            sort_order=45,
            builtin=0,
        )
        # A custom row that reuses a builtin key must stay untouched as well.
        self._insert_prompt(
            connection,
            "instruction.12",
            body="내장 키를 쓰는 사용자 정의 본문",
            sort_order=99,
            builtin=0,
        )
        query = (
            "SELECT id, prompt_key, title, body, sort_order, enabled, builtin, updated_at, source "
            "FROM context_operator_prompts WHERE builtin = 0 ORDER BY id"
        )
        before = connection.execute(query).fetchall()
        prompts.seed_prompt_defaults(connection)
        after = connection.execute(query).fetchall()
        connection.close()
        self.assertEqual(len(before), 2)
        self.assertEqual(after, before)

    def test_seed_prompt_defaults_preserves_disabled_builtin_row(self):
        prompts = self._prompt_store()
        default = self._default_item(prompts, "instruction.27")
        connection = sqlite3.connect(":memory:")
        self._create_prompt_table(connection)
        self._insert_prompt(
            connection,
            "instruction.27",
            body="Keep the reply under 137 characters.",
            sort_order=27,
            enabled=0,
        )
        prompts.seed_prompt_defaults(connection)
        row = connection.execute(
            "SELECT body, enabled, sort_order, builtin, source FROM context_operator_prompts "
            "WHERE prompt_key = ?",
            ("instruction.27",),
        ).fetchone()
        connection.close()
        self.assertEqual(row[0], default["body"])
        self.assertEqual(row[1], 0)
        self.assertEqual(row[2], 27)
        self.assertEqual(row[3], 1)
        self.assertEqual(row[4], "menubar-operator")

    def test_seed_prompt_defaults_refresh_is_idempotent(self):
        prompts = self._prompt_store()
        connection = sqlite3.connect(":memory:")
        self._create_prompt_table(connection)
        self._insert_prompt(connection, "instruction.17", body="예전 문구", sort_order=17)
        first_added = prompts.seed_prompt_defaults(connection)
        snapshot = connection.execute(
            "SELECT * FROM context_operator_prompts ORDER BY id"
        ).fetchall()
        second_added = prompts.seed_prompt_defaults(connection)
        after = connection.execute(
            "SELECT * FROM context_operator_prompts ORDER BY id"
        ).fetchall()
        connection.close()
        self.assertGreaterEqual(first_added, 1)
        self.assertEqual(second_added, 0)
        self.assertEqual(after, snapshot)

    def test_refresh_builtin_prompt_bodies_tolerates_odd_tables(self):
        prompts = self._prompt_store()
        connection = sqlite3.connect(":memory:")
        self._create_prompt_table(connection, with_builtin=False)
        connection.execute(
            "INSERT INTO context_operator_prompts("
            "prompt_key, title, kind, body, sort_order, enabled, updated_at, source"
            ") VALUES ('instruction.17', '봇 의심 대응', 'instruction', '예전 문구', 17, 1,"
            " '2026-08-21 05:34:11', 'menubar-operator')"
        )
        self.assertEqual(prompts.refresh_builtin_prompt_bodies(connection), 0)
        self.assertEqual(
            connection.execute("SELECT body FROM context_operator_prompts").fetchone()[0],
            "예전 문구",
        )
        connection.close()

        connection = sqlite3.connect(":memory:")
        self._create_prompt_table(connection, body_nullable=True)
        self._insert_prompt(connection, "instruction.17", body=None, sort_order=17)
        self.assertEqual(prompts.refresh_builtin_prompt_bodies(connection), 0)
        self.assertIsNone(
            connection.execute("SELECT body FROM context_operator_prompts").fetchone()[0]
        )
        connection.close()

    def test_menubar_lists_reference_source_and_swift_exposes_menu(self):
        source = SWIFT.read_text(encoding="utf-8")
        # 지식 그래프 창 하나로 합쳐지면서 제목이 바뀌었다 (2026-09-17).
        self.assertIn('title: "지식 그래프 (대화 기억)"', source)
        self.assertIn("설명 자료", source)
        self.assertIn('"references"', source)
        self.assertIn("누가 무엇을 어떻게 왜", source)
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("lecture-pack-v2", changelog)
        rust = (ROOT / "src" / "context" / "mod.rs").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS context_reference_packs(", rust)
        self.assertIn('item.result.message.starts_with(REFERENCE_PREFIX)', rust)
        menubar = MENUBAR.read_text(encoding="utf-8")
        # 지식 그래프가 같은 목록에 들어오면서 이름이 바뀌었다 (2026-09-17).
        self.assertIn('"references"', menubar)
        self.assertIn("VECTOR_LIST_SOURCES", menubar)
        worker = (ROOT / "scripts" / "auto-reply-worker.py").read_text(encoding="utf-8")
        # 2026-09-18: the handoff moved into the idle cycle, which brackets
        # every slow idle step (this one included) with a fresh liveness stamp.
        self.assertIn("harvest = _maybe_harvest_reference_packs", worker)
        self.assertIn("harvest()", worker)

    def test_local_group_with_unchanged_stamp_is_not_reread(self):
        """A group whose stamp has not moved must not pay for another read.

        Re-reading every group on every pass cost 12-20 seconds on a real
        machine, which is what fenced the room (2026-09-17).
        """
        lecture = (
            "대아티아이 기술적 분석. 한줄 결론은 단기 조정은 나왔지만 아직 20일선 위에 있고 "
            "MACD도 살아 있어서 상승 추세 내 눌림목으로 볼 수 있어. "
            "이유는 4,900원 돌파 실패 시 박스권 가능성이 커서 그래. 매수는 천천히."
        )
        sent_at = 1755684000
        reads: list[int] = []

        def groups():
            return [
                {
                    "chat_id": 260330955968694,
                    "chat_type": 1,
                    "title": "NIMDA 인수인계",
                    "members": 4,
                    "last_updated_at": sent_at + 16,
                }
            ]

        def messages(chat_id: int):
            reads.append(chat_id)
            return [
                {
                    "log_id": 11,
                    "chat_id": chat_id,
                    "sender_name": "최연우",
                    "message": lecture,
                    "message_type": 1,
                    "sent_at": sent_at,
                },
                {
                    "log_id": 12,
                    "chat_id": chat_id,
                    "sender_name": "성린이형",
                    "message": "차트 근거는 20일선과 MACD가 같이 살아 있다는 점이야. 아주 상세하게 풀게.",
                    "message_type": 1,
                    "sent_at": sent_at + 8,
                },
                {
                    "log_id": 13,
                    "chat_id": chat_id,
                    "sender_name": "최연우",
                    "message": "중요한 개념이니 아주 상세하게 풀게. 지지선과 저항선도 같이 봐야 해.",
                    "message_type": 1,
                    "sent_at": sent_at + 16,
                },
            ]

        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "context.sqlite3"
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE context_messages(id INTEGER PRIMARY KEY, source TEXT, chat TEXT, date TEXT, user_name TEXT, message TEXT, vector BLOB)"
            )
            connection.commit()
            connection.close()
            first = self.store.harvest_reference_packs(
                db, local_groups=groups, local_messages=messages
            )
            self.assertTrue(first["ok"])
            self.assertEqual(reads, [260330955968694])

            # 두 번째 수확은 같은 스탬프를 보므로 그룹을 다시 읽지 않는다.
            second = self.store.harvest_reference_packs(
                db, local_groups=groups, local_messages=messages
            )
            self.assertTrue(second["ok"])
            self.assertEqual(reads, [260330955968694])
            self.assertEqual(second["scanned"], 0)

    def test_local_group_stamp_moves_when_chat_changes(self):
        """A new stamp must bring the group back into the harvest."""
        lecture = (
            "대아티아이 기술적 분석. 한줄 결론은 단기 조정은 나왔지만 아직 20일선 위에 있고 "
            "MACD도 살아 있어서 상승 추세 내 눌림목으로 볼 수 있어. "
            "이유는 4,900원 돌파 실패 시 박스권 가능성이 커서 그래. 매수는 천천히."
        )
        sent_at = 1755684000
        reads: list[int] = []
        stamp = [sent_at + 16]

        def groups():
            return [
                {
                    "chat_id": 260330955968694,
                    "chat_type": 1,
                    "title": "NIMDA 인수인계",
                    "members": 4,
                    "last_updated_at": stamp[0],
                }
            ]

        def messages(chat_id: int):
            reads.append(chat_id)
            return [
                {
                    "log_id": 11,
                    "chat_id": chat_id,
                    "sender_name": "최연우",
                    "message": lecture,
                    "message_type": 1,
                    "sent_at": sent_at,
                },
                {
                    "log_id": 12,
                    "chat_id": chat_id,
                    "sender_name": "성린이형",
                    "message": "차트 근거는 20일선과 MACD가 같이 살아 있다는 점이야. 아주 상세하게 풀게.",
                    "message_type": 1,
                    "sent_at": sent_at + 8,
                },
            ]

        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "context.sqlite3"
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE context_messages(id INTEGER PRIMARY KEY, source TEXT, chat TEXT, date TEXT, user_name TEXT, message TEXT, vector BLOB)"
            )
            connection.commit()
            connection.close()
            self.store.harvest_reference_packs(
                db, local_groups=groups, local_messages=messages
            )
            self.assertEqual(reads, [260330955968694])
            stamp[0] = sent_at + 64
            self.store.harvest_reference_packs(
                db, local_groups=groups, local_messages=messages
            )
            self.assertEqual(reads, [260330955968694, 260330955968694])

    def test_worker_harvest_does_not_block_the_idle_loop(self):
        """The harvest must run off the worker thread.

        Inline harvesting held `idle` past the supervisor's 15 second phase
        limit, so the supervisor fenced the room as `reply_worker_unhealthy`
        and replies stopped (2026-09-17).
        """
        worker = load("auto_reply_worker_harvest", ROOT / "scripts" / "auto-reply-worker.py")
        calls: list[float] = []
        started = threading.Event()

        def fake_harvest(*args, **kwargs):
            calls.append(kwargs.get("deadline_at") or 0.0)
            started.set()
            time.sleep(0.4)
            return {"ok": True, "complete": True}

        module = types.ModuleType("auto_reply_reference_store")
        module.harvest_reference_packs = fake_harvest
        original = sys.modules.get("auto_reply_reference_store")
        sys.modules["auto_reply_reference_store"] = module
        try:
            begin = time.time()
            worker._maybe_harvest_reference_packs(force=True)
            elapsed = time.time() - begin
        finally:
            if original is None:
                sys.modules.pop("auto_reply_reference_store", None)
            else:
                sys.modules["auto_reply_reference_store"] = original
        # The caller returns immediately; the harvest itself is still running.
        self.assertLess(elapsed, 0.2)
        self.assertTrue(started.wait(2.0))
        self.assertEqual(len(calls), 1)
        self.assertGreater(calls[0], begin)

    def test_worker_starts_only_one_harvest_at_a_time(self):
        worker = load("auto_reply_worker_harvest_single", ROOT / "scripts" / "auto-reply-worker.py")
        calls: list[float] = []
        release = threading.Event()

        def fake_harvest(*args, **kwargs):
            calls.append(time.time())
            release.wait(2.0)
            return {"ok": True, "complete": True}

        module = types.ModuleType("auto_reply_reference_store")
        module.harvest_reference_packs = fake_harvest
        original = sys.modules.get("auto_reply_reference_store")
        sys.modules["auto_reply_reference_store"] = module
        try:
            worker._maybe_harvest_reference_packs(force=True)
            worker._maybe_harvest_reference_packs(force=True)
            worker._maybe_harvest_reference_packs(force=True)
            time.sleep(0.3)
        finally:
            release.set()
            if original is None:
                sys.modules.pop("auto_reply_reference_store", None)
            else:
                sys.modules["auto_reply_reference_store"] = original
        self.assertEqual(len(calls), 1)

    def test_photo_count_reads_album_and_kakao_types(self):
        self.assertEqual(self.store._photo_count("사진"), 1)
        self.assertEqual(self.store._photo_count("사진 3장"), 3)
        self.assertEqual(self.store._photo_count("[사진]"), 1)
        self.assertEqual(self.store._photo_count("", 2), 1)
        self.assertEqual(self.store._photo_count("", 27), 1)
        self.assertEqual(self.store._photo_count("안녕", 1), 0)

    def test_harvest_reads_historical_chat_when_live_events_belong_elsewhere(self):
        lecture = (
            "대아티아이 기술적 분석. 한줄 결론은 단기 조정은 나왔지만 아직 20일선 위에 있고 "
            "MACD도 살아 있어서 상승 추세 내 눌림목으로 볼 수 있어. "
            "이유는 4,900원 돌파 실패 시 박스권 가능성이 커서 그래. 매수는 천천히."
        )
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "context.sqlite3"
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE context_messages(id INTEGER PRIMARY KEY, source TEXT, chat TEXT, date TEXT, user_name TEXT, message TEXT, vector BLOB)"
            )
            connection.execute(
                "CREATE TABLE context_message_topics(message_id INTEGER, topic TEXT, PRIMARY KEY(message_id, topic))"
            )
            connection.execute(
                """
                CREATE TABLE context_live_events(
                    source TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    log_id INTEGER NOT NULL,
                    sent_at INTEGER NOT NULL,
                    sender_name TEXT NOT NULL,
                    message_digest TEXT NOT NULL,
                    disposition TEXT NOT NULL,
                    auto_generated INTEGER NOT NULL,
                    context_message_id INTEGER,
                    style_message_id INTEGER,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(source, chat_id, log_id)
                )
                """
            )
            connection.executemany(
                "INSERT INTO context_messages(id, source, chat, date, user_name, message, vector) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (1, "live", "부자멘토멘티", "2026-08-20 10:00:00", "현준", "ㅇㅇ", b"\x00" * 512),
                    (2, "live", "부자멘토멘티", "2026-08-20 10:00:01", "주원", "고마워", b"\x00" * 512),
                    (3, "live", "NIMDA 인수인계", "2026-08-20 11:00:00", "최연우", "사진 3장", b"\x00" * 512),
                    (4, "live", "NIMDA 인수인계", "2026-08-20 11:00:08", "최연우", lecture, b"\x00" * 512),
                    (5, "live", "NIMDA 인수인계", "2026-08-20 11:00:20", "성린이형", "감사합니다", b"\x00" * 512),
                    (6, "live", "NIMDA 인수인계", "2026-08-20 11:00:21", "현준", "ㅇㅋ", b"\x00" * 512),
                ],
            )
            connection.execute(
                """
                INSERT INTO context_live_events(
                    source, chat_id, log_id, sent_at, sender_name, message_digest,
                    disposition, auto_generated, context_message_id, style_message_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "live",
                    417780809780519,
                    99,
                    1755684000,
                    "현준",
                    "digest",
                    "keep",
                    0,
                    1,
                    None,
                    "2026-08-20 10:00:00",
                ),
            )
            connection.commit()
            connection.close()
            harvested = self.store.harvest_reference_packs(db)
            self.assertGreaterEqual(harvested["stored"], 1)
            listed = self.store.collect_reference_list(db, harvest=False)
            chats = {row["chat"] for row in listed["rows"]}
            self.assertIn("NIMDA 인수인계", chats)
            authors = {row["user_name"] for row in listed["rows"]}
            self.assertIn("최연우", authors)

    def test_local_group_photo_lecture_is_embedded(self):
        lecture = (
            "대아티아이 기술적 분석. 한줄 결론은 단기 조정은 나왔지만 아직 20일선 위에 있고 "
            "MACD도 살아 있어서 상승 추세 내 눌림목으로 볼 수 있어. "
            "이유는 4,900원 돌파 실패 시 박스권 가능성이 커서 그래. 매수는 천천히."
        )
        sent_at = 1755684000

        def groups():
            return [
                {
                    "chat_id": 260330955968694,
                    "chat_type": 1,
                    "title": "NIMDA 인수인계",
                    "members": 4,
                },
                {
                    "chat_id": 1,
                    "chat_type": 4,
                    "title": "오픈채팅 메가룸",
                    "members": 800,
                },
            ]

        def messages(chat_id: int):
            self.assertEqual(chat_id, 260330955968694)
            return [
                {
                    "log_id": 11,
                    "chat_id": chat_id,
                    "sender_name": "최연우",
                    "message": "",
                    "message_type": 27,
                    "sent_at": sent_at,
                },
                {
                    "log_id": 12,
                    "chat_id": chat_id,
                    "sender_name": "최연우",
                    "message": lecture,
                    "message_type": 1,
                    "sent_at": sent_at + 8,
                },
                {
                    "log_id": 13,
                    "chat_id": chat_id,
                    "sender_name": "최연우",
                    "message": "차트 근거는 20일선과 MACD가 같이 살아 있다는 점이야. 중요한 개념이니 아주 상세하게 풀게.",
                    "message_type": 1,
                    "sent_at": sent_at + 16,
                },
            ]

        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "context.sqlite3"
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE context_messages(id INTEGER PRIMARY KEY, source TEXT, chat TEXT, date TEXT, user_name TEXT, message TEXT, vector BLOB)"
            )
            connection.execute(
                "CREATE TABLE context_message_topics(message_id INTEGER, topic TEXT, PRIMARY KEY(message_id, topic))"
            )
            connection.commit()
            connection.close()
            harvested = self.store.harvest_reference_packs(
                db, local_groups=groups, local_messages=messages
            )
            self.assertGreaterEqual(harvested["stored"], 1)
            listed = self.store.collect_reference_list(db, harvest=False)
            self.assertTrue(listed["rows"])
            row = listed["rows"][0]
            self.assertEqual(row["chat"], "NIMDA 인수인계")
            self.assertEqual(row["user_name"], "최연우")
            self.assertIn("사진", row["status_label"])
            self.assertGreaterEqual(row["vector_dim"], 128)
            connection = sqlite3.connect(db)
            synthesized = connection.execute(
                "SELECT message FROM context_messages WHERE message LIKE ?",
                ("%[설명자료]%",),
            ).fetchone()
            connection.close()
            self.assertIsNotNone(synthesized)
            self.assertIn("누가:최연우", synthesized[0])
            self.assertIn("핵심:", synthesized[0])
            self.assertNotIn("중요한 개념이니 아주 상세하게 풀게.\n차트 근거는", synthesized[0])


    def test_joint_image_text_analysis_is_embedded_not_raw_dump(self):
        lecture = (
            "대아티아이 기술적 분석. 한줄 결론은 단기 조정은 나왔지만 아직 20일선 위에 있고 "
            "MACD도 살아 있어서 상승 추세 내 눌림목으로 볼 수 있어."
        )
        sent_at = 1755684000

        def groups():
            return [
                {
                    "chat_id": 260330955968694,
                    "title": "NIMDA 인수인계",
                    "chat_type": 1,
                    "members": 8,
                }
            ]

        def messages(chat_id: int):
            self.assertEqual(chat_id, 260330955968694)
            return [
                {
                    "log_id": 21,
                    "chat_id": chat_id,
                    "sender_name": "최연우",
                    "message": "",
                    "message_type": 27,
                    "sent_at": sent_at,
                    "author_id": 99,
                },
                {
                    "log_id": 22,
                    "chat_id": chat_id,
                    "sender_name": "최연우",
                    "message": lecture,
                    "message_type": 1,
                    "sent_at": sent_at + 8,
                    "author_id": 99,
                },
                {
                    "log_id": 23,
                    "chat_id": chat_id,
                    "sender_name": "최연우",
                    "message": "차트 근거는 20일선과 MACD가 같이 살아 있다는 점이야. 중요한 개념이니 아주 상세하게 풀게.",
                    "message_type": 1,
                    "sent_at": sent_at + 16,
                    "author_id": 99,
                },
            ]

        def image_loader(cluster):
            self.assertTrue(cluster)
            image = Path(self._vision_dir) / "chart.jpg"
            return [image]

        def analyzer(pack, texts, paths):
            self.assertTrue(texts)
            self.assertEqual([path.name for path in paths], ["chart.jpg"])
            return {
                "what": "대아티아이 차트 눌림목",
                "how": "이미지 캔들과 텍스트를 대조해 설명",
                "why": "단기 조정 이후 추세 유지를 보여주려고",
                "image_findings": "20일선 위에서 거래량이 줄어든 캔들",
                "claims": ["20일선 위 유지", "MACD 상승 유지"],
                "synthesis": "차트는 상승 추세 눌림목이고 급하게 사지 말라는 설명",
            }

        with tempfile.TemporaryDirectory() as temporary:
            self._vision_dir = temporary
            image = Path(temporary) / "chart.jpg"
            image.write_bytes(b"\xff\xd8\xff\xd9")
            db = Path(temporary) / "context.sqlite3"
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE context_messages(id INTEGER PRIMARY KEY, source TEXT, chat TEXT, date TEXT, user_name TEXT, message TEXT, vector BLOB)"
            )
            connection.execute(
                "CREATE TABLE context_message_topics(message_id INTEGER, topic TEXT, PRIMARY KEY(message_id, topic))"
            )
            connection.commit()
            connection.close()
            harvested = self.store.harvest_reference_packs(
                db,
                local_groups=groups,
                local_messages=messages,
                image_loader=image_loader,
                analyzer=analyzer,
            )
            self.assertGreaterEqual(harvested["stored"], 1)
            self.assertEqual(harvested["policy_version"], self.store.PACK_POLICY_VERSION)
            listed = self.store.collect_reference_list(db, harvest=False)
            row = listed["rows"][0]
            self.assertIn("대아티아이 차트 눌림목", row["message"])
            self.assertIn("20일선 위에서 거래량이 줄어든 캔들", row["message"])
            self.assertIn("차트는 상승 추세 눌림목", row["message"])
            self.assertNotIn(lecture, row["message"])
            connection = sqlite3.connect(db)
            policy = connection.execute(
                "SELECT policy_version, body FROM context_reference_packs"
            ).fetchone()
            connection.close()
            self.assertEqual(policy[0], self.store.PACK_POLICY_VISION)
            self.assertIn("20일선 위에서 거래량이 줄어든 캔들", policy[1])
            self.assertNotEqual(policy[1], lecture)

    def test_stale_v1_packs_rebuild_past_checkpoint(self):
        lecture = (
            "대아티아이 기술적 분석. 한줄 결론은 단기 조정은 나왔지만 아직 20일선 위에 있고 "
            "MACD도 살아 있어서 상승 추세 내 눌림목으로 볼 수 있어."
        )
        sent_at = 1755684000

        def groups():
            return [
                {
                    "chat_id": 417780809780519,
                    "title": "부자멘토멘티",
                    "chat_type": 1,
                    "members": 12,
                }
            ]

        def messages(chat_id: int):
            return [
                {
                    "log_id": 31,
                    "chat_id": chat_id,
                    "sender_name": "문승현",
                    "message": "사진",
                    "message_type": 2,
                    "sent_at": sent_at,
                    "author_id": 7,
                },
                {
                    "log_id": 32,
                    "chat_id": chat_id,
                    "sender_name": "문승현",
                    "message": lecture,
                    "message_type": 1,
                    "sent_at": sent_at + 5,
                    "author_id": 7,
                },
                {
                    "log_id": 33,
                    "chat_id": chat_id,
                    "sender_name": "문승현",
                    "message": "핵심은 20일선 위에서 거래량이 줄어도 추세가 안 깨졌다는 점이야.",
                    "message_type": 1,
                    "sent_at": sent_at + 10,
                    "author_id": 7,
                },
            ]

        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "context.sqlite3"
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE context_messages(id INTEGER PRIMARY KEY, source TEXT, chat TEXT, date TEXT, user_name TEXT, message TEXT, vector BLOB)"
            )
            connection.execute(
                "CREATE TABLE context_message_topics(message_id INTEGER, topic TEXT, PRIMARY KEY(message_id, topic))"
            )
            connection.commit()
            connection.close()
            harvested = self.store.harvest_reference_packs(
                db, local_groups=groups, local_messages=messages
            )
            self.assertGreaterEqual(harvested["stored"], 1)
            connection = sqlite3.connect(db)
            connection.execute(
                "UPDATE context_reference_packs SET policy_version = ?, body = ?",
                ("lecture-pack-v1", lecture),
            )
            connection.execute(
                "INSERT OR REPLACE INTO context_retrieval_meta(key, value) VALUES (?, ?)",
                ("reference_pack_checkpoint_local:417780809780519", "33"),
            )
            connection.commit()
            connection.close()
            rebuilt = self.store.harvest_reference_packs(
                db, local_groups=groups, local_messages=messages
            )
            self.assertGreaterEqual(rebuilt["stored"], 1)
            connection = sqlite3.connect(db)
            policy, body = connection.execute(
                "SELECT policy_version, body FROM context_reference_packs"
            ).fetchone()
            connection.close()
            self.assertEqual(policy, self.store.PACK_POLICY_VERSION)
            self.assertNotEqual(body, lecture)
            self.assertIn("종합:", body)

    def test_orphaned_image_dirs_are_cleaned(self):
        root = Path(tempfile.mkdtemp(prefix=self.store.TEMP_IMAGE_PREFIX))
        (root / "stale.jpg").write_bytes(b"x")
        old = time.time() - 7200
        os.utime(root, (old, old))
        removed = self.store._cleanup_orphaned_image_dirs(max_age_seconds=3600)
        self.assertGreaterEqual(removed, 1)
        self.assertFalse(root.exists())

    def test_download_does_not_create_tempdir_without_ids(self):
        before = set(Path(tempfile.gettempdir()).glob(self.store.TEMP_IMAGE_PREFIX + "*"))
        cluster = [
            {
                "message": "사진",
                "message_type": 2,
                "chat_id": 1,
                "log_id": 9,
                "author_id": None,
                "sender": "성린이형",
            }
        ]
        paths = self.store._download_cluster_images(cluster, bin_path=Path("/usr/bin/true"))
        after = set(Path(tempfile.gettempdir()).glob(self.store.TEMP_IMAGE_PREFIX + "*"))
        self.assertEqual(paths, [])
        self.assertEqual(after, before)

    def test_harvest_deadline_returns_without_hanging(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "context.sqlite3"
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE context_messages(id INTEGER PRIMARY KEY, source TEXT, chat TEXT, date TEXT, user_name TEXT, message TEXT, vector BLOB)"
            )
            connection.commit()
            connection.close()
            harvested = self.store.harvest_reference_packs(
                db, deadline_at=time.time() - 1
            )
            self.assertTrue(harvested["ok"])
            self.assertIn("complete", harvested)
            self.assertFalse(harvested["complete"])

    def test_encode_vector_blob_matches_values(self):
        text = "대아티아이 눌림목 설명"
        blob = self.store.encode_vector_blob(text)
        values = self.store.encode_vector_values(text)
        expected = struct.pack("<" + "f" * self.store.VECTOR_DIM, *values)
        self.assertEqual(blob, expected)



if __name__ == "__main__":
    unittest.main()
