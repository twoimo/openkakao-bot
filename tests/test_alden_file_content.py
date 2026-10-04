from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import struct
import tempfile
import time
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import alden_file_content as files
from tests import test_auto_reply_cli_runtime as cli_tests


def archive(parts):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, value in parts.items():
            z.writestr(name, value)
    return stream.getvalue()


def docx(text="올든 문서: 검수일 10월 8일, 수량 42개."):
    return archive({"word/document.xml": '<w:document xmlns:w="urn:w"><w:body><w:p><w:r><w:t>' + text + '</w:t></w:r></w:p></w:body></w:document>'})


def pptx(empty=False):
    # Presentation order deliberately differs from the slide filename order.
    return archive({
        "ppt/presentation.xml": '<p:presentation xmlns:p="urn:p" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId r:id="second"/><p:sldId r:id="first"/></p:sldIdLst></p:presentation>',
        "ppt/_rels/presentation.xml.rels": '<Relationships><Relationship Id="second" Target="slides/slide2.xml"/><Relationship Id="first" Target="slides/slide1.xml"/></Relationships>',
        "ppt/slides/slide1.xml": '<a:slide xmlns:a="urn:a"><a:p><a:r><a:t>' + ("" if empty else "끝") + '</a:t></a:r></a:p></a:slide>',
        "ppt/slides/slide2.xml": '<a:slide xmlns:a="urn:a"><a:p><a:r><a:t>' + ("" if empty else "먼저") + '</a:t></a:r></a:p></a:slide>',
    })


def xlsx(empty=False, external=False):
    return archive({
        "xl/workbook.xml": '<w:workbook xmlns:w="urn:x" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:sheets><w:sheet name="수량" r:id="one"/></w:sheets></w:workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships><Relationship Id="one" Target="worksheets/sheet1.xml"' + (' TargetMode="External"' if external else '') + '/></Relationships>',
        "xl/sharedStrings.xml": '<x:sst xmlns:x="urn:x"><x:si><x:r><x:t>올</x:t></x:r><x:r><x:t>든</x:t></x:r></x:si></x:sst>',
        "xl/worksheets/sheet1.xml": '<x:worksheet xmlns:x="urn:x"><x:sheetData><x:row>' + ('' if empty else '<x:c r="A1" t="s"><x:v>0</x:v></x:c><x:c r="B1"><x:f>21*2</x:f><x:v>42</x:v></x:c>') + '</x:row></x:sheetData></x:worksheet>',
    })


def pdf(text="Alden delivery date October 8, quantity 42."):
    content = f"BT /F1 12 Tf 40 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref = len(output)
    output += b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        output += f"{offset:010} 00000 n \n".encode()
    output += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(output)


def context(text="검수일은 10월 8일, 수량은 42개.", **changes):
    parsed = files.extract_document(text.encode(), "report.txt")
    value = {**parsed, "evidence_id": "file:" + "b" * 64, "bytes_sha256": "b" * 64,
             "filename": "report.pdf", "chat_id": 42, "log_id": 923, "author_id": 700,
             "attachment_sha256": "a" * 64}
    value.update(changes)
    return value


class DocumentExtractionTests(unittest.TestCase):
    def test_utf8_and_utf16_preserve_korean(self):
        for data in ("올든 42개".encode(), "올든 42개".encode("utf-16")):
            self.assertEqual(files.extract_document(data, "data.TXT")["text"], "올든 42개")

    def test_binary_unknown_and_oversized_are_unavailable(self):
        for data, name in ((b"abc\x00def", "a.txt"), (b"\xff", "a.txt"), (b"hello", "a.exe"),
                           (b"", "a.txt"), (b"a" * (files.MAX_FILE_BYTES + 1), "a.txt")):
            with self.subTest(name=name, size=len(data)), self.assertRaises(files.FileContentUnavailable):
                files.extract_document(data, name)

    def test_text_limit_is_utf8_boundary_and_disclosed(self):
        result = files.extract_document(("한" * 6000).encode(), "a.md")
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["text"].encode()), files.MAX_TEXT_BYTES)
        self.assertEqual(result["text_sha256"], hashlib.sha256(result["text"].encode()).hexdigest())

    def test_html_ignores_scripts_and_never_follows_links(self):
        result = files.extract_document(b'<p>42</p><script>fetch("https://bad.invalid")</script><style>hidden</style><a href="https://bad.invalid">data</a>', "a.html")
        self.assertEqual(result["text"], "42\ndata")

    def test_docx_preserves_text_run_adjacency_and_paragraphs(self):
        data = archive({"word/document.xml": '<w:document xmlns:w="urn:w"><w:p><w:r><w:t>올</w:t></w:r><w:r><w:t>든</w:t></w:r></w:p><w:p><w:r><w:t>42개</w:t></w:r></w:p></w:document>'})
        self.assertEqual(files.extract_document(data, "a.docx")["text"], "올든\n42개")

    def test_pptx_uses_presentation_order(self):
        result = files.extract_document(pptx(), "a.pptx")
        self.assertEqual(result["text"], "[Slide 1]\n먼저\n[Slide 2]\n끝")
        self.assertFalse(result["scope"]["notes_read"])

    def test_xlsx_reads_rich_strings_and_cached_formula_without_evaluation(self):
        result = files.extract_document(xlsx(), "a.xlsx")
        self.assertIn("A1\t올든", result["text"])
        self.assertIn("B1\t[formula=21*2; cached=42]", result["text"])
        self.assertFalse(result["scope"]["formulas_evaluated"])

    def test_empty_slides_and_sheets_do_not_count_labels_as_content(self):
        for data, name in ((pptx(True), "a.pptx"), (xlsx(True), "a.xlsx"), (docx(""), "a.docx")):
            with self.subTest(name=name), self.assertRaisesRegex(files.FileContentUnavailable, "has_no_text"):
                files.extract_document(data, name)

    def test_external_sheet_and_xml_entities_are_rejected(self):
        with self.assertRaisesRegex(files.FileContentUnavailable, "relation_invalid"):
            files.extract_document(xlsx(external=True), "a.xlsx")
        entity = '<!DOCTYPE x [<!ENTITY x "expanded">]><x>&x;</x>'
        for encoding in ("utf-8", "utf-16"):
            with self.subTest(encoding=encoding), self.assertRaisesRegex(files.FileContentUnavailable, "entities"):
                files.extract_document(archive({"word/document.xml": entity.encode(encoding)}), "a.docx")

    def test_duplicate_entries_and_archive_expansion_budget_are_rejected(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as z:
            z.writestr("word/document.xml", "first")
            with self.assertWarns(UserWarning):
                z.writestr("word/document.xml", "second")
        for data in (stream.getvalue(), archive({"word/document.xml": b"x" * (files.MAX_ARCHIVE_BYTES + 1)})):
            with self.assertRaisesRegex(files.FileContentUnavailable, "archive_budget"):
                files.extract_document(data, "a.docx")

    def test_native_parser_required_and_magic_bound_before_process(self):
        with self.assertRaisesRegex(files.FileContentUnavailable, "native_parser_unavailable"):
            files.extract_document(pdf(), "a.pdf")
        run = mock.Mock()
        with self.assertRaisesRegex(files.FileContentUnavailable, "format_mismatch"):
            files.extract_document(b"hello", "a.pdf", run_process=run)
        run.assert_not_called()

    def test_native_reader_receives_frozen_private_copy_then_cleans_it(self):
        seen = []
        def run(command, **limits):
            path = Path(command[-1]); seen.append(path)
            self.assertEqual(path.read_bytes(), pdf())
            self.assertEqual(path.stat().st_mode & 0o777, 0o400)
            self.assertEqual(limits["timeout"], 8.0)
            return 0, json.dumps({"text": "[Page 1]\n42", "pages_read": 1, "pages_total": 2, "truncated": True}).encode(), b""
        result = files.extract_document(pdf(), "a.pdf", run_process=run)
        self.assertTrue(result["truncated"])
        self.assertFalse(seen[0].exists())

    def test_corrupt_deflate_and_non_ascii_string_index_are_unavailable(self):
        data = bytearray(docx())
        name_length, extra_length = struct.unpack_from("<HH", data, 26)
        compressed = 30 + name_length + extra_length
        data[compressed] = (data[compressed] & ~6) | 6  # DEFLATE reserved block type.
        with self.assertRaises(files.FileContentUnavailable):
            files.extract_document(bytes(data), "a.docx")
        with zipfile.ZipFile(io.BytesIO(xlsx())) as z:
            parts = {name: z.read(name) for name in z.namelist()}
        parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(b"<x:v>0</x:v>", "<x:v>²</x:v>".encode())
        with self.assertRaisesRegex(files.FileContentUnavailable, "shared_string_invalid"):
            files.extract_document(archive(parts), "a.xlsx")

    def test_shared_string_output_is_bounded_before_final_result(self):
        with zipfile.ZipFile(io.BytesIO(xlsx())) as z:
            parts = {name: z.read(name) for name in z.namelist()}
        parts["xl/sharedStrings.xml"] = '<x:sst xmlns:x="urn:x"><x:si><x:t>' + "a" * 16_000 + '</x:t></x:si></x:sst>'
        parts["xl/worksheets/sheet1.xml"] = '<x:worksheet xmlns:x="urn:x"><x:row>' + '<x:c t="s"><x:v>0</x:v></x:c>' * 20_000 + '</x:row></x:worksheet>'
        original = files._result
        def bounded_result(text, *args, **kwargs):
            self.assertLessEqual(len(text.encode()), files.MAX_TEXT_BYTES)
            return original(text, *args, **kwargs)
        with mock.patch.object(files, "_result", side_effect=bounded_result):
            result = files.extract_document(archive(parts), "a.xlsx")
        self.assertTrue(result["truncated"])

    @unittest.skipUnless(sys.platform == "darwin", "native macOS PDFKit/textutil")
    def test_actual_native_pdfkit_and_rtf_extraction(self):
        def run(command, **limits):
            result = subprocess.run(command, capture_output=True, timeout=limits["timeout"])
            self.assertLessEqual(len(result.stdout), limits["stdout_cap"])
            self.assertLessEqual(len(result.stderr), limits["stderr_cap"])
            return result.returncode, result.stdout, result.stderr
        self.assertIn("October 8, quantity 42", files.extract_document(pdf(), "a.pdf", run_process=run)["text"])
        rtf = b"{\\rtf1\\ansi Alden \\u50732?\\u46304? 42}"
        self.assertIn("올든 42", files.extract_document(rtf, "a.rtf", run_process=run)["text"])


class ExactFileReadTests(unittest.TestCase):
    def provenance(self, data=b"42", **changes):
        result = {"chat_id": 42, "log_id": 923, "author_id": 700, "message_type": 26,
                  "filename": "a.txt", "declared_size": len(data), "attachment_sha256": "a" * 64}
        result.update(changes)
        return result

    def downloader(self, data=b"42", *, mutate=None, mode=0o600, symlink=None):
        def run(command, **limits):
            if command[0] != "/fake/cli":
                self.parse_command = command
                result = subprocess.run(command, capture_output=True, timeout=limits["timeout"])
                return result.returncode, result.stdout, result.stderr
            self.command = command
            path = Path(command[command.index("--output-dir") + 1]) / "file.download"
            self.path = path
            if symlink: path.symlink_to(symlink)
            else: path.write_bytes(data); path.chmod(mode)
            manifest = {"status": "ok", "kind": "file", "path": str(path), "filename": "a.txt",
                        "chat_id": 42, "log_id": 923, "author_id": 700, "message_type": 26,
                        "attachment_sha256": "a" * 64, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            if mutate: manifest.update(mutate)
            return 0, json.dumps(manifest).encode(), b""
        return run

    def test_download_binds_original_room_author_attachment_and_cleans_bytes(self):
        result = files.read_attachment(self.provenance(), cli=Path("/fake/cli"), run_process=self.downloader())
        self.assertEqual(result["text"], "42")
        self.assertEqual(files.validated_context(result), result)
        self.assertIn("--local", self.command)
        self.assertIn("--file", self.command)
        self.assertEqual(self.command[self.command.index("--expected-author-id") + 1], "700")
        self.assertEqual(self.command[self.command.index("--expected-attachment-sha256") + 1], "a" * 64)
        self.assertFalse(self.path.parent.exists())

    def test_manifest_identity_and_bytes_mismatches_never_become_text(self):
        for change in ({"chat_id": 43}, {"log_id": 924}, {"author_id": True}, {"message_type": 2},
                       {"attachment_sha256": "c" * 64}, {"filename": "b.txt"}, {"size": 3},
                       {"sha256": "d" * 64}, {"path": "/tmp/foreign"}, {"kind": "image"}):
            with self.subTest(change=change), self.assertRaises(files.FileContentUnavailable):
                files.read_attachment(self.provenance(), cli=Path("/fake/cli"), run_process=self.downloader(mutate=change))
            self.assertFalse(self.path.parent.exists())

    def test_symlink_and_public_file_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "foreign"; target.write_bytes(b"42")
            with self.assertRaises(OSError):
                files.read_attachment(self.provenance(), cli=Path("/fake/cli"), run_process=self.downloader(symlink=target))
            self.assertEqual(target.read_bytes(), b"42")
        with self.assertRaisesRegex(files.FileContentUnavailable, "not_private"):
            files.read_attachment(self.provenance(), cli=Path("/fake/cli"), run_process=self.downloader(mode=0o644))

    def test_invalid_or_oversized_request_never_starts_download(self):
        run = mock.Mock()
        for change in ({"chat_id": True}, {"author_id": -1}, {"declared_size": 0},
                       {"declared_size": files.MAX_FILE_BYTES + 1}, {"attachment_sha256": "wrong"}):
            with self.subTest(change=change), self.assertRaises(files.FileContentUnavailable):
                files.read_attachment(self.provenance(**change), cli=Path("/fake/cli"), run_process=run)
        run.assert_not_called()

    def test_original_filename_never_enters_parser_argv(self):
        private_name = "CONFIDENTIAL-client-medical-record.txt"
        result = files.read_attachment(self.provenance(filename=private_name), cli=Path("/fake/cli"),
                                       run_process=self.downloader(mutate={"filename": private_name}))
        self.assertEqual(result["filename"], private_name)
        self.assertEqual(self.parse_command[5], "source.txt")
        self.assertNotIn(private_name, "\0".join(self.parse_command))
        run = mock.Mock()
        with self.assertRaisesRegex(files.FileContentUnavailable, "format_unsupported"):
            files.read_attachment(self.provenance(filename="private.exe"), cli=Path("/fake/cli"), run_process=run)
        run.assert_not_called()

    def test_invalid_context_and_surrogate_fail_closed(self):
        value = context()
        for change in ({"chat_id": True}, {"text_sha256": "c" * 64}, {"text": "\ud800"},
                       {"filename": "\ud800"}, {"filename": "a\n.txt"}, {"scope": {"instruction": "send"}},
                       {"evidence_id": "file:" + "d" * 64}, {"text": "abc\x00"}):
            with self.subTest(change=change):
                self.assertIsNone(files.validated_context({**value, **change}))

    def test_parser_is_a_bounded_owned_child_and_timeout_cleans_both_copies(self):
        download = self.downloader()
        def run(command, **limits):
            if command[0] == "/fake/cli": return download(command, **limits)
            self.assertEqual(command[1:3], ["-I", "-S"])
            self.assertEqual(limits["timeout"], 8.0)
            self.assertEqual(Path(command[4]).read_bytes(), b"42")
            raise subprocess.TimeoutExpired(command, limits["timeout"])
        with self.assertRaises(subprocess.TimeoutExpired):
            files.read_attachment(self.provenance(), cli=Path("/fake/cli"), run_process=run)
        self.assertFalse(self.path.parent.exists())


class FileWorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.worker = cli_tests.AutoReplyCliRuntimeTests._load_auto_reply_module("file_content_worker_test")
        self.event = cli_tests.AutoReplyCliRuntimeTests._file_unavailable_event(self.worker, 923, int(time.time()))
        self.content = context(attachment_sha256=self.event["file_provenance"]["attachment_sha256"])

    def test_opt_in_and_privacy_and_turn_hold_prevent_fetch(self):
        for opt_in, privacy, hold in (("0", True, None), ("1", False, None), ("1", True, "cancelled")):
            with self.subTest(opt_in=opt_in, privacy=privacy, hold=hold), mock.patch.dict(os.environ, {
                    "OPENKAKAO_ALLOW_LINK_FETCH": opt_in, self.worker.DB_MODE_ENV: "database_authoritative"}), \
                    mock.patch.object(self.worker, "_reply_turn_hold_reason", return_value=hold), \
                    mock.patch.object(self.worker, "privacy_attestation_current", return_value=privacy), \
                    mock.patch.object(files, "read_attachment") as fetch, \
                    mock.patch.object(self.worker, "analyze_event") as analyze:
                self.worker.analyze_file_attachment(self.event)
                fetch.assert_not_called(); analyze.assert_not_called()

    def test_unavailable_falls_back_without_model_and_success_passes_exact_text(self):
        with mock.patch.dict(os.environ, {"OPENKAKAO_ALLOW_LINK_FETCH": "1"}), \
                mock.patch.object(self.worker, "_reply_turn_hold_reason", return_value=None), \
                mock.patch.object(files, "read_attachment", side_effect=files.FileContentUnavailable("expired")), \
                mock.patch.object(self.worker, "analyze_event") as analyze:
            result = self.worker.analyze_file_attachment(self.event)
            self.assertFalse(result["provenance"]["file_content_available"])
            self.assertEqual(result["provenance"]["file_content_reason"], "expired")
            analyze.assert_not_called()
        with mock.patch.dict(os.environ, {"OPENKAKAO_ALLOW_LINK_FETCH": "1"}), \
                mock.patch.object(self.worker, "_reply_turn_hold_reason", return_value=None), \
                mock.patch.object(files, "read_attachment", return_value=self.content) as fetch, \
                mock.patch.object(self.worker, "analyze_event", return_value={"provenance": {}}) as analyze:
            result = self.worker.analyze_file_attachment(self.event)
            self.assertEqual(fetch.call_args.args[0], self.event["file_provenance"])
            self.assertEqual(analyze.call_args.kwargs["file_context"], self.content)
            self.assertTrue(result["provenance"]["file_content_available"])

    def test_cancellation_after_download_never_reaches_model(self):
        with mock.patch.dict(os.environ, {"OPENKAKAO_ALLOW_LINK_FETCH": "1"}), \
                mock.patch.object(self.worker, "_reply_turn_hold_reason", return_value=None), \
                mock.patch.object(files, "read_attachment", return_value=self.content), \
                mock.patch.object(self.worker, "_raise_if_job_aborted", side_effect=self.worker.AldenCancelled("cancelled")), \
                mock.patch.object(self.worker, "analyze_event") as analyze:
            with self.assertRaises(self.worker.AldenCancelled):
                self.worker.analyze_file_attachment(self.event)
            analyze.assert_not_called()

    def test_wrong_room_author_log_or_attachment_blocks_before_image_or_model(self):
        for change in ({"chat_id": 43}, {"log_id": 924}, {"author_id": 701}, {"attachment_sha256": "f" * 64}):
            with self.subTest(change=change), mock.patch.object(self.worker, "_reply_turn_hold_reason", return_value=None), \
                    mock.patch.object(self.worker, "capture_visible_image") as image, \
                    mock.patch.object(self.worker, "generate_reply") as generate:
                result = self.worker.analyze_event(self.event, file_context={**self.content, **change})
                self.assertEqual(result["reason"], "invalid_file_content")
                image.assert_not_called(); generate.assert_not_called()

    def test_document_survives_prompt_fit_and_actual_trim_is_disclosed(self):
        value = {"incoming_message": "요약해줘", "context": ["aux" * 1000] * 20,
                 "file_evidence": context("한" * 4500)}
        fitted = self.worker._fit_prompt_to_budget(value, 64 * 1024)
        self.assertEqual(fitted["file_evidence"]["text"], value["file_evidence"]["text"])
        small = self.worker._fit_prompt_to_budget(value, 3000)
        self.assertLess(len(small["file_evidence"]["text"]), len(value["file_evidence"]["text"]))
        self.assertTrue(small["file_evidence"]["truncated"])
        self.assertIsNotNone(files.validated_context(small["file_evidence"]))
        self.assertIsNotNone(self.worker._encode_json_bounded(small, 3000))
        self.assertFalse(value["file_evidence"]["truncated"])

    def test_same_placeholder_is_not_duplicate_for_different_file_bytes(self):
        prior = {"message": "[파일]", "status": "sent", "evidence_json": json.dumps({"evidence_ids": ["file:" + "b" * 64]})}
        check = self.worker._prior_is_exact_duplicate
        self.assertTrue(check(prior, "[파일]", attachment="file", media_bundle_digest="", file_digest="b" * 64))
        self.assertFalse(check(prior, "[파일]", attachment="file", media_bundle_digest="", file_digest="c" * 64))
        self.assertFalse(check(prior, "[파일]", attachment="file", media_bundle_digest=""))

    def test_generation_keeps_trusted_file_rules_and_requires_file_citation(self):
        worker = self.worker
        response = {"should_reply": True, "reply": "검수일은 10월 8일이고 수량은 42개예요.",
                    "category": "information", "reason": "file_summary", "evidence_ids": [self.content["evidence_id"]]}
        systems = []
        def generate(model, system, payload, *args, **kwargs):
            systems.append(system)
            prompt = json.loads(payload)
            self.assertEqual(prompt["file_evidence"]["text"], self.content["text"])
            self.assertNotIn(self.content["text"], "\0".join(args[0] if args else kwargs["command"]))
            return 0, json.dumps(response, ensure_ascii=False).encode(), b""
        with mock.patch.object(worker, "_load_dream_rsi_checkpoint_metadata", return_value=None), \
                mock.patch.object(worker, "record_learned_style_tells"), \
                mock.patch.object(worker, "learned_style_tell_avoids", return_value=[]), \
                mock.patch.object(worker, "runner_is_trusted", return_value=True), \
                mock.patch.object(worker, "privacy_attestation_current", return_value=True), \
                mock.patch.object(worker, "_queue_expected_chat_id", return_value=42), \
                mock.patch.object(worker, "REPLY_RUNNER_KIND", "opencodex"), \
                mock.patch.object(worker, "_publish_model_status"), \
                mock.patch.object(worker, "_active_reply_model", return_value="mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit"), \
                mock.patch.object(worker, "_acquire_model_call_slot", return_value={"allowed": True, "lease_token": "test", "retry_at": time.time() + 60}), \
                mock.patch.object(worker, "_finish_model_call_success", return_value=True), \
                mock.patch("auto_reply_knowledge_graph.retrieve_knowledge_bundle", return_value={}), \
                mock.patch.object(worker, "_run_generation_candidate", side_effect=generate), \
                mock.patch.object(worker, "_halve_prompt_lists", wraps=worker._halve_prompt_lists):
            result = worker.generate_reply("이거 요약해줘", [], [], [], [], file_context=self.content)
            self.assertTrue(result["should_reply"])
            self.assertEqual(result["evidence_ids"], [self.content["evidence_id"]])
            self.assertEqual(result["file_text_sha256"], self.content["text_sha256"])
            self.assertIn("only part of the document", systems[0])
            response["evidence_ids"] = ["reaction:register"]
            result = worker.generate_reply("이거 요약해줘", [], [], [], [], file_context=self.content)
            self.assertIn(self.content["evidence_id"], result["evidence_ids"])

    def test_document_turn_does_not_capture_unrelated_pixels_or_require_old_context(self):
        worker = self.worker
        bundle = {"context": [], "styles": [], "prior_decisions": [], "style_profile": None,
                  "recipient_style_profile": None, "response_time": cli_tests.AutoReplyCliRuntimeTests._timing_stats(worker)}
        answer = {"should_reply": True, "reply": "검수일은 10월 8일이고 수량은 42개예요.",
                  "category": "information", "reason": "file_summary", "evidence_ids": [self.content["evidence_id"]]}
        with mock.patch.object(worker, "_reply_turn_hold_reason", return_value=None), \
                mock.patch.object(worker, "capture_visible_image") as image, \
                mock.patch.object(worker, "_recover_local_media_bundle") as recover, \
                mock.patch.object(worker, "record_learned_style_tells"), \
                mock.patch.object(worker, "_partner_streak_hold_reason", return_value=None), \
                mock.patch.object(worker, "fetch_link_previews", return_value=[]), \
                mock.patch.object(worker, "run_context_reply_bundle", return_value=bundle), \
                mock.patch.object(worker, "generate_reply", return_value=answer) as generate:
            result = worker.analyze_event(self.event, file_context=self.content)
        self.assertEqual(result["decision"], "reply", result)
        self.assertEqual(result["evidence_ids"], [self.content["evidence_id"]])
        self.assertEqual(generate.call_args.kwargs["file_context"], self.content)
        image.assert_not_called(); recover.assert_not_called()

    def test_followup_requires_exact_immediately_previous_same_author_file(self):
        worker = self.worker
        source = {key: self.event[key] for key in ("chat_id", "log_id", "author_id", "author_nickname", "message_type", "sent_at", "file_provenance")}
        source.update(message="", is_self=False, attachment=False)
        event = cli_tests.AutoReplyCliRuntimeTests._burst_event(worker, 924, "이거 요약해줘", self.event["sent_at"] + 1, recent=[source])
        event = worker._prepare_burst_event(event)
        reference = worker._recent_unavailable_file_followup(event)
        self.assertIsNotNone(reference)
        with mock.patch.dict(os.environ, {"OPENKAKAO_ALLOW_LINK_FETCH": "1"}), \
                mock.patch.object(worker, "_reply_turn_hold_reason", return_value=None), \
                mock.patch.object(files, "read_attachment", return_value=self.content) as fetch, \
                mock.patch.object(worker, "analyze_event", return_value={"provenance": {}}) as analyze:
            worker.analyze_file_attachment(event, reference)
            self.assertEqual(fetch.call_args.args[0]["log_id"], 923)
            self.assertEqual(analyze.call_args.args[0]["log_id"], 924)
            fetch.reset_mock()
            forged = copy.deepcopy(reference); forged["provenance"]["author_id"] = 701
            result = worker.analyze_file_attachment(event, forged)
            self.assertEqual(result["reason"], "invalid_file_provenance")
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
