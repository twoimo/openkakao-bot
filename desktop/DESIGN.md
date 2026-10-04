# Alden desktop design contract

## 0.3.12 — Dynamic neural graph

The knowledge graph keeps the navy palette and silver/gold materials, with a quiet folded cortical backdrop. Confirmed relationships form curved synaptic bridges: new bridges grow from their endpoints, withdrawn ones retract, and strength controls width and spring rest length. Stable IDs preserve positions through source updates. These are display mechanics, not a biological simulation or a learning-status indicator.

Visible knowledge maintains small node motion and restrained bridge flow at a 30 fps cap. The selected node and root stay steady for reading. Physics stops after convergence while the presentation remains alive. Hidden, closed and locked surfaces stop rendering and revision probes. Reduced motion and the emergency latch use a static frame. Runtime/voice orbs continue to depend on actual activity. Earlier idle-zero measurements describe prior versions.

The title, count and last update occupy one 20px row. Camera transitions use time-based damping at 12/s; bridge growth uses a 0.16s time constant. Overview targets use stable anchors so Back restores the same view while positions remodel. One instanced bridge geometry replaces per-edge lines, with 24 visible nodes and 144 active bundles. [Research, equations and measurements](../docs/architecture/alden-neural-plasticity-20261003.md) distinguish browser fixtures, installed native windows and remaining physical checks.

## 0.3.11 — Compact workspaces

All five sidebar pages share the existing navy observatory palette and restrained silver/gold orbs. The sidebar is 180px, 145px at the narrow breakpoint; navigation remains 40px high. Redundant topbar space is removed. Conversation and voice history open their most recent readable records, with explicit loading, empty and retry states. The latest message stays fully visible when pagination controls or the viewport change. Historical collection steps use nouns; only the current status claims ongoing work. Voice controls live beside voice history. Automation drafts survive navigation; only verified saves close the editor, with the result shown outside it.

Overview camera distance is fitted to the current perspective viewport and enclosing node boxes on resize. Label exclusion rectangles are cached on resize/selection; no new geometry reads run per frame. Keyboard node navigation remains available. Diagnostics, model IDs and benchmark text stay outside product settings. Actual voice readiness is displayed honestly.

Current tokens: canvas `#0b1322`, surface `#121e30`, text `#e7edf5`, muted `#a5b2c7`, border `#2d3b50`, selection `#20334c`. [Five-page audit and measurement](../docs/architecture/alden-sidebar-audit-20261003.md) document fixture and native scope separately. Earlier sections record prior versions.

## 0.3.8 — Thinking Orbs and selected-node evidence

[Thinking Orbs](https://libraries.dev/orbs) is the signature material: depth-shaded silver dots, distinct work states, quiet surroundings. Alden uses the official `thinking-orbs/engine` entry point, pinned to 0.3.2 (MIT), with its 20px/64px presets. React is a package peer but is not imported by the renderer or included in Alden's UI bundle. The complete license ships as `thinking-orbs-LICENSE.txt` in the frontend assets. No Studio exports or paid assets are used.

The navy observatory palette and Georgia wordmark remain. The sidebar brand is 48px, inline state orbs 20px, and the popover brand 28px. Collection nodes and the selected item use upstream projected geometry anchored to their real Three.js positions. Other people/rooms/topics retain their existing sphere glyphs. Decorative orb dots have no graph IDs or relationships; only the existing node sphere is a picking target. Labels sit below the larger glyphs.

Actual fresh voice, pipeline, database and job states select the orb. Wake waiting, stale/error/unavailable data and quiet operation freeze it. The emergency latch takes precedence, including in the popover. A short plain-language caption supplies the state without relying on motion. Reduced motion uses a static representative frame. Every window shares one capped canvas-orb loop plus its existing graph loop and status readers; no per-orb timers are added.

The official engine builds a bounded 96-frame sequence when a state is prepared. Packed Float32 frames are cached by state and canonical size; subsequent ticks reuse them and the Three.js buffers. Time advances only while work is visible, at most 30 rendered frames/s, and reverses the 3.17-second sequence at its endpoints to avoid a wrap jump. This adapts the upstream animation clock; it is not an audio spectrum or a measure of task completion. Stop/close/lock removes RAF and owned status timers. Static label bounds are retained; stable and collided labels no longer toggle DOM attributes every frame.

The selected-node card separates kind, full stored description, key facts, connections and up to 6 original excerpts. Each excerpt retains its room, sender, date, source ID and outgoing/system classification. Counts describe representative excerpts, not the complete corpus. Actor identity, account and selected room are checked in the producer, native bridge and renderer; cached samples from another room are not used. A missing original record is stated explicitly. Stored notes and structural group descriptions remain distinguishable from quoted conversation evidence. No model call or reindex is started to author a profile at click time.

Source browser evidence: three 1-second active samples reduced repeated label mutations 138→0 each; graph/canvas-orb idle draws 0/0 in three 1-second samples. Synthetic blur stopped both loops with 0 later draws; five reopens retained one pending callback per loop. These measurements concern the source browser fixture, not whole-app power, physical Retina or the primary Tauri process. See the 0.3.8 delivery evidence for installation and native observations.

## 0.3.7 — rendered playback PCM

The visible graph reads a small native voice/latch status at 100ms while voice is available, 750ms otherwise. Its own generation fence discards late responses after hide or reopen. Full runtime snapshots retain their 2.5-second cadence and supply job load. Native status reads invoke no Python or model. Only `user_listen` selects input RMS; `speaking` selects output PCM RMS, including zero during synthesis before playback. The five-second wake heartbeat does not animate idle noise.

Output PCM uses clipped source samples and the actual AVAudioPlayerNode cursor: only completed 20ms windows can be displayed. This is PCM before mixer/device volume, not perceived loudness. Ticket/generation, valid render time and a 200ms host-age fence exclude stale playback; the emergency latch masks both channels. The producer reads its native cursor only when its 10Hz status publication is due. It never manufactures a phase from an inference timer.

The retained 128-point envelope keeps the 0.12s damper, 0.0001 settling tolerance, 0.12 radius displacement cap and 0.36 opacity cap. Measured speech/fade frames cap at30fps; load/camera frames retain the existing policy. Settled state submits no frames even at positive RMS. Hide/close clears source, amplitude, polling and RAF; empty source graphs stop amplitude polling. The historical 0.3.6 input-only contract below describes that earlier release.

## 0.3.6 — sampled voice amplitude

The current graph and popover project recent microphone RMS into one 128-vertex orbital envelope. RMS is unitless in [0,1]; only available, error-free `wake_listen`/`user_listen` status at most 3 seconds old is accepted. Stored input during `speaking` is not output amplitude. Runtime status is sampled every 2.5 seconds while the graph surface is visible, so this is a sampled envelope rather than a real-time spectrum. Actual output PCM amplitude remains an unverified follow-up.

The displayed level follows `s += (1-exp(-dt/0.12))* (RMS-s)`, with `dt` in seconds and a 0.0001 settling tolerance. Strength is `sqrt(s)`, radial displacement is at most 0.12 scene units around radius 1.95, and opacity is at most 0.36. There is no autonomous phase or pulse. The same Float32Array, geometry and material are updated in place. Once camera and amplitude settle, the loop stops even at a positive level. Silence hides the envelope; hide/close/lock clears it, cancels polling and prevents late signals from restarting rendering. The decorative envelope has no graph ID or picking target. The previous core motion formulas below belong to the retained legacy component, not this graph.

## 2026-10-02 — knowledge observatory

The user requested a universe atmosphere for the graph and sidebar. Both windows use a quiet navy observatory: a static distant star field, thin orbital guides and the actual knowledge relationships as constellations. The sidebar shares the night palette, legible silver labels and a clear selected menu. Its operating control and centered bottom version retain their behavior and location.

Six primary roles: midnight canvas `#080E1C`, blue-black surface `#121D30`, starlight text `#E7EDF5`, silver secondary text `#A5B2C7`, ice-blue people `#A2C3DF`, warm-ivory rooms `#E6C797`. Topic nodes use a quiet lavender auxiliary tint. Georgia remains the Alden wordmark; system Korean text stays readable at the existing sizes. No luminous text, postprocessing, fabricated relationships or animated particle workload is introduced.

Cosmos: canvas `#080E1C`, surface `#121D30`, text `#E7EDF5`, muted `#A5B2C7`, nucleus `#DDE7EF`, people `#A2C3DF`, rooms `#E6C797`, topics `#BCB7D5`, links `#7894B1`, distant cool stars `#CAD8E9`, distant warm stars `#E6CFAC`, orbital guides `#6883A3`.

The backdrop owns at most 1,536 static points in one geometry and two orbital lines. It is outside graph data, keyboard navigation and picking. All retained Points, Line and Mesh resources must be disposed with their owning window. Settled scenes stop drawing; source, resize, load and interaction changes invalidate a frame. Camera travel retains the existing capped loop. The 24-node foreground budget, source identities, interaction controls and native hidden/locked lifecycle remain authoritative. Default, minimum, expanded and popover layouts require real browser rendering and separate installed evidence.

## 2026-10-01 — wide settings workspace

The user requested a full settings redesign, a wide rectangular window, and explicitly allowed a palette beyond champagne gold. The selected reference is [Fieldwork · A workspace that remembers](https://style.gallery/components/21st-49e34572c05d/), reviewed in its running example on Style Gallery. Its independently authored MIT study supplies the layout direction: a quiet fixed sidebar, an independent scroll area, selected-view feedback, thin separators and a restrained linen/sage surface. The product's existing controls and real backend remain authoritative; example accounts, project metrics and dates are not copied.

Default settings window: **1200×760**, minimum **640×680**. The default view is the knowledge graph, filling the main area beside the sidebar. The sidebar has knowledge graph, KakaoTalk history, voice history, DB updates, and Settings. Settings is a page, with no gear launcher or modal. A selected node opens a floating detail panel; overview closes it. Labels are local DOM text projected onto the bounded Three.js scene. OSK automatically maintains the private knowledge vault while Alden runs. Keyboard arrows/Home/End move selection and focus together. Each view retains its scroll position. The memory renderer runs only when both its view and the native window are visible; switching views never invents activity.

Settings light tokens: canvas `#FAFBF6`, sidebar `#F0F3E9`, surface `#FFFFFF`, text `#273329`, muted `#697361`, sage `#657E50`, accent ink `#3F5630`, selected surface `#DFE8D0`, border `#E1E5D9`. Muted text contrast on canvas is **4.78:1**; selected text contrast is **6.42:1**. Dark surfaces use charcoal greens and muted sage with the same hierarchy. The left-click panel now uses the same real 3D graph at 560×420, with orbit controls and a bounded node budget.

The wordmark uses the system Georgia serif and controls use existing local system sans fonts. No external font request is required. [Lucide1.49.0](https://lucide.dev/) supplies icons; ISC/Feather notices are included in the source and preserved in the built JavaScript. UI assets are local, and no neon effects or added shader background is introduced.


The Settings page opens with the automation registry. New/edit opens a contextual editor beside the list; narrow windows stack it. Two compact answer choices and voice controls follow. Current operating status is a disclosure. This replaces the duplicated registration controls and the settings drawer. Save/delete require persisted catalog readback; uncertain writes are not repeated. Catalog edits apply when automation restarts; this UI does not adopt or kill existing foreground workers.

KakaoTalk history uses room-isolated keyset pages, fixed anchors, string IDs and untruncated text. A measured virtual list bounds live DOM rows at 80. Voice history stores confirmed user/assistant text by session; final cancellation fences precede assistant journaling. DB history retains older pages through polling and pauses reads when hidden. Raw DB+WAL collection counts and the existing search corpus counts are distinct. OSK nodes preserve their IDs through real API moves into KakaoTalk / rooms / people / topics; equal names never merge identities.

The new [record and knowledge flow](../docs/architecture/alden-history-osk-20261002.html) was generated with Archify. Authored labels are Korean; its fixed viewer controls and HTML language fall back to English.

The older contracts below describe the preceding settings layout and remain historical references for the panel, runtime and motion rules. The new wide settings geometry/view grouping supersedes their 960px card split.

## Experience

Alden should feel like a quiet instrument panel: warm, legible, and composed. A new user should understand each setting without knowing model, retrieval, or runtime terminology. Status copy gives one useful next step; diagnostic detail stays in developer documentation. The trade-off is deliberate: expert users see fewer live counters in the settings window.

## Decision table

| Constraint | Decision | Review check |
| --- | --- | --- |
| First-use comprehension | Plain Korean labels and one short status per task | User-facing settings contain no model IDs or diagnostic acronyms. |
| Visual character | Ivory and warm black surfaces; champagne marks selection; amber marks caution | No neon, bloom, glow, or decorative shadow. |
| Reading width | 960px window, 912px content area, golden-ratio 61.8:38.2 columns with a 16px gap; one column below 800px | The 38.2% side retains its 300px minimum at the two-column breakpoint; long labels wrap without forcing horizontal scrolling. |
| Live panel geometry | 276×260 panel, 12 inset, 236 core | Main panel constants are tested. |
| Main-panel hierarchy | Spherical Alden core only; settings open from a right-click on the menu-bar tray icon | No health/jobs/bulk/permission chrome. |
| Settings | One unified 960×880 window; two-column desktop grid and a single narrow-screen column | Rooms and AI answers, then voice and conversation status, then conversation search beside recent replies. |
| Motion | Per-source angular velocity, voice-aware global load, phase-integrated pulse, bounded spring substeps | Idle ≤15fps, active ≤30fps, frame dt ≤250ms, hidden/close/lock cancels RAF. |
| Color | Warm neutral canvas/surface with restrained gold | Champagne/gold is reserved for core and selection; amber is warning. |
| Retrieval language | Knowledge GraphRAG shell only | Hash-cosine is never labeled RRF. |

The decision → token → component → rendered-review sequence follows the contract-first approach in [oh-my-design](https://github.com/kwakseongjae/oh-my-design). The layout and motion review also draws on the previously reviewed [style.gallery](https://style.gallery/) reference. These sources guide the method; no third-party visual style is copied verbatim.

## Tokens

Light: canvas `#F7F4EE`, surface `#FFFCF7`, text `#241F1A`, muted `#6D655B`, champagne `#B88A45`, accessible champagne text `#73501F`, warning `#92540E`.

Dark: canvas `#12100D`, surface `#191611`, text `#F1EBE1`, muted `#B7AEA1`, gold and its accessible text color `#D5B36E`, amber `#E5A04B`.

Small champagne-colored text uses the accessible text token while borders, fills, and motion marks keep the brighter accent. On the light selection surface `#F7EFE3`, the accessible text contrast is **6.36:1** (the previous `#B88A45` text was **2.72:1**).

Spacing uses 4/8/12/16/24px steps. Settings cards use an 11px radius, a 1px warm border, and no decorative shadow.

The desktop settings split uses the golden ratio `φ = (1 + √5) / 2 ≈ 1.618`: the main column receives `1/φ ≈ 61.8%` of the available tracks and the secondary column receives `1/φ² ≈ 38.2%`. The secondary column keeps a 300px minimum for readable controls, so the split yields to that minimum when the window is near the 800px breakpoint.

## Component contract

- `AldenPanel`: opaque 276×260 root. The Three.js canvas is transparent and exactly 236×236. The panel has no interactive controls; settings open from a right-click on the menu-bar tray icon.
- `AldenCore`: three independently damped gimbal rings, 96 neuron points, three synapses per neuron, 30 particles, a spring nucleus, and an acoustic wire lattice. GPU buffers are allocated once and updated in place.
- `UnifiedSettings`: target rooms, two plain-language AI choices, voice start, one conversation status, holographic conversation search, and recent replies. There are no bulk-verification, feature-checklist, permission, model-owner, hardware, index, or training-status controls in this window.
- AI models: the existing ready Qwen3.8 27B service is the verified local voice choice. The exact Flash-Next iQ option is admitted only with 60 GiB of reclaimable memory and a matching ready catalog entry. The current host does not meet that budget; the UI must not imply that Flash is resident or available. A second resident model is not started just to populate the chooser.

## Motion model

Every activity input is clamped to `L ∈ [0, 1]`. For ring `i`,
`ωᵢ* = bᵢ (1 + gᵢ ℓᵢ) (1 + 0.35 L)`, where
`b = [0.17, -0.12, 0.09] rad/s`, `g = [1.4, 1.65, 1.9]`, and
`ℓ = [reply, GeekNews, DB sync]`. `L` is the maximum of job, background, and
voice activity; voice increases global motion but does not claim a ring. Ring
velocity follows the exact exponential damper
`ωᵢ ← ωᵢ + (ωᵢ* - ωᵢ)(1 - e⁻ᵏⁱᵈᵗ)`, with `k = [1.8, 2.35, 2.9] s⁻¹`.
Rotation integrates the preserved elapsed frame time, bounded at 250ms; nucleus
spring integration subdivides that time into steps of at most 50ms.

The lattice frequency is `f = 1 + 0.8L Hz`, amplitude is `0.05 + 0.05L`,
and opacity is `0.06 + 0.12L`. Its phase accumulates `φ ← (φ + 2πf·dt) mod 2π`
so changing load does not reset the waveform. A single 1,344-vertex sphere
buffer is packed into draw ranges of 448, 896, and 1,344 vertices; normalized
load thresholds 0.22 and 0.62 select the density. Frame updates only change the
draw range when the tier changes; they do not construct geometry.

## Bundle contract

The primary bundle is `Alden.app` with identifier
`com.openkakao.alden.desktop`; `LSUIElement=true` keeps it menu-bar-only.
Signing identity is intentionally not hardcoded in source. The packaging
script accepts an explicitly supplied `OPENKAKAO_SIGN_IDENTITY`; local builds
remain unsigned when it is absent. The former `com.openkakao.auto-reply.menu`
Swift Extra is a separately named legacy path and is stopped before the Tauri
LaunchAgent is bootstrapped.

## Render review

- Opaque warm panel, transparent WebGL canvas only; no transparent window.
- No cyberpunk neon, bloom, glowing text, or Gemma recommendation.
- Core motion is legible at low frame rates and resumes without a jump.
- Settings use the golden-ratio 61.8:38.2 split with a 16px gap in the 960px desktop window and return to one column below 800px, preserving the 300px minimum for the right column.
- The conversation graph shares its row with recent replies; narrow layouts keep the graph readable and stack the sections.
- Knowledge copy uses everyday Korean and does not expose retrieval implementation details.

## Corpus-backed graph and timeline

0.3.0 uses a resumable account-scoped corpus populated from a consistent encrypted DB+WAL snapshot. Working and published SQLite databases are separate. A complete publication invalidates the graph cache; actual numeric author IDs identify people across rooms. Same names never establish identity. Snapshot evidence records observed source rows and is separate from decision-ledger evidence. Focus lookup returns scoped quoted history, with unclassified outgoing history marked explicitly. Dense/model inference coverage is not implied by raw full-text coverage.

DB history resets its oldest-page cursor when a newly fetched page does not overlap the prior window after a long hidden interval. All loaded rows remain available while the live DOM is bounded. Row keys preserve reading position through append, prepend and height measurement. Hidden views cancel their animation requests and observers.
