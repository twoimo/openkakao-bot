#!/usr/bin/env python3
"""Alden's local OSK adapter. No hooks, MCP registration, Git sync or LLM calls.

OSK v4.1.2 is pinned with a documented adjacency performance patch. Its public
write API validates ordinary notes; graph/contract APIs read them back. Imported
Kakao records live in private non-node _sources with their original roles.
Ordinary notes use genuine derived-from source coordinates, separate from ERE
semantic relationships and from the human approval ledger.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

VERSION = "v4.1.2"
COMMIT = "9bbf08febc5a1fb2af068006735ed79cbdb71178"
ARCHIVE_HASH = "74feda64efb68c819a45746359de4158e426f87d74f682602f23fb6d46c9d4e3"
MAX_MUTATIONS = 32  # Source writes + moves share one budget; at most 9 hub writes extra.
MAX_BODY = 12000
_ENGINE = None
_ROOT = None
GROUPS = {
    'kakao': ('Alden 카카오톡', '00_Scope/Alden/Alden 카카오톡', '카카오톡'),
    'rooms': ('Alden 대화방', '00_Scope/Alden/Alden 카카오톡/Alden 대화방', '대화방'),
    'people': ('Alden 카카오톡 인물', '00_Person/Alden 카카오톡 인물', '인물'),
    'topics': ('Alden 대화 주제', '00_Scope/Alden/Alden 카카오톡/Alden 대화 주제', '주제'),
}

def _group(node: dict) -> str:
    kind=str(node.get('category','')).casefold()
    if kind in ('person','people','인물','사람','대화 상대','대화상대','화자'): return 'people'
    if kind in ('room','chat','chatroom','대화방'): return 'rooms'
    return 'topics'

def _organize_vault(home: Path, checkpoint: dict, contract, graph, write, budget: int) -> int:
    """User-requested Kakao/person structure through real OSK mutation APIs.

    Existing source IDs and OSK IDs survive moves. Edited/protected notes are
    held; no name-based identity merge or human approval ledger is fabricated.
    """
    groups=checkpoint.setdefault('groups',{})
    for key,(title,space,label) in GROUPS.items():
        path=home/'vault'/space/(title+'.md')
        if not path.exists():
            body='카카오톡 대화에서 얻은 '+label+' 지식을 모으는 군집입니다.'
            try: write.create_node(title,body,body,'agent',space=space)
            except write.WriteError as error:
                # This exact new Person cluster was explicitly requested by the
                # human. Honor OSK's first notification/retry formation gate.
                if key=='people' and '새 군집 신설' in str(error):
                    write.create_node(title,body,body,'agent',space=space)
                else: raise
        note=contract.parse(path)
        if contract.validate(note): raise RuntimeError('osk_group_contract_invalid')
        groups.setdefault(key,{'title':title,'space':space,'osk_id':note.id,'written_hash':digest(path.read_bytes())})
    moved=0
    for key,item in sorted(checkpoint['managed'].items()):
        if not item.get('active') or item.get('held'): continue
        old_space=item.get('space','00_Scope/Alden');new_space=GROUPS[_group(item['source'])][1]
        if old_space==new_space or moved>=budget: continue
        old_path=home/'vault'/old_space/(item['title']+'.md')
        if not old_path.is_file() or digest(old_path.read_bytes())!=item['written_hash']: continue
        try: write.move_node(item['title'],new_space)
        except write.WriteError:
            item['held']=True;continue
        path=home/'vault'/new_space/(item['title']+'.md');note=contract.parse(path)
        if contract.validate(note) or note.id!=item['osk_id']: raise RuntimeError('osk_move_identity_changed')
        item.update(space=new_space,written_hash=digest(path.read_bytes()));moved+=1
        _save(home/'sync.json',checkpoint)
    for key,item in groups.items():
        path=home/'vault'/item['space']/(item['title']+'.md')
        if digest(path.read_bytes())!=item['written_hash']: continue
        targets=[GROUPS[k][0] for k in ('rooms','people','topics')] if key=='kakao' else [m['title'] for m in checkpoint['managed'].values() if m.get('active') and _group(m['source'])==key]
        body='카카오톡 '+GROUPS[key][2]+' 지식입니다.\n\n'+'\n'.join('- [['+title+']]' for title in targets)
        note=contract.parse(path)
        if note.body.strip()!=body.strip():
            try: write.update_node(item['title'],body=body,expect_hash=item['written_hash'])
            except write.WriteError: continue
            item['written_hash']=digest(path.read_bytes())

    return moved

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _revision(node: dict, body: str) -> str:
    semantic = {key: value for key, value in node.items() if key != "updated_at"}
    return digest(json.dumps(semantic, ensure_ascii=False, sort_keys=True).encode() + body.encode())


def _safe_directory(path: Path) -> Path:
    path = path.absolute()
    for parent in reversed([path, *path.parents]):
        if parent.is_symlink():
            raise RuntimeError("osk_symlink_path")
        parent.mkdir(mode=0o700, exist_ok=True)
        if not parent.is_dir():
            raise RuntimeError("osk_directory_invalid")
    return path


def _read_json(path: Path, fallback: dict | None = None) -> dict:
    if path.is_symlink():
        raise RuntimeError("osk_symlink_file")
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise RuntimeError("osk_state_too_large")
        result = json.loads(path.read_text())
        if not isinstance(result, dict):
            raise RuntimeError("osk_state_invalid")
        return result
    except FileNotFoundError:
        return {} if fallback is None else fallback


def _save(path: Path, value: dict) -> None:
    if path.is_symlink():
        raise RuntimeError("osk_symlink_file")
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".osk-save-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _home(state_root: Path) -> Path:
    return state_root / "knowledge" / "osk"


def _load_engine(state_root: Path):
    global _ENGINE, _ROOT
    home = _safe_directory(_home(state_root))
    if _ENGINE is not None:
        if _ROOT != home:
            raise RuntimeError("osk_process_vault_changed")
        return _ENGINE
    vendor = Path(__file__).parent / "vendor"
    archive = vendor / f"osk-{VERSION}.zip"
    manifest = _read_json(vendor / f"osk-{VERSION}.json")
    if archive.is_symlink() or digest(archive.read_bytes()) != ARCHIVE_HASH:
        raise RuntimeError("osk_archive_integrity")
    if manifest.get("archive_sha256") != ARCHIVE_HASH or manifest.get("commit") != COMMIT:
        raise RuntimeError("osk_manifest_integrity")
    destination = _safe_directory(home / "engines") / ARCHIVE_HASH[:16]
    if destination.is_symlink():
        raise RuntimeError("osk_symlink_engine")
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(dir=destination.parent, prefix=".engine-"))
        try:
            with zipfile.ZipFile(archive) as bundle:
                if set(bundle.namelist()) != set(manifest["files"]):
                    raise RuntimeError("osk_archive_manifest_mismatch")
                for name, expected in manifest["files"].items():
                    relative = Path(name)
                    if relative.is_absolute() or ".." in relative.parts:
                        raise RuntimeError("osk_archive_path")
                    data = bundle.read(name)
                    if digest(data) != expected:
                        raise RuntimeError("osk_archive_member_integrity")
                    target = stage / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            stage.rename(destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    for name, expected in manifest["files"].items():
        target = destination / name
        if any(p.is_symlink() for p in [target, *target.parents]) or digest(target.read_bytes()) != expected:
            raise RuntimeError("osk_engine_integrity")
    vault = _safe_directory(home / "vault")
    for name in ("00_Scope", "00_Domain", "00_Person", "00_Scope/Alden"):
        _safe_directory(vault / name)
    governance = _safe_directory(vault / "_governance")
    for name in ("Constitution.md", "Bylaws.md", "Mechanism.md", "Workbench-Contract.md"):
        target = governance / name
        if target.is_symlink():
            raise RuntimeError("osk_symlink_governance")
        if not target.exists():
            target.write_bytes((destination / "_governance" / name).read_bytes())
    os.environ["OSK_VAULT_ROOT"] = str(vault)
    sys.path[:0] = [str(destination / "deps"), str(destination / "_governance/_engine")]
    from osk import contract, graph, secrets, write
    _ENGINE = (contract, graph, secrets, write)
    _ROOT = home
    return _ENGINE


def _aborted(state_root: Path) -> bool:
    from alden_abort import read_abort_state
    return read_abort_state(state_root / "alden-abort.json").latched


def _clean(text, secrets, limit: int = MAX_BODY) -> str:
    # Never let source text create arbitrary wiki references or Markdown sections.
    value = secrets.filter_text(str(text or ""))[0]
    return value.replace("[[", "［［").replace("]]", "］］")[:limit]


def _title(node: dict, secrets) -> str:
    label = _clean(node.get("label"), secrets, 48)
    label = re.sub(r'[<>:"/\\|?*#\]\[\x00-\x1f]', "·", label).strip(" .")
    return f"{label or '기억'} · {digest(str(node['id']).encode())[:10]}"


def _body(node: dict, links: list[dict], titles: dict[str, str], secrets) -> str:
    lines = ["## 올든이 관리하는 대화 지식", "", f"출처 ID: `{_clean(node['id'], secrets, 192)}`",
             f"유형: {_clean(node.get('category'), secrets, 96)}", "",
             _clean(node.get("description"), secrets, 1800)]
    lines.extend("- " + _clean(fact, secrets, 600).replace("\n", " ") for fact in node.get("facts", [])[:12])
    evidence = node.get("evidence") or {}
    lines += ["", "## 출처", json.dumps(evidence, ensure_ascii=False, sort_keys=True)]
    if links:
        lines += ["", "## 대화에서 발견한 관계"]
    for edge in links[:24]:
        target = edge["target"] if edge["source"] == node["id"] else edge["source"]
        if target in titles:
            lines.append(f"- {_clean(edge.get('relation'), secrets, 96)}: [[{titles[target]}]]")
    return secrets.filter_text("\n".join(lines))[0][:MAX_BODY]


def _source_signature(state_root: Path) -> str:
    pointer=_read_json(state_root/'knowledge/corpus/current.json')
    if pointer.get('schema_version')!=1:return ''
    # Source and derivation version both invalidate the materialized graph.
    files=['alden_osk.py','alden_osk_sources.py','alden_corpus_topics.py','auto_reply_reference_store.py','auto_reply_knowledge_graph.py']
    code=b''.join(digest(Path(__file__).with_name(name).read_bytes()).encode() for name in files)
    return digest(json.dumps(pointer,sort_keys=True).encode()+code)


def synchronize(state_root: Path, source: dict | None = None) -> dict:
    """One bounded, replayable tick. Source deletion retracts, never purges notes."""
    home = _safe_directory(_home(state_root))
    lock_fd = os.open(home / "sync.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"ok": True, "state": "busy", "engine": VERSION}
        if _aborted(state_root):
            return {"ok": False, "state": "paused", "engine": VERSION}
        contract, graph, secrets, write = _load_engine(state_root)
        checkpoint_path = home / "sync.json"
        checkpoint = _read_json(checkpoint_path, {"managed": {}, "generation": 0})
        signature = _source_signature(state_root) if source is None else ''
        if (signature and checkpoint.get('source_signature')==signature and checkpoint.get('pending')==0
            and checkpoint.get('layout_pending',0)==0 and checkpoint.get('conflicts')==0 and not checkpoint.get('stale')):
            unchanged=all(not (home/'vault'/item.get('space','00_Scope/Alden')/(item['title']+'.md')).is_symlink()
                          and (home/'vault'/item.get('space','00_Scope/Alden')/(item['title']+'.md')).is_file()
                          and digest((home/'vault'/item.get('space','00_Scope/Alden')/(item['title']+'.md')).read_bytes())==item.get('written_hash')
                          for item in checkpoint.get('managed',{}).values() if item.get('active'))
            if unchanged:return {'ok':True,'state':'unchanged','changed':0,'pending':0,'conflicts':0,'stale':False,'source_signature':signature}
        if source is None:
            from auto_reply_knowledge_graph import collect_knowledge_graph
            source = collect_knowledge_graph(state_root / "context.sqlite3", state_root=state_root,wait_for_reindex=True,
                                             force_reindex=bool(signature and checkpoint.get('source_signature')!=signature))
        if source.get("ok") is not True:
            raise RuntimeError("osk_source_unavailable")
        from alden_osk_sources import ground_graph
        source = ground_graph(state_root, source, secrets.filter_text)
        source = json.loads(secrets.filter_text(json.dumps(source, ensure_ascii=False))[0])
        managed = checkpoint["managed"]
        nodes = {str(n["id"]): n for n in source.get("nodes", []) if not str(n["id"]).startswith(("message:", "msg:"))}
        titles = {key: managed.get(key, {}).get("title") or _title(node, secrets) for key, node in nodes.items()}
        # Build incident lists once, retaining input order and one copy of a
        # self-edge. This is local to this tick; edits are re-read on every tick.
        incident = {key: [] for key in nodes}
        for edge in source.get("edges", []):
            left, right = edge.get("source"), edge.get("target")
            if isinstance(left, str) and left in incident:
                incident[left].append(edge)
            if right != left and isinstance(right, str) and right in incident:
                incident[right].append(edge)
        revisions = {}
        hub = home / "vault/00_Scope/Alden/Alden.md"
        if not hub.exists():
            write.create_node("Alden", "올든의 대화에서 얻은 지식과 그 출처", "올든이 관리하는 로컬 대화 지식입니다.", "agent", space="00_Scope/Alden")
        changed = 0
        conflicts = 0
        for key, node in sorted(nodes.items()):
            if _aborted(state_root):
                break
            old = managed.get(key)
            title = old["title"] if old else titles[key]
            titles[key] = title
            body = _body(node, incident[key], titles, secrets)
            revision = _revision(node, body)
            revisions[key] = revision
            space=old.get('space','00_Scope/Alden') if old else '00_Scope/Alden'
            path = home / 'vault' / space / f"{title}.md"
            if old is None and not path.exists():
                found=graph.Index().nodes.get(title)
                if found:
                    path=found[0];space=str(path.parent.relative_to(home/'vault'))
            if path.is_symlink():
                raise RuntimeError("osk_symlink_note")
            current = path.read_bytes() if path.exists() else None
            current_hash = digest(current) if current is not None else ""
            if old and current_hash != old.get("written_hash"):
                conflicts += 1
                old["held"] = True
                continue  # Human edit, missing file, or protection: preserve and report.
            if old and old.get("revision") == revision:
                old["active"] = True
                old["held"] = False
                old["source"] = node
                continue
            if changed >= MAX_MUTATIONS:
                continue
            summary = _clean(node.get("description") or node.get("label"), secrets, 78).replace("\n", " ").replace("［［", "") or "대화에서 찾은 지식"
            try:
                if current is None:
                    refs = node.get('raw_sources') or []
                    result = write.create_node(title, summary, body, "agent", space=space, edges={'derived-from':refs} if refs else None)
                elif old:
                    history = _safe_directory(home / "history" / digest(key.encode())[:16])
                    backup = history / f"{current_hash}.md"
                    if not backup.exists():
                        backup.write_bytes(current)
                    refs = node.get('raw_sources') or []
                    previous_refs = contract.parse(path).meta.get('derived-from') or []
                    edge_changes = {'add_edges':{'derived-from':[ref for ref in refs if ref not in previous_refs]},
                                    'remove_edges':{'derived-from':[ref for ref in previous_refs if ref not in refs]}} if refs else {}
                    result = write.update_node(title, body=body, expect_hash=current_hash, summary=summary,
                                               **edge_changes)
                else:
                    # Recover an interrupted create only after exact persisted readback.
                    recovered = contract.parse(path)
                    if recovered.body.strip() != body.strip() or recovered.meta.get("drafter") != "agent":
                        conflicts += 1
                        continue
                    result = {"id": recovered.id, "new_hash": current_hash}
            except write.WriteError:
                conflicts += 1
                continue
            parsed = contract.parse(path)
            if contract.validate(parsed):
                raise RuntimeError("osk_written_contract_invalid")
            managed[key] = {"title": title, "osk_id": parsed.id, "written_hash": digest(path.read_bytes()),
                            "revision": revision, "active": True, "held": False, "source": node, "space": space}
            changed += 1
            _save(checkpoint_path, checkpoint)
        # Do not retract while the source reindex is pending or cancellation occurs.
        if not source.get("stale") and not _aborted(state_root):
            for key, item in managed.items():
                if key not in nodes:
                    item["active"] = False
        active = {key for key, item in managed.items() if item.get("active")}
        moved = 0
        if not _aborted(state_root):
            moved = _organize_vault(home,checkpoint,contract,graph,write,MAX_MUTATIONS-changed)
        layout_pending=sum(1 for m in managed.values() if m.get('active') and not m.get('held') and m.get('space','00_Scope/Alden')!=GROUPS[_group(m['source'])][1])
        checkpoint.update({"engine": VERSION, "commit": COMMIT, "generation": checkpoint.get("generation", 0) + 1,
                           "synced_at": int(time.time()), "source_indexed_at": source.get("indexed_at", 0),"layout_pending":layout_pending,
                           "stale": bool(source.get("stale")), "changed": changed, "moved": moved, "conflicts": conflicts,
                           "pending": sum(managed.get(k, {}).get("revision") != (revisions[k] if k in revisions else _revision(n, _body(n, incident[k], titles, secrets))) for k, n in nodes.items()),
                           "edges": [e for e in source.get("edges", []) if e.get("source") in active and e.get("target") in active]})
        if signature:checkpoint['source_signature']=signature
        _save(checkpoint_path, checkpoint)
        return {"ok": True, 'source_signature':signature, **{k: checkpoint[k] for k in ("engine", "synced_at", "changed", "pending", "conflicts", "stale")}}
    finally:
        os.close(lock_fd)


def read_graph(state_root: Path) -> dict:
    home = _home(state_root)
    checkpoint = _read_json(home / "sync.json")
    if not checkpoint:
        return {"ok": False, "nodes": [], "edges": [], "stale": True, "osk": {"state": "preparing", "engine": VERSION}}
    contract, graph, _, _ = _load_engine(state_root)
    idx = graph.Index()
    nodes, identities = [], {}
    for key, item in checkpoint.get("managed", {}).items():
        if not item.get("active"):
            continue
        resolved = idx.nodes.get(item["title"])
        if not resolved:
            continue
        note = contract.parse(resolved[0])
        if contract.validate(note) or note.id != item.get("osk_id"):
            continue
        nodes.append({**item["source"], "description": str(note.meta.get("summary", "")), "facts": [str(note.meta.get("summary", ""))]})
        identities[item["title"]] = key
    # Human-created ordinary OSK notes participate too; governance/hub never does.
    for title, (path, kind) in idx.nodes.items():
        if title in identities or kind[0] in ("governance", "workbench", "archive") or graph.is_hub(path):
            continue
        note = contract.parse(path)
        if contract.validate(note):
            continue
        if any(item.get("osk_id") == note.id for item in checkpoint.get("managed", {}).values()):
            continue  # A retracted imported note remains recoverable, not searchable.
        key = "osk:" + note.id
        identities[title] = key
        nodes.append({"id": key, "label": title, "category": "memory", "description": str(note.meta.get("summary", "")),
                      "importance": 45, "updated_at": int(path.stat().st_mtime), "evidence": {"kind": "seed"}})
    group_ids={}
    for key,item in checkpoint.get('groups',{}).items():
        resolved=idx.nodes.get(item['title'])
        if not resolved: continue
        note=contract.parse(resolved[0])
        if contract.validate(note) or note.id!=item['osk_id']: continue
        node_id='osk:'+note.id;group_ids[key]=node_id;identities[item['title']]=node_id
        nodes.append({'id':node_id,'label':GROUPS[key][2],'category':'collection','description':str(note.meta.get('summary','')),'importance':100 if key=='kakao' else 98,'updated_at':int(resolved[0].stat().st_mtime),'evidence':{'kind':'structure'}})
    for node in nodes:
        if node['category']!='collection': node['importance']=min(int(node.get('importance',50)),94)
    ids = {n["id"] for n in nodes}
    edges = [e for e in checkpoint.get("edges", []) if e.get("source") in ids and e.get("target") in ids]
    if 'kakao' in group_ids:
        for key,target in group_ids.items():
            if key!='kakao': edges.append({'source':group_ids['kakao'],'target':target,'relation':'contains','weight':2,'evidence':{'kind':'structure'}})
    for key,item in checkpoint.get('managed',{}).items():
        parent=group_ids.get(_group(item['source']))
        if key in ids and parent: edges.append({'source':parent,'target':key,'relation':'contains','weight':1,'evidence':{'kind':'structure'}})
    existing = {(e["source"], e["target"]) for e in edges}
    for title, key in identities.items():
        note = contract.parse(idx.nodes[title][0])
        for target in note.wikilinks():
            if target in identities and identities[target] != key and (key, identities[target]) not in existing:
                edges.append({"source": key, "target": identities[target], "relation": "linked", "weight": 1, "evidence": {"kind": "seed"}})
    stale = checkpoint.get("stale", True) or int(time.time()) - checkpoint.get("synced_at", 0) > 180
    return {"ok": True, "nodes": nodes, "edges": edges, "node_count": len(nodes), "edge_count": len(edges),
            "indexed_at": checkpoint.get("source_indexed_at", 0), "stale": stale,
            "osk": {"state": "paused" if _aborted(state_root) else "ready", "engine": VERSION,
                    "synced_at": checkpoint.get("synced_at", 0), "pending": checkpoint.get("pending", 0),
                    "conflicts": checkpoint.get("conflicts", 0)}}


def read_focus(state_root: Path, node_id: str, *, chat_id: str = '') -> dict:
    home = _home(state_root)
    checkpoint = _read_json(home / "sync.json")
    if not checkpoint:
        return {"ok": False, "facts": [], "fact_count": 0}
    contract, graph, secrets, _ = _load_engine(state_root)
    idx = graph.Index()
    item = checkpoint.get("managed", {}).get(node_id)
    title = item.get("title") if item and item.get("active") else None
    if node_id.startswith("osk:"):
        match = idx.by_id.get(node_id[4:])
        title = match[0].stem if match else None
    if not title or title not in idx.nodes:
        return {"ok": False, "facts": [], "fact_count": 0}
    note = contract.parse(idx.nodes[title][0])
    if contract.validate(note):
        return {"ok": False, "facts": [], "fact_count": 0}
    source = item.get('source', {}) if item and item.get('active') else {}
    evidence = source.get('evidence', {})
    summary = _clean(source.get('description') if source and not item.get('held') else note.meta.get('summary', ''), secrets, 1200)
    summary = summary or _clean(note.meta.get('summary', ''), secrets, 1200)
    details = {
        'node_id': node_id, 'summary': summary,
        'kind': str(source.get('category') or ('collection' if graph.is_hub(idx.nodes[title][0]) else 'memory')),
        'basis': 'structure' if graph.is_hub(idx.nodes[title][0]) else 'snapshot' if evidence.get('kind') in ('local_db_snapshot', 'snapshot') else 'ledger' if evidence.get('kind') in ('decision_ledger', 'ledger') else 'note',
        'key_facts': [_clean(value, secrets, 600) for value in source.get('facts', [])[:6] if isinstance(value, str)] if not item or not item.get('held') else [],
        'source_updated_at': source.get('updated_at', 0),
        'note_updated_at': int(idx.nodes[title][0].stat().st_mtime),
        'scope_room_id': '',
    }
    actor=re.fullmatch(r'person:kakao:([0-9a-f]{64}):actor:([0-9]+)',node_id)
    room=re.fullmatch(r'chat:kakao:([0-9a-f]{64}):room:([0-9]+)',node_id)
    if actor or room:
        from alden_corpus import search
        scope=actor or room
        selected_room = str(chat_id or '').strip()
        if selected_room.startswith('kakao:'):
            parsed = re.fullmatch(r'kakao:([0-9a-f]{64}):room:([0-9]+)', selected_room)
            if not parsed or parsed[1] != scope[1]:
                return {'ok': False, 'reason': 'focus_room_scope_invalid', 'facts': [], 'fact_count': 0}
            selected_room = parsed[2]
        if selected_room and (not selected_room.isascii() or not selected_room.isdigit() or not 0 < int(selected_room) < 2**63
                              or room and selected_room != room[2]):
            return {'ok': False, 'reason': 'focus_room_scope_invalid', 'facts': [], 'fact_count': 0}
        selected_room = room[2] if room else selected_room
        details['scope_room_id'] = selected_room
        details['key_facts'] = []
        if actor and selected_room:
            summary = '선택한 채팅방의 원문에서 발화가 확인된 카카오톡 대화 상대입니다.'
            details['summary'] = summary
        result=search(state_root,'',author_id=actor[2] if actor else '',chat_id=selected_room,expected_account=scope[1])
        if result.get('ok'):
            # A selected room cannot inherit an actor's multi-room cached samples.
            details['key_facts'] = []
            titles = {key.rsplit(':', 1)[-1]: value['source'].get('label', '') for key, value in checkpoint.get('managed', {}).items()
                      if key.startswith('chat:kakao:' + scope[1] + ':room:') and value.get('active')}
            sources, seen = [], set()
            for raw in result['items']:
                if raw['source_id'] in seen: continue
                seen.add(raw['source_id'])
                row = dict(raw)
                row['content'] = _clean(row['content'], secrets, 1000)
                row['sender'] = _clean(row['sender'], secrets, 128)
                row['room_title'] = _clean(titles.get(str(row['chat_id'])) or ('제목 미확인 · #' + digest(str(row['chat_id']).encode())[:8]), secrets, 128)
                sources.append(row)
            if actor and selected_room and not sources:
                summary = '선택한 채팅방에서 이 인물의 원문을 아직 찾지 못했습니다.'
                details['summary'] = summary
            facts=[summary]+[str(row['date'])[:10]+' · '+str(row['sender'])+' · '+str(row['content']) for row in sources]
            details['basis'] = 'snapshot'
            details['sample_count'] = len(sources)
            return {'ok':True,'facts':facts,'fact_count':len(facts),'focus_node_id':node_id,'sources':sources,'details':details,'search_mode':'bm25'}
        details['source_unavailable'] = True
        if actor and selected_room:
            summary = '선택한 채팅방의 원문에 접근할 수 없어 이 인물의 세부 내용을 확인하지 못했습니다.'
            details['summary'] = summary
        return {'ok': True, 'facts': [summary], 'fact_count': 1, 'focus_node_id': node_id, 'sources': [], 'details': details}
    return {"ok": True, "facts": [summary, note.body[:1200]], "fact_count": 2, "focus_node_id": node_id, 'details': details}


def sync_cycle(state_root: Path, binary: Path | None = None, *, collect=None, sync=None, record=None, now=None, cycle=None) -> dict:
    """Collection readiness does not prevent updating a valid saved corpus.

    A waiting original-data permission is reported independently. Retry only
    after the bounded backoff; no send/worker/model action is part of this cycle.
    """
    from alden_history import cycle_step, _local_cli, LocalDataAccessWaiting
    from auto_reply_knowledge_graph import _index_db_path
    collect = collect or _local_cli; sync = sync or synchronize; record = record or cycle_step
    now = time.time() if now is None else now
    cycle = cycle or uuid.uuid4().hex; status_path = _home(state_root)/'producer.json'
    previous = _read_json(status_path); collection = 'not_requested'; corpus = {}
    _safe_directory(_home(state_root))
    if _aborted(state_root):
        record(state_root,cycle,'paused');return {'ok':False,'state':'paused'}
    waiting = binary is not None and previous.get('collection')=='waiting' and now < previous.get('collection_retry_at',0)
    if binary and not waiting:
        record(state_root,cycle,'collecting')
        try:
            collected=collect(binary,['local-db-collect','--index-dir',str(state_root/'knowledge/corpus'),'--max-rows','500000','--abort-state',str(state_root/'alden-abort.json')])
            corpus=collected.get('index',{});collection='complete' if corpus.get('complete') else 'pending'
        except LocalDataAccessWaiting:
            collection='waiting'
    elif waiting: collection='waiting'
    # Fail closed if no valid saved account corpus exists. This does not probe
    # the original Kakao database or change its permission policy.
    source_path = _index_db_path(state_root)
    if not source_path.is_file():raise RuntimeError('saved_corpus_unavailable')
    record(state_root,cycle,'graphing',reason='LocalDataAccessWaiting' if collection=='waiting' else '')
    if ( _home(state_root)/'raw-sources.json').is_file():
        from alden_osk_delta import capture
        _contract,_graph,secrets,_write=_load_engine(state_root)
        archive=capture(state_root,secrets.filter_text,cancelled=lambda:_aborted(state_root))
    else: archive={}
    result=sync(state_root)
    checkpoint=_read_json(_home(state_root)/'sync.json')
    pending=int(corpus.get('pending_messages',0))+int(result.get('pending',0))+int(checkpoint.get('layout_pending',0))+int(result.get('conflicts',0))
    phase='paused' if _aborted(state_root) else 'complete' if collection!='waiting' and result.get('ok') and result.get('state')!='busy' and not result.get('stale') and pending==0 else 'pending'
    record(state_root,cycle,phase,nodes=sum(bool(m.get('active')) for m in checkpoint.get('managed',{}).values()),pending=pending,changed=result.get('changed',0),reason='LocalDataAccessWaiting' if collection=='waiting' else '')
    status={'schema_version':1,'checked_at':int(now),'collection':collection,'collection_retry_at':int(now+300) if collection=='waiting' and not waiting else previous.get('collection_retry_at',0) if waiting else 0,
            'saved_corpus_ready':bool(result.get('ok') and not result.get('stale') and pending==0),'source_signature':result.get('source_signature'),
            'archive_snapshot':archive.get('snapshot'),'archive_rows':archive.get('rows')}
    _save(status_path,status)
    return {**result,'collection':collection,'archive':archive}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--sync", action="store_true")
    parser.add_argument('--bin',type=Path)
    args = parser.parse_args()
    cycle=uuid.uuid4().hex
    try:
        result = sync_cycle(args.state_root,args.bin,cycle=cycle) if args.sync else read_graph(args.state_root)
    except Exception as error:
        result = {"ok": False, "state": "unavailable", "error": type(error).__name__}
        if args.sync and args.bin:
            from alden_history import cycle_step
            cycle_step(args.state_root,cycle,'failed',reason=type(error).__name__)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
