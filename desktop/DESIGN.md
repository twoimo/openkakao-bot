# Jarvis desktop design contract

## Experience

Jarvis should feel like a quiet instrument panel: warm, legible, and composed. A new user should understand each setting without knowing model, retrieval, or runtime terminology. Status copy gives one useful next step; diagnostic detail stays in developer documentation. The trade-off is deliberate: expert users see fewer live counters in the settings window.

## Decision table

| Constraint | Decision | Review check |
| --- | --- | --- |
| First-use comprehension | Plain Korean labels and one short status per task | User-facing settings contain no model IDs or diagnostic acronyms. |
| Visual character | Ivory and warm black surfaces; champagne marks selection; amber marks caution | No neon, bloom, glow, or decorative shadow. |
| Reading width | 960px window, 912px content area, 58:42 columns with a 16px gap; one column below 700px | Long labels wrap without forcing horizontal scrolling; the settings window uses its available width. |
| Live panel geometry | 276×260 panel, 12 inset, 236 core | Main panel constants are tested. |
| Main-panel hierarchy | Spherical Jarvis core only; settings open from a right-click on the menu-bar tray icon | No health/jobs/bulk/permission chrome. |
| Settings | One unified 960×880 window; two-column desktop grid and a single narrow-screen column | Rooms and AI answers, then voice and conversation status, then conversation search beside recent replies. |
| Motion | Physical damping and capped rendering | Idle ≤15fps, busy ≤30fps, dt clamp, hidden/close/lock stop. |
| Color | Warm neutral canvas/surface with restrained gold | Champagne/gold is reserved for core and selection; amber is warning. |
| Retrieval language | Knowledge GraphRAG shell only | Hash-cosine is never labeled RRF. |

The decision → token → component → rendered-review sequence follows the contract-first approach in [oh-my-design](https://github.com/kwakseongjae/oh-my-design). The layout and motion review also draws on the previously reviewed [style.gallery](https://style.gallery/) reference. These sources guide the method; no third-party visual style is copied verbatim.

## Tokens

Light: canvas `#F7F4EE`, surface `#FFFCF7`, text `#241F1A`, muted `#6D655B`, champagne `#B88A45`, warning `#92540E`.

Dark: canvas `#12100D`, surface `#191611`, text `#F1EBE1`, muted `#B7AEA1`, gold `#D5B36E`, amber `#E5A04B`.

Spacing uses 4/8/12/16/24px steps. Settings cards use an 11px radius, a 1px warm border, and no decorative shadow.

## Component contract

- `JarvisPanel`: opaque 276×260 root. The Three.js canvas is transparent and exactly 236×236. The panel has no interactive controls; settings open from a right-click on the menu-bar tray icon.
- `JarvisCore`: three independently damped gimbal rings, 96 neuron points, three synapses per neuron, 30 particles, a spring nucleus, and an acoustic wire lattice. GPU buffers are allocated once and updated in place.
- `UnifiedSettings`: target rooms, two plain-language AI choices, voice start, one conversation status, holographic conversation search, and recent replies. There are no bulk-verification, feature-checklist, permission, model-owner, hardware, index, or training-status controls in this window.
- AI models: Flash-Next is the default resident choice; the 27B model is shown as an on-demand swap target and is never prepared or loaded by this unit.

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
- Settings use a 58:42 split with a 16px gap in the 960px desktop window and return to one column below 700px.
- The conversation graph shares its row with recent replies; narrow layouts keep the graph readable and stack the sections.
- Knowledge copy uses everyday Korean and does not expose retrieval implementation details.
