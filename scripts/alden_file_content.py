"""Read document bytes bound to one exact incoming Kakao attachment.

Downloads use the existing local, read-only CLI. Parsing never follows links,
executes embedded code, or calls a model. The caller owns cancellation and the
bounded child-process adapter. Extracted text exists only for the current turn.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
import zlib
from html.parser import HTMLParser

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TEXT_BYTES = 16_000
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 256
MAX_DOCUMENT_PAGES = 50
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml", ".log",
                 ".py", ".js", ".ts", ".rs", ".toml", ".ini", ".sql", ".xml", ".html", ".htm"}
OFFICE_SUFFIXES = {".docx", ".xlsx", ".pptx"}
NATIVE_SUFFIXES = {".pdf", ".rtf", ".doc"}


class FileContentUnavailable(ValueError):
    """A bounded, non-secret reason suitable for a truthful clarification."""


class _TextBudget:
    """Cap output while accumulating, before shared strings can multiply."""
    def __init__(self):
        self.parts = []
        self.bytes = 0
        self.truncated = False

    def add(self, text):
        if not text:
            return True
        data = (("\n" if self.parts else "") + text).encode("utf-8")
        remaining = MAX_TEXT_BYTES - self.bytes
        if len(data) > remaining:
            self.parts.append(data[:remaining].decode("utf-8", "ignore"))
            self.truncated = True
            return False
        self.parts.append(data.decode("utf-8"))
        self.bytes += len(data)
        return True

    def text(self):
        return "".join(self.parts)


def _result(text: str, parser: str, *, truncated: bool = False, **scope) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise FileContentUnavailable("document_has_no_text")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in text):
        raise FileContentUnavailable("document_contains_binary_text")
    encoded = text.encode("utf-8")
    truncated = truncated or len(encoded) > MAX_TEXT_BYTES
    bounded = encoded[:MAX_TEXT_BYTES].decode("utf-8", "ignore").strip()
    if not bounded:
        raise FileContentUnavailable("document_has_no_text")
    return {"text": bounded, "parser": parser, "text_sha256": hashlib.sha256(bounded.encode()).hexdigest(),
            "truncated": truncated, "scope": scope}


def _xml(data: bytes):
    try:
        encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        source = data.decode(encoding)
    except UnicodeError as error:
        raise FileContentUnavailable("document_xml_encoding_unsupported") from error
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", source, re.I):
        raise FileContentUnavailable("document_xml_entities_unsupported")
    try:
        return ET.fromstring(source)
    except ET.ParseError as error:
        raise FileContentUnavailable("document_xml_invalid") from error


def _archive(data: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        infos = archive.infolist()
        if (len(infos) > MAX_ARCHIVE_ENTRIES or len({x.filename for x in infos}) != len(infos)
                or sum(x.file_size for x in infos) > MAX_ARCHIVE_BYTES
                or any(x.flag_bits & 1 or x.file_size > MAX_ARCHIVE_BYTES for x in infos)):
            raise FileContentUnavailable("document_archive_budget_or_shape")
        return archive
    except (zipfile.BadZipFile, OSError) as error:
        raise FileContentUnavailable("document_archive_invalid") from error


def _xml_text(root, names=("t",)) -> str:
    parts = []
    for x in root.iter():
        tag = x.tag.rsplit("}", 1)[-1]
        if tag in names: parts.append(x.text or "")
        elif tag == "tab": parts.append("\t")
        elif tag in {"br", "cr"}: parts.append("\n")
    return "".join(parts)


def _office_text(data: bytes, suffix: str) -> dict:
    try:
        with _archive(data) as z:
            names = set(z.namelist())
            if suffix == ".docx":
                if "word/document.xml" not in names:
                    raise FileContentUnavailable("document_format_mismatch")
                parts = ["word/document.xml"] + sorted(n for n in names if re.fullmatch(
                    r"word/(?:footnotes|endnotes|header\d+|footer\d+)\.xml", n))
                text = _TextBudget()
                for part in parts:
                    root = _xml(z.read(part))
                    for p in root.iter():
                        if p.tag.endswith("}p") and not text.add(_xml_text(p)):
                            break
                    if text.truncated: break
                return _result(text.text(), "docx-xml", truncated=text.truncated, parts=len(parts))
            if suffix == ".pptx":
                if not {"ppt/presentation.xml", "ppt/_rels/presentation.xml.rels"} <= names:
                    raise FileContentUnavailable("document_format_mismatch")
                relations = {x.get("Id"): x for x in _xml(z.read("ppt/_rels/presentation.xml.rels"))}
                slides = []
                for entry in _xml(z.read("ppt/presentation.xml")).iter():
                    if not entry.tag.endswith("}sldId"): continue
                    rid = entry.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                    relation = relations.get(rid)
                    if relation is None or relation.get("TargetMode") == "External":
                        raise FileContentUnavailable("document_slide_relation_invalid")
                    target = relation.get("Target", "")
                    target = target[1:] if target.startswith("/ppt/") else "ppt/" + target
                    if not re.fullmatch(r"ppt/slides/slide\d+\.xml", target) or target not in names:
                        raise FileContentUnavailable("document_slide_relation_invalid")
                    slides.append(target)
                if not slides:
                    raise FileContentUnavailable("document_format_mismatch")
                text = _TextBudget()
                has_content = False
                slides_read = 0
                for i, part in enumerate(slides[:MAX_DOCUMENT_PAGES], 1):
                    if not text.add(f"[Slide {i}]"): break
                    slides_read += 1
                    for p in _xml(z.read(part)).iter():
                        if not p.tag.endswith("}p"): continue
                        value = _xml_text(p)
                        has_content = has_content or bool(value.strip())
                        if not text.add(value): break
                    if text.truncated: break
                if not has_content:
                    raise FileContentUnavailable("document_has_no_text")
                return _result(text.text(), "pptx-xml", truncated=text.truncated or len(slides) > MAX_DOCUMENT_PAGES,
                               slides_read=slides_read, slides_total=len(slides), notes_read=False)
            if "xl/workbook.xml" not in names or "xl/_rels/workbook.xml.rels" not in names:
                raise FileContentUnavailable("document_format_mismatch")
            strings = []
            if "xl/sharedStrings.xml" in names:
                strings = [_xml_text(x) for x in _xml(z.read("xl/sharedStrings.xml"))]
            relations = {x.get("Id"): x for x in _xml(z.read("xl/_rels/workbook.xml.rels"))}
            sheets = [x for x in _xml(z.read("xl/workbook.xml")).iter() if x.tag.endswith("}sheet")]
            lines = _TextBudget()
            cells_read = 0
            sheets_read = 0
            for sheet in sheets[:MAX_DOCUMENT_PAGES]:
                rid = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                relation = relations.get(rid)
                if relation is None or relation.get("TargetMode") == "External":
                    raise FileContentUnavailable("document_sheet_relation_invalid")
                target = relation.get("Target", "")
                if target.startswith("/xl/"): target = target[1:]
                elif not target.startswith("/"): target = "xl/" + target
                if not re.fullmatch(r"xl/worksheets/[^/]+\.xml", target) or target not in names:
                    raise FileContentUnavailable("document_sheet_relation_invalid")
                if not lines.add("[Sheet " + str(sheet.get("name", "")) + "]"): break
                sheets_read += 1
                for cell in _xml(z.read(target)).iter():
                    if not cell.tag.endswith("}c"): continue
                    values = [x.text or "" for x in cell if x.tag.endswith("}v")]
                    value = values[0] if values else ""
                    if cell.get("t") == "s":
                        if not re.fullmatch(r"[0-9]{1,9}", value) or not 0 <= int(value) < len(strings):
                            raise FileContentUnavailable("document_shared_string_invalid")
                        value = strings[int(value)]
                    elif cell.get("t") == "inlineStr": value = _xml_text(cell)
                    formula = next((x.text or "" for x in cell if x.tag.endswith("}f")), "")
                    if formula: value = "[formula=" + formula + "; cached=" + value + "]"
                    if value:
                        cells_read += 1
                        if not lines.add(str(cell.get("r", "")) + "\t" + value): break
                if lines.truncated: break
            if not cells_read:
                raise FileContentUnavailable("document_has_no_text")
            return _result(lines.text(), "xlsx-xml", truncated=lines.truncated or len(sheets) > MAX_DOCUMENT_PAGES,
                           sheets_read=sheets_read, sheets_total=len(sheets), formulas_evaluated=False)
    except FileContentUnavailable:
        raise
    except (KeyError, RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error, ValueError) as error:
        raise FileContentUnavailable("document_archive_invalid") from error


_PDF_SCRIPT = r'''function run(args) {
    ObjC.import('PDFKit');
    var doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath($(args[0])));
    if (doc.isNil() || doc.isLocked) throw Error('document_pdf_unavailable');
    var total = Number(doc.pageCount), text = '', read = 0, clipped = false;
    if (total < 1) throw Error('document_pdf_empty');
    for (var i=0; i<Math.min(total,50); i++) {
        var page = doc.pageAtIndex(i), value = page.string;
        text += '[Page '+(i+1)+']\n'+(value.isNil() ? '' : ObjC.unwrap(value))+'\n';
        read = i+1;
        if (text.length > 32000) { text = text.slice(0,32000); clipped = true; break; }
    }
    return JSON.stringify({text:text, pages_read:read, pages_total:total,
                           truncated:clipped || read<total});
}'''


def extract_document(data: bytes, filename: str, *, run_process=None) -> dict:
    """Extract bounded text; native macOS readers are isolated child processes."""
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_FILE_BYTES:
        raise FileContentUnavailable("document_byte_budget")
    suffix = Path(filename).suffix.lower()
    if suffix in OFFICE_SUFFIXES:
        return _office_text(data, suffix)
    if suffix in NATIVE_SUFFIXES:
        if run_process is None:
            raise FileContentUnavailable("document_native_parser_unavailable")
        if suffix == ".pdf" and not data.startswith(b"%PDF-"):
            raise FileContentUnavailable("document_format_mismatch")
        if suffix == ".rtf" and not data.startswith(b"{\\rtf"):
            raise FileContentUnavailable("document_format_mismatch")
        if suffix == ".doc" and not data.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
            raise FileContentUnavailable("document_format_mismatch")
        with tempfile.TemporaryDirectory(prefix="alden-document-") as td:
            path = Path(td) / ("source" + suffix)
            with path.open("xb") as stream: stream.write(data)
            path.chmod(0o400)
            command = (["/usr/bin/osascript", "-l", "JavaScript", "-e", _PDF_SCRIPT, "--", str(path)]
                       if suffix == ".pdf" else ["/usr/bin/textutil", "-convert", "txt", "-stdout", str(path)])
            code, stdout, _ = run_process(command, timeout=8.0, stdout_cap=256_000, stderr_cap=4096)
            if code != 0: raise FileContentUnavailable("document_native_parser_failed")
        if suffix == ".pdf":
            try:
                result = json.loads(stdout)
                text = result["text"]
                # Page labels alone do not prove content (e.g. a scanned PDF).
                if not re.sub(r"\[Page \d+\]", "", text).strip():
                    raise FileContentUnavailable("document_pdf_has_no_text")
                return _result(text, "macos-pdfkit", truncated=result["truncated"],
                               pages_read=result["pages_read"], pages_total=result["pages_total"])
            except (KeyError, TypeError, ValueError) as error:
                raise FileContentUnavailable("document_pdf_text_unavailable") from error
        try: return _result(stdout.decode("utf-8"), "macos-textutil")
        except UnicodeError as error: raise FileContentUnavailable("document_encoding_unavailable") from error
    if suffix not in TEXT_SUFFIXES:
        raise FileContentUnavailable("document_format_unsupported")
    try:
        encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        text = data.decode(encoding)
    except UnicodeError as error:
        raise FileContentUnavailable("document_encoding_unavailable") from error
    if suffix in {".html", ".htm"}:
        class Text(HTMLParser):
            def __init__(self): super().__init__(convert_charrefs=True); self.parts=[]; self.hidden=0
            def handle_starttag(self, tag, attrs):
                if tag in {"script", "style"}: self.hidden += 1
            def handle_endtag(self, tag):
                if tag in {"script", "style"}: self.hidden = max(0, self.hidden - 1)
            def handle_data(self, value):
                if not self.hidden: self.parts.append(value)
        parser = Text(); parser.feed(text); text = "\n".join(parser.parts)
    return _result(text, "text-" + suffix[1:])


def read_attachment(provenance: dict, *, cli: Path, run_process) -> dict:
    """Resolve and read exact local-row bytes without trusting event file paths."""
    if (not isinstance(provenance, dict)
            or any(type(provenance.get(k)) is not int or not 0 < provenance[k] < 2**63-1
                   for k in ("chat_id", "log_id", "author_id"))
            or type(provenance.get("message_type")) is not int
            or provenance["message_type"] not in {3, 12, 16, 18, 26}
            or type(provenance.get("declared_size")) is not int
            or not 0 < provenance["declared_size"] <= MAX_FILE_BYTES
            or not isinstance(provenance.get("filename"), str)
            or not provenance["filename"] or "/" in provenance["filename"] or "\\" in provenance["filename"]
            or not isinstance(provenance.get("attachment_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", provenance["attachment_sha256"])):
        raise FileContentUnavailable("file_request_identity_invalid")
    suffix = Path(provenance["filename"]).suffix.lower()
    if suffix not in TEXT_SUFFIXES | OFFICE_SUFFIXES | NATIVE_SUFFIXES:
        raise FileContentUnavailable("document_format_unsupported")
    with tempfile.TemporaryDirectory(prefix="alden-file-") as td:
        directory = Path(td).resolve(); directory.chmod(0o700)
        command = [str(cli), "download", str(provenance["chat_id"]), str(provenance["log_id"]),
                   "--local", "--file", "--expected-author-id", str(provenance["author_id"]),
                   "--expected-attachment-sha256", provenance["attachment_sha256"],
                   "--output-dir", str(directory), "--json"]
        code, stdout, _ = run_process(command, timeout=30.0, stdout_cap=8192, stderr_cap=4096)
        if code != 0: raise FileContentUnavailable("file_download_unavailable")
        try: downloaded = json.loads(stdout)
        except (ValueError, TypeError) as error: raise FileContentUnavailable("file_download_manifest_invalid") from error
        path = directory / "file.download"
        if (not isinstance(downloaded, dict) or downloaded.get("status") != "ok" or downloaded.get("kind") != "file"
                or downloaded.get("path") != str(path) or downloaded.get("filename") != provenance["filename"]
                or any(type(downloaded.get(key)) is not int or downloaded[key] != provenance[key]
                       for key in ("chat_id", "log_id", "author_id", "message_type"))
                or downloaded.get("attachment_sha256") != provenance["attachment_sha256"]
                or type(downloaded.get("size")) is not int or downloaded["size"] != provenance["declared_size"]
                or not 0 < downloaded["size"] <= MAX_FILE_BYTES
                or not isinstance(downloaded.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", downloaded["sha256"])):
            raise FileContentUnavailable("file_download_identity_mismatch")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_nlink != 1
                    or before.st_mode & 0o077 or before.st_size != downloaded["size"]):
                raise FileContentUnavailable("file_download_not_private")
            with os.fdopen(fd, "rb", closefd=False) as stream: data = stream.read(MAX_FILE_BYTES + 1)
            after = os.fstat(fd)
            if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    or len(data) != downloaded["size"] or hashlib.sha256(data).hexdigest() != downloaded["sha256"]):
                raise FileContentUnavailable("file_download_bytes_mismatch")
        finally: os.close(fd)
        if suffix in NATIVE_SUFFIXES:
            result = extract_document(data, provenance["filename"], run_process=run_process)
        else:
            # Pure Python XML/ZIP parsing is also an owned cancellable child.
            # Freeze verified bytes again so path replacement cannot change its
            # input. The parent adapter kills this group on abort or timeout.
            frozen = directory / "parse.source"
            with frozen.open("xb") as stream: stream.write(data)
            frozen.chmod(0o400)
            command = [sys.executable, "-I", "-S", str(Path(__file__).resolve()),
                       str(frozen), "source" + suffix, downloaded["sha256"]]
            code, stdout, _ = run_process(command, timeout=8.0, stdout_cap=64_000, stderr_cap=4096)
            try: result = json.loads(stdout)
            except (ValueError, TypeError) as error:
                raise FileContentUnavailable("document_parser_unavailable") from error
            if code != 0:
                reason = result.get("unavailable") if isinstance(result, dict) else None
                if not isinstance(reason, str) or not re.fullmatch(r"[a-z0-9_]{1,80}", reason):
                    reason = "document_parser_unavailable"
                raise FileContentUnavailable(reason)
            if not isinstance(result, dict):
                raise FileContentUnavailable("document_parser_unavailable")
        context = {**result, "evidence_id": "file:" + downloaded["sha256"],
                "bytes_sha256": downloaded["sha256"], "filename": provenance["filename"],
                "chat_id": provenance["chat_id"], "log_id": provenance["log_id"],
                "author_id": provenance["author_id"], "attachment_sha256": provenance["attachment_sha256"]}
        if validated_context(context) is None:
            raise FileContentUnavailable("document_parser_manifest_invalid")
        return context


def validated_context(value: object) -> dict | None:
    keys = {"text", "parser", "text_sha256", "truncated", "scope", "evidence_id", "bytes_sha256",
            "filename", "chat_id", "log_id", "author_id", "attachment_sha256"}
    if not isinstance(value, dict) or set(value) != keys: return None
    try:
        text_bytes = value["text"].encode("utf-8")
        filename_bytes = value["filename"].encode("utf-8")
    except (AttributeError, UnicodeError):
        return None
    allowed_scope = {"parts", "slides_read", "slides_total", "notes_read", "sheets_read", "sheets_total",
                     "formulas_evaluated", "pages_read", "pages_total"}
    if (not value["text"].strip() or len(text_bytes) > MAX_TEXT_BYTES
            or any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in value["text"])
            or type(value["truncated"]) is not bool or not isinstance(value["scope"], dict)
            or set(value["scope"]) - allowed_scope
            or any(type(v) not in (int, bool) or (type(v) is int and not 0 <= v <= 100_000) for v in value["scope"].values())
            or not isinstance(value["parser"], str) or not re.fullmatch(r"[a-z0-9-]{1,32}", value["parser"])
            or not isinstance(value["filename"], str) or not value["filename"]
            or len(filename_bytes) > 512 or value["filename"] in {".", ".."}
            or any(unicodedata.category(c) == "Cc" for c in value["filename"])
            or "/" in value["filename"] or "\\" in value["filename"]
            or any(type(value[k]) is not int or not 0 < value[k] < 2**63-1 for k in ("chat_id", "log_id", "author_id"))
            or any(not isinstance(value[k], str) or not re.fullmatch(r"[0-9a-f]{64}", value[k])
                   for k in ("bytes_sha256", "text_sha256", "attachment_sha256"))
            or value["evidence_id"] != "file:" + value["bytes_sha256"]
            or value["text_sha256"] != hashlib.sha256(text_bytes).hexdigest()):
        return None
    return dict(value)


def _main():
    """Private pure-document subprocess; never fetches or executes content."""
    try:
        if len(sys.argv) != 4: raise FileContentUnavailable("document_parser_arguments")
        path, filename, digest = sys.argv[1:]
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            metadata = os.fstat(fd)
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                    or metadata.st_mode & 0o077 or metadata.st_nlink != 1
                    or not 0 < metadata.st_size <= MAX_FILE_BYTES):
                raise FileContentUnavailable("document_parser_source_invalid")
            with os.fdopen(fd, "rb", closefd=False) as stream: data = stream.read(MAX_FILE_BYTES + 1)
        finally: os.close(fd)
        if hashlib.sha256(data).hexdigest() != digest:
            raise FileContentUnavailable("document_parser_source_changed")
        result = extract_document(data, filename)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (FileContentUnavailable, OSError) as error:
        reason = str(error) if isinstance(error, FileContentUnavailable) else "document_parser_source_unavailable"
        print(json.dumps({"unavailable": reason}))
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
