import { fetchSettingsAction } from './runtime';
import type { SettingsPage } from './settings-navigation';

type Row = Record<string, unknown>;
type Result = { select(page: SettingsPage): void; visible(flag: boolean): void; dispose(): void };

/** Catalog changes are confirmed by a fresh read; uncertain writes are never retried. */
export function wireSettingsPreferences(load = fetchSettingsAction, root: Document = document): Result {
  const get = <T extends HTMLElement>(id: string) => root.getElementById(id) as T;
  const form = get<HTMLFormElement>('automation-form');
  const select = get<HTMLSelectElement>('automation-room');
  const search = get<HTMLInputElement>('automation-room-search');
  const options = get<HTMLElement>('automation-room-options');
  const title = get<HTMLInputElement>('automation-title-input');
  const reply = get<HTMLInputElement>('automation-reply');
  const news = get<HTMLInputElement>('automation-geeknews');
  const status = get<HTMLElement>('automation-status');
  const list = get<HTMLElement>('automation-list');
  const workspace = form.parentElement!;
  form.hidden = true;
  const showEditor = (flag: boolean) => { form.hidden = !flag; workspace.dataset.editing = String(flag); };
  const events = new AbortController();
  let dead = false, busy = false, reading = false, visible = true, page: SettingsPage = 'memory';
  let catalog: Row[] = [];
  let roomOptions: Array<{id:string;label:string}> = [], matches: Array<{id:string;label:string}> = [], cursor = 0;
  const normalize = (text:string) => text.normalize('NFKC').toLocaleLowerCase('ko-KR').replace(/\s+/g,'');
  const closePicker = () => { options.hidden = true; search.setAttribute('aria-expanded','false'); search.removeAttribute('aria-activedescendant'); };
  const syncPicker = () => { search.value = roomOptions.find(room=>room.id===select.value)?.label ?? ''; closePicker(); };
  const pick = (room:{id:string;label:string}) => { search.focus(); select.value = room.id; search.value = room.label; closePicker(); select.dispatchEvent(new Event('change')); };
  const showPicker = () => {
    if (busy || dead) return;
    const query = normalize(search.value); matches = roomOptions.filter(room=>normalize(room.label).includes(query)||room.id.includes(query)).slice(0,60); cursor = Math.min(cursor,Math.max(0,matches.length-1));
    options.replaceChildren();
    matches.forEach((room,index)=>{ const button=root.createElement('button'); button.type='button'; button.id='automation-room-option-'+index; button.setAttribute('role','option'); button.setAttribute('aria-selected',String(index===cursor)); button.textContent=room.label; button.title=room.label+' · '+room.id; button.onpointerdown=event=>event.preventDefault(); button.onclick=()=>pick(room); options.append(button); });
    if (!matches.length) { const empty=root.createElement('p'); empty.textContent='검색 결과 없음'; options.append(empty); }
    options.hidden=false;search.setAttribute('aria-expanded','true');
    if(matches[cursor])search.setAttribute('aria-activedescendant','automation-room-option-'+cursor);else search.removeAttribute('aria-activedescendant');
  };
  search.addEventListener('input',()=>{select.value='';cursor=0;showPicker();},{signal:events.signal});
  search.addEventListener('focus',showPicker,{signal:events.signal});
  search.addEventListener('blur',closePicker,{signal:events.signal});
  search.addEventListener('keydown',event=>{
    if(event.key==='Escape'){closePicker();return;}
    if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();if(options.hidden){cursor=0;showPicker();}else{cursor=Math.max(0,Math.min(matches.length-1,cursor+(event.key==='ArrowDown'?1:-1)));showPicker();}options.querySelector('[aria-selected="true"]')?.scrollIntoView?.({block:'nearest'});}
    else if(event.key==='Enter'&&!options.hidden){event.preventDefault();if(matches[cursor])pick(matches[cursor]);}
  },{signal:events.signal});
  const message = (text: string) => { if (!dead) { status.textContent = text; status.hidden = !text; } };
  const setBusy = (flag: boolean) => {
    busy = flag;
    if (dead) return;
    for (const control of [...form.querySelectorAll<HTMLInputElement | HTMLButtonElement | HTMLSelectElement>('input,select,button'), ...list.querySelectorAll<HTMLButtonElement>('button')]) control.disabled = flag;
    form.setAttribute('aria-busy', String(flag));
  };
  const edit = (row: Row) => {
    showEditor(true);
    select.value = String(row.chat_id);
    syncPicker();
    title.value = String(row.title ?? '');
    reply.checked = row.auto_reply === true;
    news.checked = row.geeknews === true;
    form.dataset.editingId = String(row.chat_id);
    get<HTMLElement>('automation-editor-title').textContent = '자동화 수정';
    render();
  };
  function render(): void {
    list.replaceChildren();
    get<HTMLElement>('automation-count').textContent = `${catalog.length}개 등록`;
    if (!catalog.length) {
      const empty = root.createElement('p');
      empty.className = 'automation-empty';
      const heading = root.createElement('strong'), detail = root.createElement('span');
      heading.textContent = '대화 공간을 추가하세요'; detail.textContent = '새 등록을 눌러 첫 채팅방 자동화를 만들어 보세요.';
      empty.append(heading, detail); list.append(empty);
    }
    for (const row of catalog) {
      const entry = root.createElement('div'); entry.className = 'automation-row';
      entry.dataset.selected = String(form.dataset.editingId === String(row.chat_id));
      const copy = root.createElement('div'), name = root.createElement('strong'), flags = root.createElement('p');
      name.textContent = String(row.title || '이름 없는 대화방');
      for (const [label, enabled] of [['자동 답변', row.auto_reply], ['긱뉴스', row.geeknews]] as const) {
        const flag = root.createElement('span'); flag.className = 'automation-feature'; flag.dataset.enabled = String(enabled === true); flag.textContent = `${label} ${enabled === true ? '켜짐' : '꺼짐'}`; flags.append(flag);
      }
      copy.append(name, flags);
      const modify = root.createElement('button'), remove = root.createElement('button');
      modify.type = remove.type = 'button'; modify.textContent = '수정'; remove.textContent = '등록 삭제';
      modify.setAttribute('aria-label', `${name.textContent} 자동화 수정`);
      remove.setAttribute('aria-label', `${name.textContent} 자동화 등록 삭제`);
      modify.disabled = remove.disabled = busy;
      modify.onclick = () => { if (!busy) { edit(row); title.focus(); } };
      remove.onclick = () => { void drop(row); };
      entry.append(copy, modify, remove); list.append(entry);
    }
  }
  async function refresh(): Promise<boolean> {
    if (dead || reading) return false;
    reading = true;
    try {
      const [rooms, registered] = await Promise.all([load('history-rooms'), load('room-catalog')]);
      if (dead) return false;
      if (registered?.ok !== true || !Array.isArray(registered.rooms)) {
        message('자동화 등록 정보를 불러오지 못했습니다.'); return false;
      }
      catalog = registered.rooms as Row[]; render();
      if (!catalog.length) showEditor(true);
      if (rooms?.ok === true && Array.isArray(rooms.rooms)) {
        const previous = select.value;
        const option = (label: string, value: string) => { const node = root.createElement('option'); node.textContent = label; node.value = value; return node; };
        select.replaceChildren(option('채팅방 선택', ''));
        const all = new Map((rooms.rooms as Row[]).map(room => [String(room.chat_id), String(room.chat_name || '이름 없는 대화방')]));
        for (const row of catalog) if (!all.has(String(row.chat_id))) all.set(String(row.chat_id), String(row.title || '이름 없는 대화방'));
        const counts = new Map<string,number>();for(const label of all.values()){const key=normalize(label);counts.set(key,(counts.get(key)??0)+1);}
        roomOptions = [...all].map(([id,label])=>({id,label:(counts.get(normalize(label))??0)>1?`${label} · ${id}`:label}));
        for (const {id, label} of roomOptions) select.append(option(label, id));
        select.value = previous;
        if (!search.value || select.value) syncPicker();
      }
      return true;
    } catch { message('자동화 등록 정보를 불러오지 못했습니다.'); return false; }
    finally { reading = false; }
  }
  async function drop(row: Row): Promise<void> {
    if (busy || reading || dead) return;
    setBusy(true); message('자동화 등록을 삭제하는 중입니다.');
    try {
      await load('room-delete', { chatId: String(row.chat_id) });
      const verified = await refresh();
      message(!verified ? '삭제 상태를 확인하지 못했습니다. 설정을 다시 열어 확인해 주세요.'
        : catalog.some(item => String(item.chat_id) === String(row.chat_id)) ? '등록이 남아 있습니다. 다시 확인해 주세요.'
          : '자동화 등록을 삭제했습니다. 대화 기록은 유지됩니다.');
    } catch { message('삭제 상태를 확인하지 못했습니다. 설정을 다시 열어 확인해 주세요.'); }
    finally { setBusy(false); }
  }
  form.addEventListener('submit', event => {
    event.preventDefault(); if (busy || reading || dead) return;
    if (!select.value) { message('검색 결과에서 채팅방을 선택하세요.'); search.focus(); return; }
    const id = select.value, payload = { title: title.value.trim() || select.selectedOptions[0]?.textContent || '', auto_reply: reply.checked, geeknews: news.checked };
    setBusy(true); message('자동화 설정을 저장하는 중입니다.');
    void (async () => {
      try {
        await load('room-upsert', { chatId: id, query: JSON.stringify(payload) });
        const verified = await refresh(), saved = catalog.find(row => String(row.chat_id) === id);
        const confirmed = verified && saved?.title === payload.title && saved.auto_reply === payload.auto_reply && saved.geeknews === payload.geeknews;
        if (confirmed) { showEditor(false); delete form.dataset.editingId; render(); }
        message(confirmed ? '저장했습니다.' : '저장 상태를 확인하지 못했습니다. 설정을 다시 열어 확인해 주세요.');
      } catch { message('저장 상태를 확인하지 못했습니다. 설정을 다시 열어 확인해 주세요.'); }
      finally { setBusy(false); }
    })();
  }, { signal: events.signal });
  get<HTMLElement>('automation-new').addEventListener('click', () => {
    if (busy) return; showEditor(true); select.value = ''; search.value = ''; title.value = ''; reply.checked = news.checked = false; delete form.dataset.editingId; get<HTMLElement>('automation-editor-title').textContent = '새 자동화 등록'; message(''); render(); search.focus();
  }, { signal: events.signal });
  get<HTMLElement>('automation-cancel').addEventListener('click', () => {
    if (busy) return; showEditor(false); delete form.dataset.editingId; message(''); render(); get<HTMLElement>('automation-new').focus();
  }, { signal: events.signal });
  select.addEventListener('change', () => {
    syncPicker();
    const row = catalog.find(item => String(item.chat_id) === select.value);
    if (row) edit(row); else { title.value = select.value ? select.selectedOptions[0]?.textContent ?? '' : ''; reply.checked = news.checked = false; delete form.dataset.editingId; get<HTMLElement>('automation-editor-title').textContent = '새 자동화 등록'; render(); }
  }, { signal: events.signal });
  return {
    select(next) { const entered = next === 'settings' && page !== next; page = next; if (entered && visible && !busy) void refresh(); },
    visible(flag) { const restored = flag && !visible; visible = flag; if (restored && page === 'settings' && !busy) void refresh(); },
    dispose() { dead = true; events.abort(); },
  };
}
