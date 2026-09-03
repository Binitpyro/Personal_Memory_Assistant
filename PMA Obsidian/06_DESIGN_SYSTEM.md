# Design System — Specimen Cabinet

Reference document for AI-assisted engineering on the frontend.

**Last verified against source: 2026-08-30, on top of commit `f33c310`, branch `updates`, working tree dirty with the four-page pass, the raw-palette pass and the ProviderRecipes pass (all §8).** Every ratio below was computed on 2026-08-29 against the values that actually ship in `frontend/src/index.css`, not carried over from a plan; the four-page pass introduced no new colour value, so they still hold, but they were **not** re-measured. Anything here that a source read contradicts is stale — re-verify before acting on it. §5 carries a retraction and §8 a status change made on 2026-08-30.

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

**Ramp, 6 steps.** caption 12 / body-sm 13 / body 15 / title 18 / heading 24 / display 30. The app previously shipped **86 arbitrary sub-12px sizes** — `text-[10px]`×59, `text-[9px]`×15, `text-[11px]`×11, `text-[8px]`×1 — all below Tailwind's smallest step and all invisible to the audit's regex.

> **Retracted 2026-08-30 — "hard 12px floor" was never true, and this document was the only place claiming it.** Measured at `f33c310`: **109** arbitrary sub-12px sizes were still live, and 16 of them sat inside the system's own primitives (`LabelSlip` and `Field` in `Surfaces.tsx`, `ShelfMark` and `EmptyState` in `Feedback.tsx`) and in `AppShell.tsx`'s catalogue marks. A rule the system breaks in its own foundation is not a rule.
>
> **The real rule, which is what the code has actually been doing:**
>
> | Context | Floor |
> |---|---|
> | Prose, UI labels, anything set in the sans or serif | **12px**, i.e. the ramp's `caption` step |
> | Mono metadata — catalogue marks, shelf marks, paths, chunk ids, ruled field labels | **10px** permitted, 9px for a ruled `Field` label |
> | Anything at all | never below 9px |
>
> The 10px mono line is load-bearing: it is what makes a drawer front read as a label slip rather than a nav item, and raising it to 12px would change the shell's proportions. It is also the reason the exception is narrow — IBM Plex Mono at 10px on `--text-tertiary` measures 6.94 on surface and 5.58 on raised, so the small size is paid for with contrast rather than excused.
>
> As of this pass **104 remain**, 48 of them on an explicitly `font-mono` element. The one live `text-[8px]` — a `text-text-secondary/60` sub-label in the Providers cascade panel, below even the mono allowance and with an alpha that dropped it under its own measured ratio — is gone. The rest are inventory, not defects, and are not worth churn.

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

**Done 2026-08-30 — the four-page pass** (`SetupPage`, `ProvidersPage`, `ExplorerPage`, `DiagnosticsPage`). Both entries below were the top of the "not done" list and are now closed; the paragraph each replaced is kept in place so the claim can be diffed rather than re-derived.

- **Those four pages now have the per-page pass.** What came off them: the last two mesh-glow blobs and the `from-primary to-accent-blue` gradient tile with its `shadow-primary/20` halo (Setup, i.e. screen one); `backdrop-blur` on an opaque header; ~50 `bg-primary/N` tint-as-surface sites; 12 `font-black`; a second accent family in Explorer's sidebar; `confirm()` and `alert()`; and every `glass-button` with an `!important` override.
- **Two real a11y bugs, found on the way and not previously recorded.** Both cloud-consent blocks (`SetupPage`, `ProvidersPage`) were authored in raw Tailwind palette values — `bg-amber-500/10 border-amber-500/20 text-amber-800`, icon `text-amber-600` — never tokens. On Cabinet that is near-black text on a near-black wash. They now use `warning`, which measures 8.18 on cabinet and 6.82 on paper. Separately, `providers/icons.tsx` carried nine hardcoded hues (indigo, emerald, orange, amber, sky, green, zinc, blue, slate), all authored for the dark theme; `text-amber-400` on Paper's `#F7F3E9` measures about 1.7. The marks now inherit `currentColor` and the glyph alone distinguishes the provider.
- **Primitive adoption: 11 of 13, up from 3.** Newly consumed: `Panel`, `Button`, `Badge`, `Skeleton`, `LabelSlip`, `Field`, `SpecimenCard`, `EmptyState`, `ErrorState`.
- **A double-submit bug in `Button` itself, found by adopting it.** The gate read `disabled={disabled ?? loading}`. `??` only falls through on null/undefined, so an explicit `disabled={false}` won and `loading` was discarded — meaning the ordinary form-submit shape, `disabled={!valid} loading={saving}`, stayed **clickable for the whole request**. It affected Save Configuration, Test & Validate, Validate All, Compact now and the Setup API-key save. Now `disabled || loading`, which is what the line's own comment ("a loading control is not a target") always claimed. Invisible until now because `LibraryPage` was the sole consumer and never passed `loading`. Locked by `tests/components/Button.test.tsx`; restoring `??` fails 2 of its 5 tests.
- **A double-confirmation introduced during this pass, and caught in review.** Moving Explorer's folder removal from `confirm()` to a sonner action-toast changed the contract of the `onDeleteFolder` callback, and `FileTypeTreemap.tsx` — its only other caller — was still running its own `confirm()` first. The treemap path asked twice: a platform dialog, then a toast. The `confirm()` there is gone; the callback owns the confirmation.
- **Setup's "Update" was a dead control.** It called `setKey('')` and `setSaving(false)`, both already their current values, while the branch it sat in still keyed off `pData.is_set` — so it re-rendered the identical "Ready" view and **a stored API key could not be replaced from onboarding at all**. An `editing` flag now reopens the field, with a Cancel beside Save so the user is not trapped, and the control is hidden entirely when `stored_in === 'env'` (not ours to replace — the rule ProvidersPage already enforces). Three tests in `SetupPage.test.tsx`; dropping the flag from the branch fails two of them.
- **Copy: the "intelligence" register is retired on these pages, deliberately.** §2 of this document states the purpose as the user *getting their own material back, not conjuring intelligence*, and three strings contradicted it outright. Setup's subtitle was "Your offline-first personal memory assistant. Let's get your intelligence engine connected." and is now "Everything stays on this machine. Point PMA at a model, then at your files."; its "Cloud Intelligence" heading is now "Cloud models"; Providers' H1 was "Intelligence Engines" and is now "Model providers", matching the "Providers" tab in the Settings sub-nav. **Do not restore them.** `SetupPage.test.tsx` asserts the new subtitle verbatim and matches the heading by regex, because the heading now carries a nested catalogue mark. One instance survives out of scope: `pages/settings/LLMPreferences.tsx:85`, "your preferred intelligence provider".

> **On evidence for the three behavioural fixes above** (the `Button` loading gate, the treemap double-confirmation, and Setup's dead "Update"). Each is negative-controlled in the sense §13 of `CLAUDE.md` asks for — the fix was reverted, the test observed to fail, and the fix restored — except the treemap double-confirmation, which has **no test**. It was found by reading the call graph after changing the callback's contract, not by a failing assertion, and `FileTypeTreemap.test.tsx` does not exercise the delete path at all. Treat that one as verified by inspection only.


**Not done — do not assume otherwise:**

- **`DrawerFront` and `ShelfMark` are still imported nowhere.** Not because they are unused ideas — both treatments ship — but because `AppShell.tsx` and `MessageBubble.tsx` hand-roll the markup the primitives were extracted from. Collapsing each caller onto its primitive is a separate, small change on two out-of-scope files.
- **10 `!important` utilities remain** (was 12; `TourOverlay`'s two went with the raw-palette pass), all on `glass-button`/`glass-card` in files outside these passes: `settings/LLMPreferences.tsx` ×4, `settings/ResetSection.tsx` ×4, `settings/LocalModels.tsx`, `settings/DiagnosticsSection.tsx`, `settings/SplitBrainSection.tsx`, `NotFoundPage.tsx`, `LibraryPage.tsx` and `MessageBubble.tsx:256`. `.glass`, `.glass-card` and `.glass-button` therefore stay defined in `index.css` as the migration bridge.
**Done 2026-08-30 — the raw-palette pass** (`ModelPicker`, `WebGPUFallback`, `MessageBubble`, `TourOverlay`). The entry this replaces listed these four as surviving instances of the consent-banner bug. Three were; one was not, and that distinction is the useful part.

- **`ModelPicker.tsx:195`** — `bg-yellow-500/20 text-yellow-300` on the "Offline / Cached" chip. Real: #fde047 on Paper's #F1ECDF panel measures about 1.2. Now `Badge tone="warning"` (8.18 cabinet / 6.82 paper).
- **`MessageBubble.tsx:149`** — `bg-black/80 text-white` on the "Precision match" tooltip. Not a contrast failure; a **theme** failure, a black box in a cream UI. First attempt used `bg-deep`, and measuring killed it: deep against the cabinet page ground is **1.06**, and `border-rule` on deep is 1.69 cabinet / 1.04 paper, so the tooltip would have had no visible edge. No single ground token is distinct in both themes — the same shape of problem §4 records for the focus ring. It uses `bg-surface` + `border-edge` (3.98 / 3.71) + `shadow-md` instead, which is what §4 already says out loud: *elevation is carried by the edge and a directional shadow, never by a lighter fill*. Also 9px → 11px, since a UI label is not mono metadata.
- **`TourOverlay.tsx`** — `shadow-primary/20` was a coloured glow (§2: out). Also retired: `backdrop-blur-2xl` over an **opaque** `bg-surface`, which blurred nothing; `rounded-3xl`, which renders 10px; and `border-primary/10`, a 10%-alpha brass that is effectively invisible. Its two `glass-button` controls became `Button variant="plate"`.

> **`WebGPUFallback` was the one that was NOT a bug, and mechanically tokenising it would have broken the page.** `renderer/palette.ts` reads no CSS variable and no theme: the vitrine is a fixed dark grade (`skyHorizon` #0A0806) in **both** themes, and the wrapper is a fixed `#02030a`. So every overlay there sits on a dark ground regardless of the user's choice, and `text-white/*` is correct — swapping it for `text-text-primary` would render ink on ink the moment someone picked Paper. The file now says so in a comment, because this is exactly the change a future sweep makes by reflex.
>
> What *was* wrong there was the **alpha**, and separately, two branches that are not over the canvas at all:
>
> | | before | after |
> |---|---|---|
> | `:442` Node # label, 10px | white/40 → **3.70** | white/70 → 9.85 |
> | `:453` hover kind, 9px | white/40 → **3.69** | white/70 at 10px → 9.87 |
> | `:431` separator glyph | white/30 → **2.53** | a real 1px rule, so no text rule applies |
> | tier dots | two `shadow-[0_0_12px_...]` glows, one of them **`rgba(142,72,234)` — violet**, which §2 bans in any role | flat, no glow, no violet |
> | `checking` branch | `bg-slate-900 border-slate-800 text-slate-400 border-blue-500` | tokens — it renders on the themed page, not the canvas |
> | `unsupported` branch | `bg-amber-900/30 border-amber-500 text-amber-200` | tokens — same reason; this is the consent-banner bug again |
>
> Ratios are composited against the real ground rather than eyeballed. The violet glow and those two branches were **not** in the original list; they were found by reading the file rather than the line numbers.

**Done 2026-08-30 — `ProviderRecipes`, the fifth site the raw-palette sweep missed.** The sweep named four files and there were five. It is worth recording *how* it was missed: the four were found by grepping the pages the design pass had touched, and `ProviderRecipes` is a component mounted **into** one (`ProvidersPage.tsx:429`) rather than a page itself. So it sat inside a surface that had been passed, and was the one panel still reading as pre-system.

- **The three-way colour coding is kept, deliberately.** These are three options a user picks between and the tone carries which is which. What changed is that it is expressed in tokens: `text-amber-500` / `bg-amber-500/10` → `warning`, `bg-surface` → `success/10` so the first chip stops being the odd one out, and `accent-blue` → `info` for one name per token (`accent-blue` is a real alias for `--pma-info`, so it emitted CSS and was never broken).
- **Measured in both themes**, composited onto the real opaque ancestor, after a reload rather than after a live toggle (see the retraction below):

  | | cabinet | paper |
  |---|---|---|
  | icon on chip — Free / Max / Fast | 7.23 · 8.37 · 7.89 | 5.96 · 5.46 · 6.04 |
  | title on card | 14.87 | 14.95 |
  | description on card | — | 8.38 |

- **Also retired:** `glass rounded-3xl border-primary/10` → the `Panel` primitive; `backdrop-blur-[1px]` over an **opaque** `bg-surface`, which blurred nothing and cost a compositor layer — the identical defect removed from `TourOverlay`; `rounded-full` on a close button, plus `2xl`/`xl`/`lg` onto the four-page scale; `border-primary/10 hover:border-primary/30` → `border-rule hover:border-edge`, because the card *is* the control (WCAG 1.4.11); and `text-danger bg-danger/5` → `text-error` on a real edge.
- **First test coverage, 5 tests.** The component had none and it is not decorative: applying a recipe rewrites the fallback chain *and* the default model in two sequential calls, so a partial failure leaves them disagreeing. One test covers exactly that. The icon-only close button had no accessible name; removing the `aria-label` now fails 2 tests.

> **Left by decision, not oversight — do not re-report these.** The `Rocket` icon (rocketship-for-launch is a named cliché) and the three-equal-cards grid both stay: the scope taken was hygiene, and for a 3-option chooser three cards is arguably the honest shape.

**Not done — do not assume otherwise:**

- **`WebGPUFallback`'s visual states were never seen in a browser.** Insights needs a live backend, so the component does not mount without one; everything above rests on computation, source reading and the existing `unsupported`-branch test. The canvas overlays in particular are unverified visually. `ProviderRecipes` *was* seen, in both themes — it is local state plus a static array, so it renders without a backend.

> **Measuring colour in the Browser pane needs one precaution, and getting it wrong has already produced two false bug reports.** `document.timeline.currentTime` does **not advance** there — six samples 400 ms apart all read `0`. Every CSS transition therefore pins at `currentTime: 0 / running` and `getComputedStyle` returns its **start** value, so after any theme switch a transitioning element still reports the *previous* theme's colour, and descendants inherit it — even a freshly created probe with no transition of its own. **Change the theme, reload, then measure.** An empty `el.getAnimations()` is the signal the reading can be trusted. This is not a product bug: on a clean load both themes are correct.
- **Raw palette values still survive elsewhere**, unaudited: `ModelPicker.tsx:134` `bg-black/60` and `TourOverlay.tsx:105` `bg-black/40` are both **modal scrims**, which is a legitimate use of a translucent black (an occluder is not a themed surface) — left deliberately. `ModelPicker` also still has an emoji button (a refresh button labelled with an emoji) and a `placeholder:text-text-secondary/50`, which is alpha on an already-measured token.
- **`confirm()` / `alert()` remain in five places** outside this pass — `LibraryPage.tsx:129,290` and `SettingsPage.tsx:57,63,74`. `FileTypeTreemap.tsx` no longer has one (see the double-confirmation entry above); `SearchPage.tsx:110` only mentions them in a comment explaining why it uses sonner instead.
- **Markup still names 7 radii against 4 token values.** Inside the four pages the scale is now `sm` for inputs and chips, `md` for controls, `xl` for containers, `full` only for real dots. Elsewhere `rounded-2xl` and `rounded-3xl` still appear and still render 10px, so the markup states an intent the tokens do not honour.
- **`Skeleton`'s radius cannot be overridden from `className`, and that is a cascade fact, not a bug to "fix" by reordering classes.** Tailwind emits `.rounded-full` at byte 24097 of the built sheet and `.rounded-sm` at 24180; equal-specificity rules are decided by **emission order**, not by the order of the class attribute. `Skeleton` hardcodes `rounded-sm`, so a caller passing `rounded-full` for a circular placeholder gets a rounded square and no warning. One such call site existed in this pass and the dead class was removed rather than left in as decoration. The same trap applies to any `h-*` override on `Button`, whose sizes set a fixed height — use `min-h-*`, which is a different property and therefore actually wins.
- **`files.extract_status` reaches no API.** It is populated in the database (`ocr_pending`, `binary`, `encrypted`, `nocontent`) but a user still cannot tell a skipped file from an indexed one. Surfacing it is a backend prerequisite, not a design choice.
- **No demand evidence exists for any of this.** `CLAUDE.md` §3 names zero demand validation as the project's most likely failure mode. The cheapest signal remains a 5–8 person moderated test on the current build, needing no new code.

---

## 9) Constraints that bite

- **A toast-based confirmation makes a test's trailing async work race jsdom teardown.** `ExplorerPage.test.tsx`'s in-flight-removal test resolved its deferred mutation promise as the **last statement** and returned, so react-query ran `onSuccess` — cache invalidation plus state — after the test finished. Intermittently that surfaced as `ReferenceError: window is not defined` under **Unhandled Errors**, which fails the whole vitest stage *while every test still reports as passed*, so the summary line lies. Resolve inside `await act(async () => ...)` instead. Introduced when the `confirm()` became a sonner action-toast: the old synchronous dialog had no trailing async work to strand.

- **Stop the preview dev server before running `Run-Tests.bat`.** `frontend/playwright.config.ts` sets `webServer.reuseExistingServer: !process.env.CI`, and the batch script does not set `CI` — so Playwright **adopts whatever is already serving port 5173** rather than starting its own. If that borrowed server dies, all four E2E tests fail together with "element(s) not found" on ordinary inputs in files the change never touched, which reads exactly like a global render regression. Seen 2026-08-30: `SCRIPT_EXIT=1`, `[FAIL] Playwright`, while Python, Rust, Miri and vitest all passed; `npx playwright test` with nothing on 5173 then passed 4/4. Check the port before reading the diff for a cause.

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
