// The design contract in desktop/DESIGN.md is only binding if something fails
// when the code drifts away from it. These checks pin the palette, the ban on
// cyberpunk treatments, and the ban on decorative shadows to real files.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { DARK_TOKENS, LIGHT_TOKENS } from "../tokens";

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
});
