import * as THREE from "three";

/**
 * The nucleus has one fixed white key and ambient fill; every other core
 * material is unlit. Keep its standard-material BRDF, but express those two
 * lights here so the renderer need not collect and update scene lights.
 * Colors arrive in Three's linear working space. No time/load uniform is
 * needed: the existing render state still owns all nucleus motion.
 */
export function createNucleusMaterial(accent: THREE.Color): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    name: "JarvisNucleus",
    uniforms: { accent: { value: accent.clone() } },
    vertexShader: /* glsl */`
      varying vec3 vNormal;
      varying vec3 vViewPosition;

      void main() {
        vNormal = normalize(normalMatrix * normal);
        vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
        vViewPosition = -mvPosition.xyz;
        gl_Position = projectionMatrix * mvPosition;
      }
    `,
    fragmentShader: /* glsl */`
      uniform vec3 accent;
      varying vec3 vNormal;
      varying vec3 vViewPosition;

      #include <common>
      #include <lights_physical_pars_fragment>

      void main() {
        vec3 normal = normalize(vNormal);
        vec3 viewDir = normalize(vViewPosition);
        vec3 lightDir = transformDirection(vec3(2.0, 3.0, 4.0), viewMatrix);

        // Match MeshStandardMaterial's geometric roughness and metalness.
        vec3 dxy = max(abs(dFdx(normal)), abs(dFdy(normal)));
        PhysicalMaterial material;
        material.roughness = min(0.7 + max(max(dxy.x, dxy.y), dxy.z), 1.0);
        material.diffuseColor = accent * (1.0 - 0.08);
        material.specularColor = mix(vec3(0.04), accent, 0.08);
        material.specularF90 = 1.0;

        vec3 diffuse = BRDF_Lambert(material.diffuseColor);
        float irradiance = 1.8 * saturate(dot(normal, lightDir));
        vec3 outgoingLight = 1.35 * diffuse
          + irradiance * (diffuse + BRDF_GGX(lightDir, viewDir, normal, material));
        gl_FragColor = vec4(outgoingLight, 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }
    `,
  });
}
