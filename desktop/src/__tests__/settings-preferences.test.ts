// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { settingsMarkup } from '../ui';
import { wireSettingsPreferences } from '../settings-preferences';
import type { fetchSettingsAction } from '../runtime';
beforeEach(() => { document.body.innerHTML = settingsMarkup(); });
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

describe('settings menu catalog', () => {
  it('searches normalized room names, disambiguates them and submits only a picked exact ID', async () => {
    const load = vi.fn<typeof fetchSettingsAction>(async action => action === 'history-rooms'
      ? {ok:true, rooms:[{chat_id:'9007199254740997',chat_name:'ＡＢＣ 방'},{chat_id:'9007199254740998',chat_name:'ABC 방'}]}
      : {ok:true, rooms:[]});
    const control=wireSettingsPreferences(load);control.select('settings');await settle();
    const search=document.getElementById('automation-room-search') as HTMLInputElement;
    search.value='abc방';search.dispatchEvent(new Event('input'));
    expect(document.querySelectorAll('#automation-room-options [role=option]')).toHaveLength(2);
    expect(document.getElementById('automation-room-options')!.textContent).toContain('740997');
    document.getElementById('automation-form')!.dispatchEvent(new Event('submit',{cancelable:true}));await settle();
    expect(load.mock.calls.some(([name])=>name==='room-upsert')).toBe(false);
    search.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',cancelable:true}));
    search.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',cancelable:true}));
    expect((document.getElementById('automation-room') as HTMLSelectElement).value).toBe('9007199254740998');
    expect(search.getAttribute('aria-expanded')).toBe('false');control.dispose();
  });
  it('reads on entry and confirms create, update and delete without a send', async () => {
    let catalog: Record<string, unknown>[] = [];
    const load = vi.fn<typeof fetchSettingsAction>(async (action, input = {}) => {
      if (action === 'history-rooms') return { ok: true, rooms: [{ chat_id: '9007199254740997', chat_name: '방' }] };
      if (action === 'room-catalog') return { ok: true, rooms: catalog };
      if (action === 'room-upsert') catalog = [{ chat_id: input.chatId, ...JSON.parse(input.query!) }];
      if (action === 'room-delete') catalog = [];
      return { ok: true };
    });
    const control = wireSettingsPreferences(load);
    expect(load).not.toHaveBeenCalled(); control.select('settings'); await settle();
    document.getElementById('automation-new')!.click();
    const select = document.getElementById('automation-room') as HTMLSelectElement;
    select.value = '9007199254740997'; select.dispatchEvent(new Event('change'));
    (document.getElementById('automation-reply') as HTMLInputElement).checked = true;
    document.getElementById('automation-form')!.dispatchEvent(new Event('submit', { cancelable: true }));
    await settle(); expect(catalog[0].auto_reply).toBe(true); expect(document.getElementById('automation-status')!.textContent).toContain('저장했습니다');
    expect(document.getElementById('automation-form')!.hidden).toBe(true);
    expect(document.getElementById('automation-status')!.hidden).toBe(false);
    document.querySelector<HTMLButtonElement>('[aria-label="방 자동화 수정"]')!.click();
    (document.getElementById('automation-geeknews') as HTMLInputElement).checked = true;
    document.getElementById('automation-form')!.dispatchEvent(new Event('submit', { cancelable: true })); await settle();
    expect(catalog[0].geeknews).toBe(true);
    document.querySelector<HTMLButtonElement>('[aria-label="방 자동화 등록 삭제"]')!.click(); await settle();
    expect(catalog).toEqual([]); expect(document.getElementById('automation-status')!.textContent).toContain('삭제했습니다');
    expect(load.mock.calls.every(([name]) => ['history-rooms', 'room-catalog', 'room-upsert', 'room-delete'].includes(name))).toBe(true);
    control.dispose();
  });
  it('does not report success or repeat a write when readback fails', async () => {
    let wrote = false;
    const load = vi.fn<typeof fetchSettingsAction>(async action => {
      if (action === 'history-rooms') return { ok: true, rooms: [{ chat_id: '1', chat_name: '<img onerror=bad>' }] };
      if (action === 'room-catalog') return wrote ? null : { ok: true, rooms: [{ chat_id: '1', title: '<img onerror=bad>', auto_reply: false, geeknews: false }] };
      if (action === 'room-delete') wrote = true;
      return null;
    });
    const control = wireSettingsPreferences(load); control.select('settings'); await settle();
    expect(document.querySelector('#automation-list img')).toBeNull();
    document.querySelector<HTMLButtonElement>('#automation-list button:last-child')!.click(); await settle();
    expect(document.getElementById('automation-status')!.textContent).toContain('확인하지 못했습니다');
    expect(document.getElementById('automation-status')!.hidden).toBe(false);
    expect(load.mock.calls.filter(([name]) => name === 'room-delete')).toHaveLength(1); control.dispose();
  });
});
