// The design contract in desktop/DESIGN.md is only binding if something fails
// when the code drifts away from it. These checks pin the palette, the ban on
// cyberpunk treatments, and the ban on decorative shadows to real files.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { DARK_TOKENS, LAYOUT, LIGHT_TOKENS } from "../tokens";

const read = (relative: string): string =>
  readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf8");

const STYLES = read("../styles.css");
const DESIGN = read("../../DESIGN.md");
const CORE = read("../core/jarvis-core.ts");
const HOLOGRAM = read("../knowledge/hologram.ts");
const TOKENS_SOURCE = read("../tokens.ts");

const hexCodes = (text: string): string[] =>
  [...new Set(text.match(/#[0-9A-Fa-f]{6}\b/g) ?? [])].map((value) => value.toUpperCase());

const paletteLine = (label: string): string[] => {
  const line = DESIGN.split("\n").find((candidate) => candidate.startsWith(`${label}:`));
  if (!line) throw new Error(`DESIGN.md is missing the ${label} palette line`);
  return hexCodes(line);
};

const DESIGN_LIGHT = paletteLine("Light");
const DESIGN_DARK = paletteLine("Dark");
const DESIGN_PALETTE = new Set([...DESIGN_LIGHT, ...DESIGN_DARK]);

const linearChannel = (value: number): number => {
  const channel = value / 255;
  return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
};

const luminance = (hex: string): number => {
  const channels = [1, 3, 5].map((index) => linearChannel(Number.parseInt(hex.slice(index, index + 2), 16)));
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
};

const contrastRatio = (first: string, second: string): number => {
  const [lighter, darker] = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (lighter + 0.05) / (darker + 0.05);
};

const composite = (foreground: string, background: string, alpha: number): string => {
  const channels = [1, 3, 5].map((index) => {
    const front = Number.parseInt(foreground.slice(index, index + 2), 16);
    const back = Number.parseInt(background.slice(index, index + 2), 16);
    return Math.round(front * alpha + back * (1 - alpha)).toString(16).padStart(2, "0");
  });
  return `#${channels.join("")}`.toUpperCase();
};

describe("design contract", () => {
  it("DESIGN.md light palette matches LIGHT_TOKENS", () => {
    expect([...DESIGN_LIGHT].sort()).toEqual(
      hexCodes(Object.values(LIGHT_TOKENS).join(" ")).sort()
    );
  });

  it("DESIGN.md dark palette matches DARK_TOKENS", () => {
    expect([...DESIGN_DARK].sort()).toEqual(
      hexCodes(Object.values(DARK_TOKENS).join(" ")).sort()
    );
  });

  it("styles.css declares exactly the token-module palette for both schemes", () => {
    const lightBlock = STYLES.slice(STYLES.indexOf(":root"), STYLES.indexOf("@media"));
    const darkBlock = STYLES.slice(STYLES.indexOf("@media"));
    expect(hexCodes(lightBlock).sort()).toEqual(
      hexCodes(Object.values(LIGHT_TOKENS).join(" ")).sort()
    );
    expect(hexCodes(darkBlock).sort()).toEqual(
      hexCodes(Object.values(DARK_TOKENS).join(" ")).sort()
    );
  });

  it("tokens.ts defines no color outside the DESIGN.md palette", () => {
    expect(hexCodes(TOKENS_SOURCE).filter((value) => !DESIGN_PALETTE.has(value))).toEqual([]);
  });

  it("styles.css introduces no color outside the DESIGN.md palette", () => {
    expect(hexCodes(STYLES).filter((value) => !DESIGN_PALETTE.has(value))).toEqual([]);
  });

  it("uses readable champagne text on light and dark selected surfaces", () => {
    const lightSelection = composite(LIGHT_TOKENS.accent, LIGHT_TOKENS.surface, 0.11);
    const darkSelection = composite(DARK_TOKENS.accent, DARK_TOKENS.surface, 0.13);
    expect(contrastRatio(LIGHT_TOKENS.accentInk, lightSelection)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(DARK_TOKENS.accentInk, darkSelection)).toBeGreaterThanOrEqual(4.5);
    expect(STYLES).not.toMatch(/color:\s*var\(--accent\)\s*[!;]/);
    expect(STYLES).toContain("--selection: rgba(184, 138, 69, 0.11)");
    expect(STYLES).toContain("--selection: rgba(213, 179, 110, 0.13)");
  });

  it("the shell and the 3D core introduce no color outside the DESIGN.md palette", () => {
    const offenders = [...hexCodes(CORE), ...hexCodes(HOLOGRAM)].filter(
      (value) => !DESIGN_PALETTE.has(value)
    );
    expect(offenders).toEqual([]);
  });

  it("no neon, bloom, glow, or cyberpunk treatment is applied", () => {
    const surfaces: Array<[string, string]> = [
      ["styles.css", STYLES],
      ["core/jarvis-core.ts", CORE],
    ];
    for (const [name, source] of surfaces) {
      const match = source.match(/neon|bloom|glow|cyberpunk|drop-shadow/i);
      expect(match?.[0], `${name} must not use ${match?.[0]}`).toBeUndefined();
    }
  });

  it("every box-shadow declaration is none, so no card carries a decorative shadow", () => {
    const declarations = [...STYLES.matchAll(/box-shadow:\s*([^;]+);/g)].map((match) =>
      match[1].trim().toLowerCase()
    );
    expect(declarations.length).toBeGreaterThan(0);
    expect(declarations.filter((value) => value !== "none")).toEqual([]);
  });

  it("uses the settings window width on desktop and keeps a single column on narrow screens", () => {
    expect(LAYOUT.settingsMaxWidth).toBe(912);
    expect(STYLES).toContain("width: min(912px, calc(100% - 48px))");
    expect(STYLES).toMatch(/@media \(min-width: 800px\)[\s\S]*grid-template-columns: minmax\(0, 58fr\) minmax\(300px, 42fr\)/);
    expect(STYLES).toMatch(/@media \(max-width: 799px\)[\s\S]*\.settings-shell \{ width: calc\(100% - 32px\)/);
    expect(STYLES).toContain("font-size: 16px; line-height: 1.3");
    expect(STYLES).toContain("font-size: 14px; line-height: 1.5");
    expect(STYLES).toContain("#knowledge-graph-canvas { height: 230px; }");
  });
});
