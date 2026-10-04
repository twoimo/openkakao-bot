// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { settingsMarkup, SETTINGS_IDS } from "../ui";
import { wireSettingsNavigation } from "../settings-navigation";
import { RenderLifecycle } from "../core/lifecycle";
import { AnimationLoop, type FrameScheduler } from "../core/animation-loop";
const tab = (page: string) => document.querySelector<HTMLButtonElement>(`[data-settings-view="${page}"]`)!;
beforeEach(() => { document.body.innerHTML = settingsMarkup(); });
describe("wide settings navigation", () => {
  it("keeps bound actions once and exposes one selected panel", () => {
    const changed = vi.fn(); const nav = wireSettingsNavigation(document, changed);
    for (const id of SETTINGS_IDS) expect(document.querySelectorAll(`[id="${id}"]`).length).toBe(1);
    tab("memory").click();
    expect(nav.current()).toBe("memory");
    expect(document.querySelectorAll('[role="tabpanel"]:not([hidden])').length).toBe(1);
    expect(document.getElementById("settings-page-memory")!.hidden).toBe(false);
    expect(document.getElementById("settings-current-view")!.textContent).toBe("지식 그래프");
    expect(tab("memory").getAttribute("aria-selected")).toBe("true");
    tab("memory").click(); expect(changed).toHaveBeenCalledTimes(1);
    nav.dispose();
  });
  it("moves keyboard focus and selection together, including wrapping", () => {
    const nav = wireSettingsNavigation(); tab("conversation").focus();
    tab("conversation").dispatchEvent(new KeyboardEvent("keydown", { key: "End", bubbles: true }));
    expect(document.activeElement).toBe(tab("settings")); expect(nav.current()).toBe("settings");
    tab("settings").dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
    expect(nav.current()).toBe("memory"); expect(document.activeElement).toBe(tab("memory"));
    expect([...document.querySelectorAll<HTMLButtonElement>('[role="tab"]')].filter(b=>b.tabIndex===0)).toHaveLength(1);
    nav.dispose();
  });
  it("preserves each view's scroll position and stops disposed listeners", () => {
    const nav=wireSettingsNavigation(), content=document.querySelector<HTMLElement>(".settings-content")!;
    tab("conversation").click(); content.scrollTop=80; tab("memory").click(); content.scrollTop=140;
    tab("voice").click(); tab("conversation").click(); expect(content.scrollTop).toBe(80);
    tab("memory").click(); expect(content.scrollTop).toBe(140);
    nav.dispose(); tab("history").click(); expect(nav.current()).toBe("memory");
  });
  it("ignores a non-whitelisted view rather than corrupting the current page", () => {
    const nav=wireSettingsNavigation(); tab("voice").dataset.settingsView="__proto__";
    document.getElementById("settings-tab-voice")!.click(); expect(nav.current()).toBe("memory"); nav.dispose();
  });
});
class Scheduler implements FrameScheduler {
  queued=new Map<number,FrameRequestCallback>();next=0;
  request(fn:FrameRequestCallback){const id=++this.next;this.queued.set(id,fn);return id;}
  cancel(id:number){this.queued.delete(id);}
  now(){return 0;}
}
describe("hidden settings surface lifecycle",()=>{
  it("only animates when both window and memory page are visible",()=>{
    const scheduler=new Scheduler(), loop=new AnimationLoop(()=>undefined,scheduler);
    const lifecycle=new RenderLifecycle(loop,()=>undefined,()=>undefined);
    lifecycle.setSurfaceVisible(false); lifecycle.transition("visible"); expect(scheduler.queued.size).toBe(0);
    lifecycle.setSurfaceVisible(true); expect(scheduler.queued.size).toBe(1);
    lifecycle.setSurfaceVisible(false); expect(scheduler.queued.size).toBe(0);
    lifecycle.transition("hidden"); lifecycle.setSurfaceVisible(true); expect(scheduler.queued.size).toBe(0);
    lifecycle.transition("visible"); expect(scheduler.queued.size).toBe(1);
    lifecycle.transition("closed"); lifecycle.setSurfaceVisible(false); lifecycle.setSurfaceVisible(true); expect(scheduler.queued.size).toBe(0);
  });
});
