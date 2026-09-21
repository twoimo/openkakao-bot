import { describe, expect, it } from "vitest";
import type { BackgroundStatus } from "../contracts";
import { latticePulse, ringTargetVelocities, sourceLoads } from "../core/load-mapping";

function background(overrides: Partial<BackgroundStatus> = {}): BackgroundStatus {
  return {
    activity: 0,
    caption: "",
    replyLoad: 0,
    geeknews: { state: "unknown", activity: 0, caption: "" },
    dbSync: { state: "unknown", activity: 0, caption: "" },
    ...overrides,
  };
}

describe("source load mapping", () => {
  it("maps each safe background source independently", () => {
    expect(sourceLoads(background({
      activity: 0.9,
      replyLoad: 0.2,
      geeknews: { state: "sending", activity: 0.5, caption: "" },
      dbSync: { state: "syncing", activity: 0.7, caption: "" },
    }))).toEqual({ reply: 0.2, geeknews: 0.5, dbSync: 0.7, total: 0.9 });
  });

  it("fails closed for non-finite and out-of-range loads", () => {
    expect(sourceLoads(background({
      activity: Number.NaN,
      replyLoad: 4,
      geeknews: { state: "unknown", activity: -2, caption: "" },
      dbSync: { state: "unknown", activity: Number.POSITIVE_INFINITY, caption: "" },
    }))).toEqual({ reply: 1, geeknews: 0, dbSync: 0, total: 0 });
  });

  it("drives rings from reply, GeekNews, and DB sync loads and preserves base at zero", () => {
    const base = [0.17, -0.12, 0.09];
    const gain = [1.4, 1.65, 1.9];
    expect(ringTargetVelocities(base, { reply: 0, geeknews: 0, dbSync: 0, total: 0 }, gain)).toEqual(base);
    const differentiated = ringTargetVelocities(
      base,
      { reply: 1, geeknews: 0.5, dbSync: 0.25, total: 0.8 },
      gain,
    );
    expect(differentiated[0]).toBeCloseTo(0.17 * 2.4);
    expect(differentiated[1]).toBeCloseTo(-0.12 * 1.825);
    expect(differentiated[2]).toBeCloseTo(0.09 * 1.475);
  });

  it("keeps lattice pulse parameters bounded and deterministic", () => {
    expect(latticePulse(-4, 0)).toEqual({ frequency: 1, amplitude: 0.05, opacity: 0.06 });
    expect(latticePulse(1, 10)).toEqual({ frequency: 1.8, amplitude: 0.1, opacity: 0.18 });
    expect(latticePulse(5, 999)).toEqual(latticePulse(1, 1));
  });
});
