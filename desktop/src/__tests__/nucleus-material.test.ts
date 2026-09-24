import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { createNucleusMaterial } from "../core/nucleus-material";

describe("Jarvis nucleus shader", () => {
  it("uses a private warm accent uniform and fixed physical lighting", () => {
    const accent = new THREE.Color("#B88A45");
    const material = createNucleusMaterial(accent);

    expect(material).toBeInstanceOf(THREE.ShaderMaterial);
    expect(material.name).toBe("JarvisNucleus");
    expect(material.uniforms.accent.value).not.toBe(accent);
    expect(material.uniforms.accent.value.equals(accent)).toBe(true);
    expect(material.lights).toBe(false);
    expect(material.fragmentShader).toContain("BRDF_GGX");
    expect(material.fragmentShader).toContain("#include <colorspace_fragment>");

    material.dispose();
  });
});
