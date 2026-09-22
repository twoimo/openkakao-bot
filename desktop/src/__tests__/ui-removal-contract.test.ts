// @vitest-environment happy-dom
// The core-only panel and the deletion of the bulk-verification, feature-check,
// permission, and self-improvement surfaces are user requirements, not
// conventions. Until now the deletion was only a property of the markup: nothing
// failed if one of those panels came back. These checks pin all three surfaces.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { MAIN_PANEL_CONTROLS, mainPanelMarkup, settingsMarkup } from "../ui";

const read = (relative: string): string =>
  readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf8");

const STYLES = read("../styles.css");
const PANEL = mainPanelMarkup();
const SETTINGS = settingsMarkup();

// A match means one of the deleted control surfaces came back.
const REMOVED_TOKENS = [
  "검증",
  "점검",
  "권한",
  "자기개선",
  "자가개선",
  "자동개선",
  "일괄",
  "대량",
  "permission",
  "verify",
  "self-improv",
  "selfimprov",
  "bulk",
  "approve",
  "audit",
];

const SETTINGS_SECTIONS = [
  "대상 채팅방",
  "AI 모델",
  "Voice",
  "카카오 DB 동기화 · 색인",
  "DREAM-RSI",
  "Knowledge",
  "History",
];

const offending = (text: string): string[] =>
  REMOVED_TOKENS.filter((token) => text.toLowerCase().includes(token.toLowerCase()));

const interactiveCount = (markup: string): number =>
  (markup.match(/<(button|select|input|textarea|a|details|summary)\b/gi) ?? []).length;

describe("removed UI surfaces stay removed", () => {
  it("detects the tokens it bans", () => {
    // A vacuous contract would pass forever, so the detector is checked first.
    expect(offending("<section>일괄 검증</section>")).toEqual(["검증", "일괄"]);
    expect(offending('<button id="permission-row">권한</button>')).toEqual(["권한", "permission"]);
    expect(offending("<canvas id=\"knowledge-graph-canvas\"></canvas>")).toEqual([]);
  });

  it("keeps the main panel free of interactive elements", () => {
    expect(MAIN_PANEL_CONTROLS).toHaveLength(0);
    expect(interactiveCount(PANEL)).toBe(0);
    expect(PANEL).not.toContain('id="gear"');
  });

  it("reintroduces no removed control in the panel markup", () => {
    expect(offending(PANEL)).toEqual([]);
  });

  it("reintroduces no removed control in the settings markup", () => {
    expect(offending(SETTINGS)).toEqual([]);
  });

  it("reintroduces no removed control in the stylesheet", () => {
    expect(offending(STYLES)).toEqual([]);
  });

  it("keeps the settings window to its declared sections in order", () => {
    const headings = [...SETTINGS.matchAll(/<h2[^>]*>([^<]+)<\/h2>/g)].map((match) => match[1]);
    expect(headings).toEqual(SETTINGS_SECTIONS);
  });

  it("keeps every settings surface inside the one window", () => {
    expect((SETTINGS.match(/<main\b/g) ?? []).length).toBe(1);
    expect(SETTINGS).toContain('<main class="settings-shell">');
    expect(SETTINGS).not.toContain("<iframe");
    expect(PANEL).not.toContain("<iframe");
  });
});
