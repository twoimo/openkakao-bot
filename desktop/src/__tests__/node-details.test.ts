// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from 'vitest';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { renderNodeDetails, nodeSummary } from '../knowledge/node-details';
const account='1'.repeat(64),other='2'.repeat(64);
const id=`person:kakao:${account}:actor:7`;
const graph=parseKnowledgeGraph({nodes:[{id,label:'같은 이름',category:'대화 상대',description:'기록에 남은 대화 상대'}],edges:[]});
beforeEach(()=>{document.body.innerHTML='<p id="knowledge-node-summary"></p><p id="knowledge-node-kind"></p><p id="knowledge-node-basis"></p><ul id="knowledge-node-facts"></ul><h2 id="knowledge-evidence-heading"></h2><div id="knowledge-node-evidence"></div>';});
const source={source_id:`kakao:${account}:room:42:log:1`,source_kind:'local_db_snapshot',author_id:'7',room_title:'회의',sender:'같은 이름',date:'2026-10-01T15:00:00+09:00',content:'금요일 오후 3시에 회의합니다.',source_role:'peer_history'};
describe('selected-node explanations',()=>{
  it('displays traceable original text and excludes another room, account and same-name actor',()=>{
    const payload={details:{node_id:id,scope_room_id:'42',summary:'선택한 방의 참여자',basis:'snapshot'},sources:[source,source,{...source,source_id:`kakao:${account}:room:84:log:2`,content:'다른 방'},{...source,source_id:`kakao:${other}:room:42:log:3`,content:'다른 계정'},{...source,author_id:'8',source_id:`kakao:${account}:room:42:log:4`,content:'동명이인'}]};
    renderNodeDetails(graph,graph.nodes[0],payload);
    expect(document.querySelectorAll('.knowledge-source')).toHaveLength(1);
    expect(document.body.textContent).toContain('금요일 오후 3시');
    expect(document.body.textContent).not.toContain('다른 방');expect(document.body.textContent).not.toContain('동명이인');
    expect(document.getElementById('knowledge-node-basis')?.textContent).toContain('최근 기록의 일부');
  });
  it('keeps quoted markup inert and does not classify outgoing history as Alden speech',()=>{
    renderNodeDetails(graph,graph.nodes[0],{details:{node_id:id},sources:[{...source,content:'<img src=x onerror="alert(1)">',source_role:'outgoing_unclassified'}]});
    expect(document.querySelector('img')).toBeNull();expect(document.body.textContent).toContain('<img');
    expect(document.body.textContent).toContain('발신자 구분 미확인');
  });
  it('rejects mismatched details and labels missing proof explicitly',()=>{
    renderNodeDetails(graph,graph.nodes[0],{details:{node_id:'another',summary:'잘못된 설명'},sources:[source]});
    expect(document.body.textContent).not.toContain('잘못된 설명');expect(document.querySelector('.knowledge-source')).toBeNull();
    expect(document.body.textContent).toContain('원문을 확인하지 못했습니다');
    const collection=parseKnowledgeGraph({nodes:[{id:'group',label:'인물',category:'collection'},...graph.nodes],edges:[{source:'group',target:id,relation:'contains'}]});
    expect(nodeSummary(collection,collection.nodes[0])).toContain('1개 항목');
  });
});
