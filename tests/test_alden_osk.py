"""Exercise the bundled, unmodified OSK engine, not a replacement fake writer."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PRELUDE = r'''
import sys, json
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import alden_osk as a
root = Path(sys.argv[2])
source = {'ok': True, 'indexed_at': 42, 'stale': False, 'nodes': [
 {'id':'chat:1', 'label':'한 공간', 'category':'room', 'description':'대화 공간', 'facts':[], 'evidence':{'kind':'ledger','chat_id':'1'}},
 {'id':'topic:one', 'label':'맥락', 'category':'topic', 'description':'맥락을 이어가는 기억', 'facts':['최신 턴을 보존합니다.'], 'evidence':{'kind':'ledger','source_event_ids':['turn:1'],'chat_id':'1'}}],
 'edges':[{'source':'chat:1','target':'topic:one','relation':'mentions','weight':3,'room_id':'1','evidence':{'kind':'ledger','chat_id':'1'}}]}
'''


class OskIntegrationTests(unittest.TestCase):
    def test_clicked_actor_details_keep_exact_room_account_and_original_roles(self):
        result = self.execute(r'''
import sqlite3
account='1'*64
actor='person:kakao:'+account+':actor:7'
room42='chat:kakao:'+account+':room:42'
room84='chat:kakao:'+account+':room:84'
source['nodes'] += [
 {'id':actor,'label':'동일 이름','category':'대화 상대','description':'여러 방에서 수집된 화자','facts':['다른 방의 캐시 문장'], 'evidence':{'kind':'local_db_snapshot'}},
 {'id':room42,'label':'선택한 방','category':'대화방','facts':[],'evidence':{'kind':'local_db_snapshot'}},
 {'id':room84,'label':'다른 방','category':'대화방','facts':[],'evidence':{'kind':'local_db_snapshot'}}]
a.synchronize(root,source)
p=root/'knowledge/corpus'/account/'context.sqlite3';p.parent.mkdir(parents=True)
with sqlite3.connect(p) as db:
 db.executescript('CREATE TABLE corpus_meta(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE alden_messages(id INTEGER PRIMARY KEY,chat_id TEXT,log_id TEXT,author_id TEXT,user_name TEXT,message TEXT,date TEXT,is_self INTEGER,message_type INTEGER);')
 db.execute('INSERT INTO corpus_meta VALUES (?,?)',('account',account))
 db.executemany('INSERT INTO alden_messages VALUES (?,?,?,?,?,?,?,?,?)',[
  (1,'42','1','7','동일 이름','금요일 3시 회의','2026-10-01 15:00:00',1,1),
  (2,'84','2','7','동일 이름','다른 방 영화 내용','2026-10-02 15:00:00',0,1),
  (3,'42','3','8','동일 이름','동명이인 문장','2026-10-02 16:00:00',0,1)])
(root/'knowledge/corpus/current.json').write_text(json.dumps({'schema_version':1,'account':account}))
payload=a.read_focus(root,actor,chat_id='42')
assert payload['ok'] and len(payload['sources'])==1
assert payload['sources'][0]['room_title']=='선택한 방'
assert payload['sources'][0]['source_role']=='outgoing_unclassified'
assert payload['details']['key_facts']==[] and payload['details']['scope_room_id']=='42'
assert '다른 방' not in json.dumps(payload,ensure_ascii=False) and '동명이인' not in json.dumps(payload,ensure_ascii=False)
foreign=a.read_focus(root,actor,chat_id='kakao:'+('2'*64)+':room:42')
assert not foreign['ok'] and foreign['reason']=='focus_room_scope_invalid'
mismatch=a.read_focus(root,room42,chat_id='84');assert not mismatch['ok']
missing=a.read_focus(root,actor,chat_id='99');assert missing['sources']==[] and '찾지 못했습니다' in missing['details']['summary']
print(json.dumps({'scoped':True,'classified':False}))
''')
        self.assertTrue(result['scoped'])

    def execute(self, code):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, "-B", "-s", "-c", PRELUDE + code, str(SCRIPTS), str(Path(tmp).resolve())], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

    def test_real_create_readback_noop_and_incremental_update(self):
        result = self.execute('''
first=a.synchronize(root,source); before=a.read_graph(root)
source['nodes'][1]['updated_at']=12345
second=a.synchronize(root,source)
checkpoint=json.loads((a._home(root)/'sync.json').read_text())
identity=checkpoint['managed']['topic:one']['osk_id']
source['nodes'][1]['description']='更新된 한국어 기억'
third=a.synchronize(root,source); after=a.read_graph(root)
assert sum(n['category']!='collection' for n in before['nodes'])==2 and before['edges'][0]['relation']=='mentions'
assert third['changed']==1
assert json.loads((a._home(root)/'sync.json').read_text())['managed']['topic:one']['osk_id']==identity
assert a.read_focus(root,'topic:one')['facts'][0]=='更新된 한국어 기억'
assert list((a._home(root)/'history').rglob('*.md'))
print(json.dumps({'first':first['changed'],'second':second['changed'],'updated':third['changed']}))
''')
        self.assertEqual(result, {"first": 2, "second": 0, "updated": 1})

    def test_human_edits_are_preserved_and_reported(self):
        result = self.execute('''
a.synchronize(root,source)
state=json.loads((a._home(root)/'sync.json').read_text());item=state['managed']['topic:one']
note=a._home(root)/'vault'/item['space']/f"{item['title']}.md"
data=note.read_text()+'\\n사람이 추가한 기억입니다.\\n';note.write_text(data)
source['nodes'][1]['description']='새 자동 요약'
status=a.synchronize(root,source)
assert note.read_text()==data
print(json.dumps({'conflicts':status['conflicts'],'kept':a.read_focus(root,'topic:one')['facts'][1].endswith('사람이 추가한 기억입니다.\\n')}))
''')
        self.assertEqual(result, {"conflicts": 1, "kept": True})

    def test_retraction_hides_but_preserves_note_and_stale_never_retracts(self):
        result = self.execute('''
a.synchronize(root,source)
source['nodes']=source['nodes'][:1];source['edges']=[];source['stale']=True
a.synchronize(root,source);assert sum(n['category']!='collection' for n in a.read_graph(root)['nodes'])==2
source['stale']=False;a.synchronize(root,source)
assert sum(n['category']!='collection' for n in a.read_graph(root)['nodes'])==1
assert len(list((a._home(root)/'vault').rglob('*.md')))>3
print(json.dumps({'retracted':True}))
''')
        self.assertTrue(result["retracted"])

    def test_interrupted_create_recovers_by_exact_readback(self):
        result = self.execute('''
a.synchronize(root,source)
p=a._home(root)/'sync.json';state=json.loads(p.read_text());identity=state['managed'].pop('topic:one')['osk_id'];a._save(p,state)
status=a.synchronize(root,source)
assert json.loads(p.read_text())['managed']['topic:one']['osk_id']==identity
assert sum(n['category']!='collection' for n in a.read_graph(root)['nodes'])==2
print(json.dumps({'recovered':True}))
''')
        self.assertTrue(result["recovered"])

    def test_secret_filter_and_no_global_hooks(self):
        result = self.execute('''
source['nodes'][1]['facts']=['Bearer sk-'+('a'*48)]
a.synchronize(root,source)
raw=(a._home(root)/'sync.json').read_text()+'\\n'.join(p.read_text() for p in (a._home(root)/'vault').rglob('*.md'))
assert 'sk-'+('a'*48) not in raw
assert not (a._home(root)/'vault/.git').exists()
assert not (a._home(root)/'vault/00_Scope/Workbench/_ledger/approvals.jsonl').exists()
print(json.dumps({'filtered':True}))
''')
        self.assertTrue(result["filtered"])

    def test_symlink_target_rejected(self):
        result = self.execute('''
(root/'knowledge').mkdir();(root/'elsewhere').mkdir();(root/'knowledge/osk').symlink_to(root/'elsewhere',target_is_directory=True)
try:a.synchronize(root,source)
except RuntimeError as e:assert str(e)=='osk_symlink_path'
else:raise AssertionError('followed symlink')
assert not list((root/'elsewhere').iterdir())
print(json.dumps({'rejected':True}))
''')
        self.assertTrue(result["rejected"])

    def test_emergency_latch_blocks_writes(self):
        result = self.execute('''
from alden_abort import AbortController
AbortController(root / 'alden-abort.json').abort('test')
status=a.synchronize(root,source)
assert status['state']=='paused' and not (a._home(root)/'sync.json').exists()
print(json.dumps({'paused':True}))
''')
        self.assertTrue(result["paused"])

    def test_manual_osk_note_enters_graph_with_original_id(self):
        result = self.execute('''
a.synchronize(root,source)
contract,graph,secrets,write=a._load_engine(root)
receipt=write.create_node('직접 남긴 기억','사람이 정리한 추가 지식','자세한 메모입니다.','agent',space='00_Scope/Alden')
view=a.read_graph(root)
assert sum(n['category']!='collection' for n in view['nodes'])==3
assert a.read_focus(root,'osk:'+receipt['id'])['facts'][0]=='사람이 정리한 추가 지식'
print(json.dumps({'manual':True}))
''')
        self.assertTrue(result["manual"])

    def test_people_have_real_person_spaces_and_names_do_not_merge_identities(self):
        result=self.execute('''
source['nodes'] += [{'id':'person:a','label':'같은 이름','category':'대화 상대','facts':[],'evidence':{'kind':'ledger','chat_id':'1'}},{'id':'person:b','label':'같은 이름','category':'화자','facts':[],'evidence':{'kind':'ledger','chat_id':'2'}}]
a.synchronize(root,source);state=json.loads((a._home(root)/'sync.json').read_text())
contract,graph,secrets,write=a._load_engine(root)
for key in ('person:a','person:b'):
 item=state['managed'][key];assert item['space'].startswith('00_Person/')
 note=contract.parse(a._home(root)/'vault'/item['space']/(item['title']+'.md'));assert note.id==item['osk_id'];assert not contract.validate(note)
assert state['managed']['person:a']['osk_id']!=state['managed']['person:b']['osk_id']
view=a.read_graph(root);assert {'카카오톡','대화방','인물','주제'}<={n['label'] for n in view['nodes'] if n['category']=='collection'}
assert any(e['relation']=='contains' for e in view['edges'])
print(json.dumps({'personSpaces':True,'distinct':True}))
''')
        self.assertEqual(result,{'personSpaces':True,'distinct':True})


if __name__ == "__main__":
    unittest.main()
