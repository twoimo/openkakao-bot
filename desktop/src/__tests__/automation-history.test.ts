// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { settingsMarkup } from '../ui';
import { wireAutomationHistory } from '../automation-history';
import { wireSettingsNavigation } from '../settings-navigation';
import type { fetchSettingsAction } from '../runtime';
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
const item = (id: string, outcome = 'confirmed') => ({ id, room: '디자인 이야기', at: 100, outcome, incoming: '이전 맥락은?', reply: '<img src=x onerror=bad>', context: [{ sender: '상대', text: '약속 취소', sent_at: 90 }] });
beforeEach(() => { document.body.innerHTML = settingsMarkup(); });

describe('separate automatic histories', () => {
  it('loads only the selected history and renders source content as text', async () => {
    const load = vi.fn<typeof fetchSettingsAction>(async () => ({ ok: true, items: [item('room:event')], total: 1, next: null }));
    const control = wireAutomationHistory(load); const navigation = wireSettingsNavigation(document, control.select);
    expect(load).not.toHaveBeenCalled();
    document.getElementById('settings-tab-reply')!.click(); await settle();
    expect(load.mock.calls.map(call => call[0])).toEqual(['reply-history']);
    expect(document.getElementById('reply-detail')!.textContent).toContain('약속 취소');
    expect(document.getElementById('reply-detail')!.textContent).toContain('보낸 답변');
    expect(document.querySelector('#reply-detail img')).toBeNull();
    document.getElementById('settings-tab-geeknews')!.click(); await settle();
    expect(load.mock.calls[1][0]).toBe('geeknews-history');
    expect(document.querySelectorAll('[role="tabpanel"]:not([hidden])')).toHaveLength(1);
    navigation.dispose(); control.dispose();
  });
  it('does not mark uncertain output as a sent answer and reports partial reads', async () => {
    const control = wireAutomationHistory(async () => ({ ok: true, items: [item('unknown', 'unknown')], total: 1, next: null, partial: true }));
    control.select('reply'); await settle();
    expect(document.getElementById('reply-detail')!.textContent).toContain('확인 필요');
    expect(document.getElementById('reply-detail')!.textContent).not.toContain('보낸 답변');
    expect(document.getElementById('reply-status')!.textContent).toContain('일부 채팅방');
    control.dispose();
  });
  it('pages distinct events and retains exact cursor IDs', async () => {
    const cursor = [100, '9007199254740997:event'];
    const load = vi.fn<typeof fetchSettingsAction>(async (_action, args) => JSON.parse(args!.query!).before ? { ok: true, items: [item('older')], total: 2, next: null } : { ok: true, items: [item(cursor[1] as string)], total: 2, next: cursor });
    const control = wireAutomationHistory(load); control.select('reply'); await settle();
    document.getElementById('reply-older')!.click(); await settle();
    expect(JSON.parse(load.mock.calls[1][1]!.query!).before).toEqual(cursor);
    expect(document.querySelectorAll('#reply-list button')).toHaveLength(2);
    expect(document.getElementById('reply-older')!.hidden).toBe(true); control.dispose();
  });
  it('rejects late responses after hiding and refreshes once on restoration', async () => {
    let finish!: (value: Record<string, unknown>) => void;
    const load = vi.fn<typeof fetchSettingsAction>().mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })).mockResolvedValue({ ok: true, items: [item('new')], total: 1, next: null });
    const control = wireAutomationHistory(load); control.select('reply'); control.visible(false);
    finish({ ok: true, items: [item('stale')], total: 1, next: null }); await settle();
    expect(document.querySelector('#reply-list button')).toBeNull();
    control.visible(true); await settle();
    expect(load).toHaveBeenCalledTimes(2); expect(document.querySelector('#reply-list button')).not.toBeNull();
    control.dispose(); document.getElementById('reply-refresh')!.click(); await settle(); expect(load).toHaveBeenCalledTimes(2);
  });
  it('searches and filters on the source reader without exposing write actions', async () => {
    const load = vi.fn<typeof fetchSettingsAction>(async () => ({ ok: true, items: [], total: 0, next: null }));
    const control = wireAutomationHistory(load); control.select('geeknews'); await settle();
    const search = document.getElementById('geeknews-search') as HTMLInputElement; search.value = '새 소식'; search.dispatchEvent(new Event('input'));
    const filter = document.getElementById('geeknews-filter') as HTMLSelectElement; filter.value = 'unknown'; filter.dispatchEvent(new Event('change'));
    await new Promise(resolve => setTimeout(resolve, 210));
    expect(JSON.parse(load.mock.calls.at(-1)![1]!.query!)).toMatchObject({ search: '새 소식', status: 'unknown' });
    expect(load.mock.calls.every(call => call[0] === 'geeknews-history')).toBe(true);
    expect(document.getElementById('geeknews-detail')!.textContent).toContain('조건에 맞는 기록이 없습니다'); control.dispose();
  });
});
