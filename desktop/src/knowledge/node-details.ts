import type { KnowledgeGraph, KnowledgeNode } from './graph-model';

function text(value: unknown, limit = 1200): string {
  return typeof value === 'string' ? value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '').trim().slice(0, limit) : '';
}
function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
function element(tag: string, className: string, copy = ''): HTMLElement {
  const item = document.createElement(tag); item.className = className; item.textContent = copy; return item;
}
function date(value: unknown): string {
  const epoch = typeof value === 'number' ? value * 1000 : Date.parse(text(value));
  return Number.isFinite(epoch) && epoch > 0 ? new Date(epoch).toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' }) : '시점 미확인';
}

export function nodeKind(node: KnowledgeNode): string {
  if (node.category === 'collection') return '지식 모음';
  if (/대화 상대|인물|화자|person/.test(node.category)) return '대화 상대';
  if (/대화방|room/.test(node.category)) return '채팅방';
  if (/주제|topic/.test(node.category)) return '대화 주제';
  return '저장된 기억';
}

export function nodeSummary(graph: KnowledgeGraph, node: KnowledgeNode): string {
  if (node.category === 'collection') {
    const children = graph.edges.filter(edge => edge.source === node.id && edge.relation === 'contains').map(edge => edge.target);
    return `${node.label}에 속한 ${children.length}개 항목을 묶은 모음입니다. 항목을 선택하면 대화 기록과 연결된 내용을 확인할 수 있습니다.`;
  }
  return text(node.description) || `${nodeKind(node)}로 분류된 항목입니다. 원문을 확인해 설명할 수 있는 내용을 찾고 있습니다.`;
}

export function renderNodeDetails(graph: KnowledgeGraph, node: KnowledgeNode, payload: Record<string, unknown> | null = null, root: Document = document): void {
  const details = record(payload?.details);
  const selected = details && details.node_id === node.id ? details : null;
  const summary = root.getElementById('knowledge-node-summary');
  if (summary) summary.textContent = text(selected?.summary) || nodeSummary(graph, node);
  const kind = root.getElementById('knowledge-node-kind'); if (kind) kind.textContent = nodeKind(node);
  const facts = root.getElementById('knowledge-node-facts'); facts?.replaceChildren();
  const values = selected?.key_facts;
  if (Array.isArray(values)) for (const value of [...new Set(values.filter(value => typeof value === 'string'))].slice(0, 6)) {
    if (text(value, 600)) facts?.append(element('li', '', text(value, 600)));
  }
  if (facts) facts.hidden = facts.children.length === 0;
  const evidence = root.getElementById('knowledge-node-evidence'); evidence?.replaceChildren();
  const seen = new Set<string>();
  const identity = node.id.match(/^(person|chat):kakao:([0-9a-f]{64}):(actor|room):([0-9]+)$/);
  const rawSources = selected && Array.isArray(payload?.sources) ? payload.sources : [];
  for (const raw of rawSources.slice(0, 12)) {
    const source = record(raw); if (!source) continue;
    const id = text(source.source_id, 200), quote = text(source.content, 1000);
    const sourceId = id.match(/^kakao:([0-9a-f]{64}):room:([0-9]+):log:([0-9]+)$/);
    if (!quote || !sourceId || source.source_kind !== 'local_db_snapshot' || seen.has(id)
      || selected?.scope_room_id && String(selected.scope_room_id) !== sourceId[2]
      || identity && (identity[2] !== sourceId[1] || identity[1] === 'chat' && identity[4] !== sourceId[2]
        || identity[1] === 'person' && identity[4] !== String(source.author_id))) continue;
    seen.add(id);
    const entry = element('blockquote', 'knowledge-source');
    entry.dataset.sourceId = id;
    const author = source.source_role === 'outgoing_unclassified' ? '발신자 구분 미확인' : source.source_role === 'system_history' ? '시스템 기록' : text(source.sender, 128) || '이름 미확인';
    entry.append(element('p', 'knowledge-source-text', quote));
    entry.append(element('cite', '', `${text(source.room_title, 128) || '제목 미확인'} · ${author} · ${date(source.date)}${source.truncated === true ? ' · 일부 발췌' : ''}`));
    evidence?.append(entry);
    if (seen.size === 6) break;
  }
  const heading = root.getElementById('knowledge-evidence-heading'); if (heading) heading.hidden = seen.size === 0;
  const basis = root.getElementById('knowledge-node-basis');
  if (basis) basis.textContent = seen.size ? `원문 ${seen.size}건 · 최근 기록의 일부입니다.`
    : node.category === 'collection' ? '그래프에 실제 등록된 구조를 기준으로 설명합니다.'
    : selected?.basis === 'note' ? '저장된 노트의 설명입니다. 대화 원문으로 확인된 사실과 구분합니다.'
    : payload ? '원문을 확인하지 못했습니다. 저장된 설명만 표시합니다.' : '저장된 설명 · 원문을 확인하고 있습니다.';
}
