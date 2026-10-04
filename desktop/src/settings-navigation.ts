/*! Lucide icon notices
ISC License

Copyright (c) 2026 Lucide Icons and Contributors

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

---

The following Lucide icons are derived from the Feather project:

airplay, alert-circle, alert-octagon, alert-triangle, aperture, arrow-down-circle, arrow-down-left, arrow-down-right, arrow-down, arrow-left-circle, arrow-left, arrow-right-circle, arrow-right, arrow-up-circle, arrow-up-left, arrow-up-right, arrow-up, at-sign, calendar, cast, check, chevron-down, chevron-left, chevron-right, chevron-up, chevrons-down, chevrons-left, chevrons-right, chevrons-up, circle, clipboard, clock, code, columns, command, compass, corner-down-left, corner-down-right, corner-left-down, corner-left-up, corner-right-down, corner-right-up, corner-up-left, corner-up-right, crosshair, database, divide-circle, divide-square, dollar-sign, download, external-link, feather, frown, hash, headphones, help-circle, info, italic, key, layout, life-buoy, link-2, link, loader, lock, log-in, log-out, maximize, meh, minimize, minimize-2, minus-circle, minus-square, minus, monitor, moon, more-horizontal, more-vertical, move, music, navigation-2, navigation, octagon, pause-circle, percent, plus-circle, plus-square, plus, power, radio, rss, search, server, share, shopping-bag, sidebar, smartphone, smile, square, table-2, tablet, target, terminal, trash-2, trash, triangle, tv, type, upload, x-circle, x-octagon, x-square, x, zoom-in, zoom-out

The MIT License (MIT) (for the icons listed above)

Copyright (c) 2013-present Cole Bemis

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

*/
import { createIcons, Aperture, AudioLines, ChevronDown, History, Laptop, MessageCircle, MessagesSquare, Mic, Network, Plus, Rss, ShieldCheck, Sparkles, Waypoints, Zap, Database, Settings, X } from "lucide";

export type SettingsPage = "conversation" | "reply" | "geeknews" | "voice" | "memory" | "history" | "settings";
const labels: Record<SettingsPage, string> = { conversation: "카카오톡 대화", reply: "카카오톡 답변", geeknews: "긱뉴스 전송", voice: "음성 대화", memory: "지식 그래프", history: "기억 정리", settings: "설정" };

export function renderSettingsIcons(): void {
  createIcons({ icons: { Aperture, AudioLines, ChevronDown, History, Laptop, MessageCircle, MessagesSquare, Mic, Network, Plus, Rss, ShieldCheck, Sparkles, Waypoints, Zap, Database, Settings, X }, attrs: { "stroke-width": 1.6, "aria-hidden": "true" } });
}

export function wireSettingsNavigation(root: Document = document, changed: (page: SettingsPage) => void = () => undefined): { current: () => SettingsPage; dispose: () => void } {
  let current: SettingsPage = "memory";
  let initialized = false;
  const buttons = [...root.querySelectorAll<HTMLButtonElement>("[data-settings-view]")];
  const content = root.querySelector<HTMLElement>(".settings-content");
  const offsets = new Map<SettingsPage, number>();
  const listeners = new AbortController();
  const select = (page: SettingsPage): void => {
    if (!Object.hasOwn(labels, page) || (initialized && page === current)) return;
    initialized = true;
    if (content) offsets.set(current, content.scrollTop);
    current = page;
    for (const button of buttons) {
      const active = button.dataset.settingsView === page;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
      const panel = root.getElementById(button.getAttribute("aria-controls") ?? "");
      if (panel) panel.hidden = !active;
    }
    const shell = root.querySelector<HTMLElement>(".settings-shell");
    if (shell) shell.dataset.settingsPage = page;
    const title = root.getElementById("settings-current-view");
    if (title) title.textContent = labels[page];
    if (content) content.scrollTop = offsets.get(page) ?? 0;
    changed(page);
  };
  for (const [index, button] of buttons.entries()) {
    button.addEventListener("click", () => select(button.dataset.settingsView as SettingsPage), { signal: listeners.signal });
    button.addEventListener("keydown", (event) => {
      let next: number;
      if (event.key === "ArrowDown" || event.key === "ArrowRight") next = (index + 1) % buttons.length;
      else if (event.key === "ArrowUp" || event.key === "ArrowLeft") next = (index + buttons.length - 1) % buttons.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = buttons.length - 1;
      else return;
      event.preventDefault();
      const target = buttons[next];
      if (target) { target.focus(); select(target.dataset.settingsView as SettingsPage); }
    }, { signal: listeners.signal });
  }
  root.getElementById("open-voice-page")?.addEventListener("click", () => select("voice"), { signal: listeners.signal });
  select(current);
  return { current: () => current, dispose: () => listeners.abort() };
}
