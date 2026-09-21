# Jarvis desktop design contract

## Decision table

| Constraint | Decision | Review check |
| --- | --- | --- |
| Live panel geometry | 276×260 panel, 12 inset, 236 core, 28 gear | Main panel constants are tested. |
| Main-panel hierarchy | Spherical Jarvis core plus one top-right gear control | No health/jobs/bulk/permission chrome. |
| Settings | One unified, single-column window, max 720px | Rooms → AI model → voice → knowledge → history. |
| Motion | Physical damping and capped rendering | Idle ≤15fps, busy ≤30fps, dt clamp, hidden/close/lock stop. |
| Color | Warm neutral canvas/surface with restrained gold | Champagne/gold is reserved for core and selection; amber is warning. |
| Retrieval language | Knowledge GraphRAG shell only | Hash-cosine is never labeled RRF. |

`https://style.gallery/` was re-fetched on 2026-09-20 (HTTP 200) and inspected before implementation. The page's Layout, Motion, Design Engineering, and platform-comparison references informed the compact hierarchy and motion review; no external visual style was copied verbatim.

## Tokens

Light: canvas `#F7F4EE`, surface `#FFFCF7`, text `#241F1A`, muted `#6D655B`, champagne `#B88A45`, warning `#92540E`.

Dark: canvas `#12100D`, surface `#191611`, text `#F1EBE1`, muted `#B7AEA1`, gold `#D5B36E`, amber `#E5A04B`.

Spacing uses 4/8/12/16/24px steps. Settings cards use an 11px radius, a 1px warm border, and no decorative shadow.

## Component contract

- `JarvisPanel`: opaque 276×260 root. The Three.js canvas is transparent and exactly 236×236. `gear` is the only interactive control.
- `JarvisCore`: three independently damped gimbal rings, 96 neuron points, three synapses per neuron, 30 particles, a spring nucleus, and an acoustic wire lattice. GPU buffers are allocated once and updated in place.
- `UnifiedSettings`: rooms, AI model, voice, knowledge, history. `settings-sync-card` precedes `settings-dream-rsi-card`. Existing AX ids are preserved as DOM ids.
- AI models: Flash-Next is the default resident choice; the 27B model is shown as an on-demand swap target and is never prepared or loaded by this unit.
- DREAM-RSI: checkpoint provenance read from `--action dream-rsi-status`; it is not presented as a trainer.

## Bundle contract

The primary bundle is `OpenKakao Jarvis.app` with identifier
`com.openkakao.jarvis.desktop`; `LSUIElement=true` keeps it menu-bar-only.
Signing identity is intentionally not hardcoded in source. The packaging
script accepts an explicitly supplied `OPENKAKAO_SIGN_IDENTITY`; local builds
remain unsigned when it is absent. The former `com.openkakao.auto-reply.menu`
Swift Extra is a separately named legacy path and is stopped before the Tauri
LaunchAgent is bootstrapped.

## Render review

- Opaque warm panel, transparent WebGL canvas only; no transparent window.
- No cyberpunk neon, bloom, glowing text, or Gemma recommendation.
- Core motion is legible at low frame rates and resumes without a jump.
- Settings remain one column and keep sync before DREAM-RSI.
- Knowledge copy says GraphRAG/drilldown readiness and does not claim BM25+Dense+RRF.
