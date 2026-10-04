// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { settingsMarkup } from '../ui';
import { wireConversationViews } from '../conversations';
import { VirtualList } from '../virtual-list';
import type { fetchSettingsAction } from '../runtime';
beforeEach(() => { document.body.innerHTML = settingsMarkup(); });
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

describe('complete local histories', () => {
  it('collapses system metadata while preserving its original and a user JSON message', async () => {
    const text='{"feedType":2,"member":{"nickName":"예시"}}';
    const load=vi.fn<typeof fetchSettingsAction>(async action=>action==='history-rooms'
      ? {ok:true,rooms:[{chat_id:'1',chat_name:'기록'}]}
      : {ok:true,anchor_log_id:'2',total:2,next_before:null,messages:[{id:'1',type:0,text},{id:'2',type:1,text}]});
    const views=wireConversationViews(load);views.select('conversation');await settle();
    const rows=document.querySelectorAll('.message-row');
    expect(rows[0].querySelector('details')?.open).toBe(false);
    expect(rows[0].querySelector('.message-text')?.textContent).toBe(text);
    expect(rows[0].querySelector('.message-author')?.textContent).toBe('대화방');
    expect(rows[1].querySelector('details')).toBeNull();
    expect(rows[1].querySelector('.message-text')?.textContent).toBe(text);views.dispose();
  });
  it('opens the latest room and keeps the last message visible when pagination appears', async () => {
    const load=vi.fn<typeof fetchSettingsAction>(async(action)=>action==='history-rooms'
      ? {ok:true,rooms:[{chat_id:'1',chat_name:'최근 대화'}]}
      : {ok:true,anchor_log_id:'9',next_before:'8',total:9,messages:[{id:'9',text:'최신 원문'}]});
    const views=wireConversationViews(load);views.select('conversation');await settle();
    expect(load.mock.calls.some(([action,args])=>action==='history-messages'&&args?.chatId==='1')).toBe(true);
    expect(document.getElementById('conversation-placeholder')!.hidden).toBe(true);
    expect(document.getElementById('chat-message-list')!.textContent).toContain('최신 원문');
    expect(document.getElementById('conversation-history-older')!.hidden).toBe(false);views.dispose();
  });
  it('shows a failed empty reader and retries once instead of retaining a loading screen', async () => {
    let available=false;
    const load=vi.fn<typeof fetchSettingsAction>(async(action)=>action==='history-rooms'
      ? available?{ok:true,rooms:[{chat_id:'1',chat_name:'방'}]}:{ok:false}
      : {ok:true,anchor_log_id:'1',next_before:null,total:1,messages:[{id:'1',text:'복구한 기록'}]});
    const views=wireConversationViews(load);views.select('conversation');await settle();
    expect(document.getElementById('conversation-placeholder')!.textContent).toContain('불러오지 못했습니다');
    expect(document.getElementById('conversation-retry')!.hidden).toBe(false);
    available=true;document.getElementById('conversation-retry')!.click();await settle();
    expect(load.mock.calls.filter(([action])=>action==='history-rooms')).toHaveLength(2);
    expect(document.getElementById('conversation-placeholder')!.hidden).toBe(true);views.dispose();
  });
  it('refreshes a revisited voice session without duplicating old turns or losing the older cursor', async () => {
    let revision=1;
    const load=vi.fn<typeof fetchSettingsAction>(async(action)=>action==='voice-history-sessions'
      ? {ok:true,items:[{id:'session',title:'음성 기록'}]}
      : {ok:true,next:revision===1?5:10,items:[{turn_id:1,role:'user',content:'첫 발화'},...(revision===2?[{turn_id:2,role:'assistant',content:'새 답변'}]:[])]});
    const views=wireConversationViews(load);views.select('voice');await settle();
    revision=2;views.select('settings');views.select('voice');await settle();
    expect(document.getElementById('voice-message-list')!.textContent?.match(/첫 발화/g)).toHaveLength(1);
    expect(document.getElementById('voice-message-list')!.textContent).toContain('새 답변');
    document.getElementById('voice-history-older')!.click();await settle();
    const calls=load.mock.calls.filter(([action])=>action==='voice-history-messages');
    expect(JSON.parse(calls.at(-1)![1]!.query!).before).toBe(5);views.dispose();
  });
  it('discards a late room response and reads the new room with string IDs', async () => {
    let release!: (data: Record<string, unknown>) => void;
    const load = vi.fn<typeof fetchSettingsAction>(async (action, input = {}) => {
      if (action === 'history-rooms') return { ok: true, rooms: [{ chat_id: '9007199254740997', chat_name: 'A' }, { chat_id: '2', chat_name: 'B' }] };
      if (action === 'history-messages' && input.chatId !== '2') return new Promise(resolve => { release = resolve; });
      return { ok: true, anchor_log_id: '99', next_before: null, total: 1, messages: [{ id: '99', text: '<script>bad</script>\n' + '긴 원문'.repeat(10000), sender: 'B' }] };
    });
    const views = wireConversationViews(load); views.select('conversation'); await settle();
    document.querySelector<HTMLButtonElement>('#chat-room-list button')!.click();
    document.querySelectorAll<HTMLButtonElement>('#chat-room-list button')[1].click();
    release({ ok: true, anchor_log_id: '1', total: 1, messages: [{ id: '1', text: 'OLD_ROOM' }] }); await settle();
    expect(document.getElementById('chat-message-list')!.textContent).not.toContain('OLD_ROOM');
    expect(document.querySelector('.message-text')!.textContent!.length).toBeGreaterThan(40000);
    expect(document.querySelector('#chat-message-list script')).toBeNull();
    expect(load.mock.calls.find(([action]) => action === 'history-messages')?.[1]?.chatId).toBe('9007199254740997'); views.dispose();
  });
  it('retains older DB receipts across current-state polling and stops when hidden', async () => {
    vi.useFakeTimers();
    const load = vi.fn<typeof fetchSettingsAction>(async (action, input = {}) => {
      if (action !== 'db-sync-history') return null;
      const older = JSON.parse(input.query!).before;
      return { ok: true, current: { phase: 'pending' }, next: older ? null : 2, items: [{ id: older ? 1 : 3, phase: older ? 'collecting' : 'pending', details: older ? { messages: 2034253 } : {} }] };
    });
    const views = wireConversationViews(load); views.select('history'); await vi.advanceTimersByTimeAsync(0);
    document.getElementById('db-history-older')!.click(); await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(5000); expect(document.querySelectorAll('.db-cycle-row')).toHaveLength(2);
    expect(document.getElementById('db-cycle-list')!.textContent).toContain('2,034,253');
    expect(document.getElementById('db-cycle-list')!.textContent).toContain('대기');
    views.visible(false); const calls = load.mock.calls.length; await vi.advanceTimersByTimeAsync(20000); expect(load).toHaveBeenCalledTimes(calls);
    views.dispose(); vi.useRealTimers();
  });
  it('keeps a bounded DOM while all loaded message text remains addressable', () => {
    const host = document.createElement('div'); document.body.append(host);
    const list = new VirtualList<{id:number;text:string}>(host, row => String(row.id), row => { const node = document.createElement('p'); node.textContent = row.text; return node; });
    const messages = Array.from({ length: 10000 }, (_, id) => ({ id, text: `message ${id}` }));
    list.set(messages); expect(host.querySelectorAll('p').length).toBeLessThanOrEqual(80);
    host.scrollTop = 9999 * 78; host.dispatchEvent(new Event('scroll')); list.set(messages, { end: true });
    expect(host.textContent).toContain('message 9999'); expect(host.querySelectorAll('p').length).toBeLessThanOrEqual(80); list.dispose();
  });
  it('reopens a missing middle range after more than one page arrives while hidden', async () => {
    vi.useFakeTimers();
    let total = 10;
    const cursors: Array<number | null> = [];
    const load = vi.fn<typeof fetchSettingsAction>(async (action, input = {}) => {
      if (action !== 'db-sync-history') return null;
      const before = JSON.parse(input.query!).before as number | null;
      cursors.push(before);
      const high = before === null ? total : Math.min(total, before - 1);
      const low = Math.max(1, high - 49);
      return { ok: true, current: { phase: 'complete' }, next: low > 1 ? low : null,
        items: Array.from({ length: high - low + 1 }, (_, i) => ({ id: high - i, phase: 'complete', details: { messages: high - i } })) };
    });
    const views = wireConversationViews(load); views.select('history'); await vi.advanceTimersByTimeAsync(0);
    expect(document.getElementById('db-history-older')!.hidden).toBe(true);
    views.visible(false); total = 130; views.visible(true); await vi.advanceTimersByTimeAsync(0);
    expect(document.getElementById('db-history-older')!.hidden).toBe(false);
    document.getElementById('db-history-older')!.click(); await vi.advanceTimersByTimeAsync(0);
    expect(cursors.at(-1)).toBe(81);
    document.getElementById('db-history-older')!.click(); await vi.advanceTimersByTimeAsync(0);
    expect(cursors.at(-1)).toBe(31);
    expect(document.getElementById('db-history-older')!.hidden).toBe(true);
    expect(document.querySelectorAll('.db-cycle-row').length).toBeLessThanOrEqual(80);
    views.dispose(); vi.useRealTimers();
  });
});
