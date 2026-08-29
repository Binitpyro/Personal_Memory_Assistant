# Design System — Specimen Cabinet

Reference document for AI-assisted engineering on the frontend.

**Last verified against source: 2026-08-29, commit `3e186e9`, branch `updates`, working tree clean.** Every ratio below was computed this session against the values that actually ship in `frontend/src/index.css`, not carried over from a plan. Anything here that a source read contradicts is stale — re-verify before acting on it.

---

## 1) What this replaced, and why it was replaced twice

`UI_UX_AUDIT.md` (in the repo root) found 58 dead utility usages across 22 class names, four status colours failing WCAG AA, zero `font-family` declarations, and a four-class "design system" welded shut with ten `!important`.

Two directions were built and rejected before this one. Both rejections were correct and both are worth remembering:

1. **Violet/indigo with glass and gradient glow — rejected as AI slop.** The purple was *inherited*, not chosen: `--color-primary` was `#3d15cb` and the Crystal shaders were a violet night sky. The two agreeing was treated as evidence the direction was right; it only proved they were consistent. The register was also wrong, not just the hue — glow and floating translucent panels are the visual language of cloud intelligence, and PMA reads your own files on your own machine.
2. **Archival palette on the untouched app skeleton — rejected as "just dark mode".** Also correct. A five-item icon rail, a top status strip, a four-column stat grid and a bottom composer, repainted warm brown, is a dark mode with a tint. `#1C1815` at that luminance is not perceived as wood.

**The lesson that survived both:** the metaphor has to be carried by *layout*. Palette alone cannot do it. And the opposite failure — drawing wood grain and bevelled brass — is worse, because it dates on sight. **Structural, never textural.**

One thing was rescued rather than discarded: the original `--color-surface: #959f93` (Ash Grey) and `--panel-bg: rgba(241,245,224,.90)` (Translucent Beige) were bookcloth linen and aged paper. The archival instinct in the original code was right; it failed on luminance, not on concept.

---

## 2) Direction

**Purpose.** The user is getting *their own material* back — consulting an archive they own, not conjuring intelligence.

**Tone.** Archival / natural-history with a technical undertone. Ebonised wood, brass fittings, glass vitrine, ink labels, lamp light.

**Colour world.** Ink `#14110E` · brass `#B08D57` · bone `#EDE6D8` · oxblood `#7B2D26` · verdigris `#4A6B5D`. Ground neutrals are **warm brown-black, never blue-black** — blue-black reads tech, brown-black reads wood and leather.

**Three signatures** (things only this product would do):

1. **Marginalia** — the answer is the text, its sources are the margin. Implemented in `MessageBubble.tsx`: sources render under a ruled `Provenance` column as shelf marks.
2. **The brass plate** — exactly one primary action per surface, and it is the only thing that rises off the surface.
3. **Serif answers, sans chrome** — model output is reading material and is set in Newsreader; the application around it is not.

**Explicitly out:** glass / backdrop-blur panels, gradient meshes, glow halos, "lit from within" elevation, gradient text or logos, violet in any role.

---

## 3) Structure — where the cabinet actually lives

**The inversion, which is the thing the eye reads first.** In a typical dark app the background is the darkest thing and cards float lighter above it. Here **the case is the lighter material and compartments are dark recesses cut into it.** Depth runs into the screen.

> **Depth rule: the case is recessed, the brass is proud.** Inset is the default for every content region; the only thing that rises is a primary action. That is why there is one per screen.

| Move | Where |
|---|---|
| The case — hard outer rail, content in an inset well | `AppShell.tsx`, `.well` in `index.css` |
| Catalogue index — drawer fronts with permanent label slips, replacing the icon rail | `AppShell.tsx` `navItems` |
| One severity-ordered status region, replacing three stacked banners | `AppShell.tsx` `notice` |
| Settings sub-nav, so Providers/Diagnostics stop navigating away | `pages/settings/SettingsLayout.tsx` |

The drawer pull is a **22×3 brass rule, not a knob** — it reads as a handle by position and material without rendering a physical object. That is the line between structural and skeuomorphic.

---

## 4) The palette — measured, not proposed

Themes are `[data-theme="cabinet"]` (dark, default) and `[data-theme="paper"]` (light). Surfaces are **opaque on purpose**: the previous glass system made every contrast figure best-case and backdrop-dependent, which was the audit's own largest caveat.

**Lowest ratio anywhere in the system: 5.58.**

### Ground

| Role | Cabinet | Paper |
|---|---|---|
| app background | `#14110E` | `#F7F3E9` |
| surface | `#1C1815` | `#F1ECDF` |
| raised (the case) | `#302A23` | `#F7F3E9` |
| deep (tracks, vitrine) | `#0A0806` | `#D3C9B1` |
| `--rule` decorative hairline | `#3E362D` | `#D6CDB6` |
| `--edge` control boundary | `#85765B` | `#877755` |

**Two border tokens, and the split is load-bearing.** WCAG 1.4.11 requires 3:1 for a boundary that identifies a control. The first values failed it everywhere (1.34–2.54). `--edge` is the measured minimum that clears on both surface and raised — Cabinet 3.98 / 3.20, Paper 3.71 / 3.95. `--rule` is decorative only.

Adjacent ground steps are deliberately close (1.06–1.32): **elevation is carried by the edge and a directional shadow, never by a lighter fill.**

### Text and status

| Token | Cabinet | surf · raised | Paper | surf · raised |
|---|---|---|---|---|
| text primary | `#F2EBDD` | 14.87 · 11.95 | `#1C1815` | 14.95 · 15.91 |
| text secondary | `#C4B79F` | 8.92 · 7.17 | `#4A4236` | 8.38 · 8.92 |
| text tertiary | `#AEA189` | 6.94 · **5.58** | `#5C5344` | 6.41 · 6.83 |
| accent (brass) | `#C4A26B` | 7.33 · 5.89 | `#5E4724` | 7.41 · 7.89 |
| success (verdigris) | `#7FAE97` | 7.06 · 5.67 | `#33513F` | 7.45 · 7.93 |
| warning (ochre) | `#D9A94E` | 8.18 · 6.57 | `#6B4A0E` | 6.82 · 7.26 |
| error (oxblood) | `#E89684` | 7.66 · 6.16 | `#7B2D26` | 7.93 · 8.44 |
| info (cyanotype) | `#8FB0C4` | 7.70 · 6.19 | `#284E63` | 7.54 · 8.03 |

`warning` was the audit's worst case at **2.65, failing at every size**, and it is the subsystem-fault indicator — the one thing telling a user OCR or the folder watcher died. It was rendered at 10px.

### Brass, and why the plate inverts per theme

`50 #F5EEE0 · 300 #D4B784 · 400 #C4A26B · 500 #B08D57 · 600 #96753F · 700 #7A5E31 · 800 #5E4724`

`#B08D57` is the brass but it is a **fill, never a text colour** — as text it measures 3.62 on Paper and 3.84 against Cabinet's edge.

| | plate fill | label | label ratio | plate vs ground |
|---|---|---|---|---|
| Cabinet | `#B08D57` | ink `#1C1815` | 5.70 | 5.70 |
| Paper | `#7A5E31` | paper `#F7F3E9` | 5.46 | 5.13 |

On Paper, `#B08D57` reaches only 2.62 against the ground — the plate would have no visible boundary — so Paper's plate is brass-700.

**Consequence for `--color-primary`:** no single brass clears `text-primary` on the surface *and* white-on-`bg-primary`. `primary` is therefore the **text-safe** accent, and filled controls use `plate` / `on-plate`. This forced 20 lines across 12 files.

### Focus ring

**No single hue works everywhere** — every candidate failed against at least one of Cabinet, Paper, or the brass plate. The fix is structural: the ring is **theme-aware and offset** — bone on Cabinet (14.87), ink on Paper (14.95) — drawn with `outline-offset` so it lands on the surface and never on the plate.

---

## 5) Typography

Self-hosted via `@fontsource`, **no CDN** — a Google Fonts `<link>` is a network call at first run and a privacy defect under the project's own first-run rule. 19 woff2 files ride in the bundle.

| Role | Face | Package |
|---|---|---|
| Display, headings, answer body | Newsreader Variable | `@fontsource-variable/newsreader` |
| UI chrome | IBM Plex Sans Variable | `@fontsource-variable/ibm-plex-sans` |
| Shelf marks, paths, figures | IBM Plex Mono | `@fontsource/ibm-plex-mono` (no variable build exists) |

**Ramp, 6 steps, hard 12px floor.** caption 12 / body-sm 13 / body 15 / title 18 / heading 24 / display 30. The app previously shipped **86 arbitrary sub-12px sizes** — `text-[10px]`×59, `text-[9px]`×15, `text-[11px]`×11, `text-[8px]`×1 — all below Tailwind's smallest step and all invisible to the audit's regex.

**No font pop-in.** `@fontsource` ships `font-display: swap`, so serif text would render in the fallback and re-flow. `fonts.ts` adds `fonts-ready` on `document.fonts.ready`, **raced against an 800ms timeout** so a face that never decodes cannot leave text hidden forever.

---

## 6) The vitrine

The Crystal Dreamscape **stays and is re-graded, never reduced** — it is the one element that could not be mistaken for a dark mode. `renderer/palette.ts` is now the single source; 19 WGSL literals and 11 three.js literals read from it via a const preamble injected into `common.wgsl` (which every shader module is already concatenated onto). Bloom intensity 1.0 → **0.35**.

Grade: ink ground, warm brass key, cool daylight fill through glass, brass rim. No violet sky.

**It is deliberately not promoted to an always-on ambient layer.** The ~4GB VRAM target binds and the local LLM needs that memory. It mounts in exactly one place, `InsightsPage.tsx:159`.

---

## 7) The guard — `frontend/scripts/check-utilities.mjs`

Tailwind emits **nothing** for a class it does not know: no build error, no lint warning, no failing test. This diffs every utility used in `src/**/*.tsx` against the class selectors in the built stylesheet, and runs in **both** `scripts/run_ci_checks.bat` and `.github/workflows/ci.yml` — §13 of `CLAUDE.md` records those two drifting apart twice.

Two traps it exists to avoid, both of which produced wrong answers during the audit:

- **Escaping.** Tailwind writes `.border-error\/20`, `.\!bg-plate`, `.hover\:bg-raised:hover`. Matching the raw name misses all of them — that is what made `border-error` look dead when it was fine. Backslashes are stripped before matching.
- **Net width.** The audit only checked colour-ish prefixes, so a dead `animate-fade-in`, a bare `rounded` and 86 arbitrary `text-[Npx]` sizes were invisible to it.

**What it found that nobody knew:**

| Dead class | Consequence |
|---|---|
| `prose prose-invert prose-sm` (`MessageBubble.tsx`) | `@tailwindcss/typography` is **not a dependency**. The model's answer — the product's primary output — had **no typographic styling at all**. |
| `clip-triangle` (`ProvidersPage.tsx`) | Never defined, so every provider whose dot shape is "triangle" drew a square. |
| `no-scrollbar`, `animate-in`, `fade-in`, `zoom-in`, `slide-in-from-top-2`, `animate-fade-in-right` | All undefined. |

Current state: **0 unmatched utilities**, 526 distinct.

---

## 8) State — what is done and what is not

**Done and gate-verified:** foundation and dual themes; the alias sweep (all 22 dead names, 142 dark-authored `white`/`black` sites, `#3d15cb` retired); the guard in both gates; the renderer re-grade; shell and IA; self-hosted fonts; `focus-visible` and `prefers-reduced-motion` where there were zero of each; `aria-` 8 → 28 including live regions for the streaming answer and indexing progress; `SettingsPage.tsx` 1341 → 136 lines plus eight modules.

**Not done — do not assume otherwise:**

- **Explorer, Providers, Diagnostics and Setup have no per-page design pass.** They carry the systematic treatment (tokens, radii, shadows, serif headings, contrast, a11y) and nothing more.
- **Eight of the thirteen primitives in `components/ui/` are not imported anywhere** — `Panel`, `LabelSlip`, `DrawerFront`, `Field`, `SpecimenCard`, `EmptyState`, `ErrorState`, `ShelfMark`. The shelf-mark treatment was implemented inline in `MessageBubble.tsx` rather than by consuming `ShelfMark`.
- **`files.extract_status` reaches no API.** It is populated in the database (`ocr_pending`, `binary`, `encrypted`, `nocontent`) but a user still cannot tell a skipped file from an indexed one. Surfacing it is a backend prerequisite, not a design choice.
- **No demand evidence exists for any of this.** `CLAUDE.md` §3 names zero demand validation as the project's most likely failure mode. The cheapest signal remains a 5–8 person moderated test on the current build, needing no new code.

---

## 9) Constraints that bite

- **`h-[560px]` on the Insights hierarchy panel is load-bearing.** `InsightsPage.tsx:120-126` records why: `flex-1` was inert there and an indefinite height chain closed a ResizeObserver feedback loop that multiplied the canvas by DPR every cycle. Any redesign of that panel must keep a definite height.
- **No inline `<script>` in `index.html`.** `app/main.py` serves a per-request nonce CSP whose only permitted inline script is the token injection. Theme init therefore runs from `main.tsx`; the cost is a possible one-frame flash only when a stored choice disagrees with the OS.
- **The 3D view is a filter control, not an ornament.** Clicking a type fires `getInsightsByType(ext)` and re-renders the Top/Cold lists beside it. There is also a real **3D CRYSTAL / 2D TREEMAP** toggle.
- **`FileEntry` is `{path, size, type, usage_count}`** — that is the entire per-file surface the API exposes. No indexed date, no per-file chunk count, no status. `QuerySource` carries `file_path`, `folder_tag`, `score`, `chunk_id` and **no section anchors**.
- **`MessageBubble.tsx` renders both `<claim sources>` and `<inference sources>`** and separates them. Grounded-vs-inferred is a real signal with real data behind it — unlike the "High Confidence" badge the audit correctly withdrew for counting `[n]` tokens.

---

## 10) Retractions

Recorded because each was asserted before being checked.

- **"No test asserts on a class name" — false.** `SearchPage.test.tsx` selected the send button with `container.querySelector('.relative.flex.items-center.glass.rounded-2xl button')`. A grep for `className` / `toHaveClass` / `class` cannot match a CSS selector string. It broke on the composer restyle and now locates the button by accessible name. Exactly one test was coupled to presentation; the others select by tag name for sanitiser assertions, which is correct.
- **"Bloom is dialled down" — was false when written.** `bloomStrength` sat in `palette.ts` unreferenced. Now wired at `WebGPURenderer.ts:1153`.
- **A background command's reported exit code is the whole shell line's status.** `cmd //c "...bat" > log 2>&1; echo "SCRIPT_EXIT=$?"` always reports 0, because `echo` runs last. This produced a real false green: the gate had aborted at step 0 (`uv sync` could not replace `rust_core.pyd` while the dev backend held it) and the notification said success. **Append the status into the redirected log, and read it there.** This is the inverse of §13's warning — there the exit code is authoritative and the tail lies; here the tail was right.
- **A script that reports success while matching nothing.** An exact-string replacement pass on `InsightsPage.tsx` printed "292 lines changed" having applied only an import; `tsc` flagging the unused import was the only reason it was caught. Verify the artefact, not the report.
