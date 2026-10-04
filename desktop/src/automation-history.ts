import { fetchSettingsAction } from './runtime';
import type { SettingsPage } from './settings-navigation';
import { VirtualList } from './virtual-list';

type Kind = 'reply' | 'geeknews';
type Row = Record<string, unknown>;
type Cursor = [number, string] | null;
const labels: Record<string, string> = { confirmed: '전송 확인', waiting: '처리 중·대기', unknown: '확인 필요', failed: '실패', cancelled: '취소', skipped: '건너뜀' };
const text = (value: unknown): string => typeof value === 'string' ? value : '';
const rows = (value: unknown): Row[] => Array.isArray(value) ? value.filter(row => row && typeof row === 'object' && !Array.isArray(row)) : [];
const time = (value: unknown): string => typeof value === 'number' && Number.isFinite(value) && value > 0
  ? new Date(value * 1000).toLocaleString('ko-KR', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '시간 미기록';
const node = (tag: string, className: string, value = ''): HTMLElement => {
  const element = document.createElement(tag); element.className = className; element.textContent = value; return element;
};

export function wireAutomationHistory(load: typeof fetchSettingsAction = fetchSettingsAction): {
  select: (page: SettingsPage) => void; visible: (flag: boolean) => void; dispose: () => void;
} {
  let page: SettingsPage = 'memory', visible = true, dead = false;
  const listeners = new AbortController();
  const states = new Map<Kind, { items: Row[]; selected: string; next: Cursor; epoch: number; busy: boolean; again: boolean; detail: string; list: VirtualList<Row>; debounce: ReturnType<typeof setTimeout> | null }>();
  const get = (kind: Kind, suffix: string): HTMLElement => document.getElementById(`${kind}-${suffix}`)!;
  const setStatus = (kind: Kind, message: string): void => { get(kind, 'status').textContent = message; };

  function renderDetail(kind: Kind, row: Row | undefined): void {
    const state = states.get(kind)!;
    const signature = JSON.stringify(row ?? null);
    if (signature === state.detail) return;
    state.detail = signature;
    const host = get(kind, 'detail'); host.replaceChildren(); host.scrollTop = 0;
    if (!row) { get(kind, 'title').textContent = kind === 'reply' ? '카카오톡 답변' : '긱뉴스 전송'; host.append(node('p', 'history-empty', '조건에 맞는 기록이 없습니다.')); return; }
    get(kind, 'title').textContent = text(row.room) || (kind === 'reply' ? '카카오톡 답변' : '긱뉴스 전송');
    const meta = node('div', 'automation-record-meta');
    const badge = node('span', 'automation-outcome', labels[text(row.outcome)] ?? labels.unknown);
    badge.dataset.outcome = text(row.outcome);
    meta.append(badge, node('time', '', time(row.at)));
    if (kind === 'reply' && text(row.sender)) meta.append(node('span', '', text(row.sender)));
    host.append(meta);
    if (row.preview_only === true || row.text_truncated === true) host.append(node('p', 'automation-preview-note', row.preview_only === true ? '이전 기록에는 내용 일부만 보관되어 있습니다.' : '긴 내용의 앞부분을 표시합니다.'));
    const block = (heading: string, content: string): void => {
      const section = node('section', 'automation-record-block');
      section.append(node('h3', '', heading), node('div', 'automation-record-text', content)); host.append(section);
    };
    if (kind === 'reply') {
      if (text(row.incoming)) block('받은 말씀', text(row.incoming));
      block(text(row.outcome) === 'confirmed' ? '보낸 답변' : '답변', text(row.reply) || '답변 생성 기록 없음');
    } else {
      if (text(row.slot)) host.append(node('p', 'automation-slot', text(row.slot)));
      const articles = rows(row.articles);
      if (articles.length) {
        const list = node('ol', 'automation-articles');
        for (const article of articles) {
          const item = node('li', ''); const url = text(article.url);
          if (/^https:\/\/news\.hada\.io\/topic\?id=[1-9][0-9]*$/.test(url)) {
            const link = node('a', '', text(article.title) || '기사 보기') as HTMLAnchorElement;
            link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; item.append(link);
          } else item.textContent = text(article.title);
          list.append(item);
        }
        host.append(list);
        const original = node('details', 'automation-record-block');
        original.append(node('summary', '', '전송 내용 원문'), node('div', 'automation-record-text', text(row.reply) || text(row.incoming))); host.append(original);
      } else block('전송 내용', text(row.reply) || text(row.incoming) || '내용 기록 없음');
    }
    const context = rows(row.context);
    if (context.length) {
      const section = node('details', 'automation-context') as HTMLDetailsElement; section.open = true;
      section.append(node('summary', '', `기록된 대화 맥락 · ${context.length}개`));
      for (const item of context) {
        const quote = node('blockquote', '');
        quote.append(node('span', 'automation-context-author', [text(item.sender), typeof item.sent_at === 'number' && item.sent_at > 0 ? time(item.sent_at) : ''].filter(Boolean).join(' · ')), node('p', '', text(item.text)));
        section.append(quote);
      }
      host.append(section);
    } else if (kind === 'reply') host.append(node('p', 'automation-preview-note', '대화 맥락이 보관되지 않은 기록입니다.'));
  }

  function choose(kind: Kind, row: Row): void {
    const state = states.get(kind)!; state.selected = text(row.id);
    renderDetail(kind, row); state.list.set(state.items, { preserve: true });
  }

  async function read(kind: Kind, older = false): Promise<void> {
    const state = states.get(kind)!;
    if (dead || !visible || page !== kind) return;
    if (state.busy) { state.again = true; return; }
    if (older && !state.next) return;
    state.busy = true; state.again = false; const epoch = state.epoch;
    setStatus(kind, '기록을 불러옵니다.');
    const query = { limit: 50, before: older ? state.next : null, search: (get(kind, 'search') as HTMLInputElement).value, status: (get(kind, 'filter') as HTMLSelectElement).value };
    try {
      const data = await load(kind === 'reply' ? 'reply-history' : 'geeknews-history', { query: JSON.stringify(query) });
      if (dead || !visible || page !== kind || epoch !== state.epoch) return;
      if (data?.ok !== true) { setStatus(kind, '기록을 불러오지 못했습니다. 새로 고침을 누르세요.'); return; }
      const incoming = rows(data.items).filter(item => text(item.id));
      const merged = new Map((older ? state.items : []).map(item => [text(item.id), item]));
      for (const item of incoming) merged.set(text(item.id), item);
      state.items = [...merged.values()];
      const cursor = data.next;
      state.next = Array.isArray(cursor) && cursor.length === 2 && typeof cursor[0] === 'number' && Number.isFinite(cursor[0]) && typeof cursor[1] === 'string' ? cursor as [number, string] : null;
      if (!state.items.some(item => item.id === state.selected)) state.selected = text(state.items[0]?.id);
      state.list.set(state.items, { preserve: older });
      renderDetail(kind, state.items.find(item => item.id === state.selected));
      get(kind, 'older').hidden = state.next === null;
      const count = typeof data.total === 'number' && Number.isFinite(data.total) ? data.total.toLocaleString('ko-KR') : String(state.items.length);
      setStatus(kind, `${count}개 기록${data.partial === true ? ' · 일부 채팅방을 불러오지 못했습니다.' : ''}`);
    } catch {
      if (!dead && visible && page === kind && epoch === state.epoch) setStatus(kind, '기록을 불러오지 못했습니다. 새로 고침을 누르세요.');
    } finally {
      state.busy = false;
      if (state.again && !dead && visible && page === kind) void read(kind);
    }
  }

  for (const kind of ['reply', 'geeknews'] as const) {
    const list = new VirtualList<Row>(get(kind, 'list'), row => text(row.id), row => {
      const button = node('button', 'history-room automation-history-row') as HTMLButtonElement; button.type = 'button';
      button.setAttribute('aria-current', String(row.id === states.get(kind)!.selected));
      button.append(node('strong', '', text(row.room)), node('span', 'automation-row-preview', text(kind === 'reply' ? row.incoming : row.slot) || text(row.reply)), node('span', 'automation-row-meta', `${time(row.at)} · ${labels[text(row.outcome)] ?? labels.unknown}`));
      button.addEventListener('click', () => choose(kind, row));
      const entry=node('div','');entry.setAttribute('role','listitem');entry.append(button);return entry;
    }, 92);
    list.setVisible(false);
    states.set(kind, { items: [], selected: '', next: null, epoch: 0, busy: false, again: false, detail: '', list, debounce: null });
    const filter = (): void => {
      const state = states.get(kind)!; state.epoch++;
      if (state.debounce !== null) clearTimeout(state.debounce);
      state.debounce = setTimeout(() => { state.debounce = null; void read(kind); }, 180);
    };
    get(kind, 'search').addEventListener('input', filter, { signal: listeners.signal });
    get(kind, 'filter').addEventListener('change', filter, { signal: listeners.signal });
    get(kind, 'refresh').addEventListener('click', () => void read(kind), { signal: listeners.signal });
    get(kind, 'older').addEventListener('click', () => void read(kind, true), { signal: listeners.signal });
  }
  function select(next: SettingsPage): void {
    const changed = page !== next; page = next;
    for (const [kind, state] of states) {
      if (changed) state.epoch++;
      state.list.setVisible(visible && page === kind);
      if ((!visible || page !== kind) && state.debounce !== null) { clearTimeout(state.debounce); state.debounce = null; }
    }
    if (visible && (next === 'reply' || next === 'geeknews') && changed) void read(next);
  }
  return {
    select,
    visible(flag) { if (visible === flag) return; visible = flag; for (const state of states.values()) state.epoch++; const current = page; page = 'memory'; select(current); },
    dispose() { dead = true; listeners.abort(); for (const state of states.values()) { state.epoch++; state.list.dispose(); if (state.debounce !== null) clearTimeout(state.debounce); } },
  };
}
