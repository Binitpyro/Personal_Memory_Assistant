# PMA — UI/UX Audit & Handoff

**Scope:** the frontend design system and user-facing surfaces only. Backend work is out of scope for this document.

**Source of truth:** `updates` @ `ebda9e7`, working tree as of 2026-08-29.
Values read from `frontend/src/index.css` and the shipped bundle
`static/react/assets/index-CvNnEk1z.css` (built 2026-08-23, i.e. newer than
`index.css`, so it reflects current source).

**Canvas version of this audit:** https://claude.ai/code/artifact/3de88f47-b708-4b55-9793-f15a5452fc05

---

## Method, and its controls

Two questions were asked mechanically, not by eye:

1. **Does a utility used in the app actually produce a CSS rule?**
   Every `bg-|text-|border-|ring-|decoration-|divide-|from-|via-|to-` class in
   `frontend/src/**/*.tsx` was extracted, Tailwind's default palette and keyword
   colours filtered out, and each remaining name checked against the class
   selectors in the shipped bundle — accounting for Tailwind's escaped opacity
   variants (`.border-error\/20`).

2. **What is the real contrast?** WCAG 2.1 ratios computed against the
   *composited* panel, not the token colour: `--panel-bg` is
   `rgba(241,245,224,0.90)` over `--color-surface` `#959f93`, which resolves to
   `rgb(232, 236, 216)`.

**Controls (this matters — an audit that finds everything missing is usually
broken):** `bg-primary`, `border-error`, `bg-error`, `text-success` and
`text-text-secondary` all resolve correctly. An early pass wrongly flagged
`border-error` because the regex missed escaped opacity variants; that was
corrected before any conclusion was drawn.

**Not covered:** the WebGPU/WebGL renderer (`WebGPURenderer.ts` ~71 KB,
`WebGL2Renderer.ts` ~35 KB, 12 `.wgsl` shaders) handles colour in shader code
and was not audited. No runtime rendering was inspected — this is a source and
built-stylesheet audit.

---

## What is defined, and is sound

Nine tokens in one `@theme` block in `frontend/src/index.css`. This half of the
system is in good shape: small, purposeful, consistently applied.

| Token | Value | Uses | Role |
|---|---|---|---|
| `--color-primary` | `#3d15cb` | 110 | Ultrasonic Blue — the action colour, the only one carrying brand |
| `--color-primary-light` | `#9984d4` | 20 | Soft Periwinkle — mostly citation underlines |
| `--color-accent` | `#8e48ea` | 13 | Purple — almost only the glass-button hover. Under-employed |
| `--color-surface` | `#959f93` | ground | Ash Grey — the page canvas |
| `--color-text-primary` | `#0f172a` | 85 | Deep Slate |
| `--color-text-secondary` | `#4a5448` | 171 | The most-used token in the app |
| `--color-success` | `#059669` | 50 | Detected / Ready / Running |
| `--color-warning` | `#d97706` | 42 | Degraded states, subsystem pip |
| `--color-error` | `#dc2626` | 43 | Failures |

Plus three variables that carry the entire visual identity — a genuine strength,
since the look is reproducible from a handful of numbers:

```css
--panel-bg:     rgba(241, 245, 224, 0.90);
--panel-border: rgba(255, 255, 255, 0.9);
--glass-blur:   32px;
```

---

## Finding 1 — 58 utility usages produce no CSS at all

**Severity: high.** 22 distinct class names, used 58 times in the running app,
have **no matching rule** in the shipped stylesheet. Tailwind emits nothing for a
token it does not know, so each is an element rendering unstyled — with no build
error, no lint warning, and no failing test.

The system in use is three vocabularies; only one is defined.

### 1a. The `danger` / `error` split — 25 usages

The theme defines `error`. A quarter of the failure surfaces ask for `danger`.

| Class | Uses |
|---|---|
| `text-danger` | 14 |
| `bg-danger` | 7 |
| `border-danger` | 4 |

**Why it is the top item:** it lands on failure text, including the API-key save
error in onboarding (`SetupPage.tsx`). An error message that is not red is not an
error message.

### 1b. The `background` / `surface` split — 9 usages

The theme defines `surface`. Nine places ask for `background`.

| Class | Uses |
|---|---|
| `bg-background` | 8 |
| `ring-offset-background` | 1 |

Affected: the full-screen onboarding backdrop (`SetupPage.tsx:216`), the
`ProvidersPage` root, and the key / endpoint / model-filter inputs on that page —
all falling back to transparent.

### 1c. A dark theme that was never defined — 20 usages

An elevation-based dark vocabulary is written into the chat and provider
components. None of it exists in `@theme`.

| Class | Uses |
|---|---|
| `bg-surface-dark` | 3 |
| `bg-accent-blue` | 3 |
| `border-border` | 3 |
| `bg-primary-h` | 2 |
| `to-accent-blue`, `text-accent-blue`, `text-text-muted`, `bg-surface-lighter`, `bg-surface-elevation-2`, `bg-surface-elevation-3`, `bg-primary-dark`, `bg-bg-dark`, `bg-border` | 1 each |

This is the clearest evidence the app was built against a different design system
than the one it ships, and it is what makes `index.css` misleading to read.

### 1d. The remainder — 4 usages

`bg-text-primary` (1) and `border-accent` (1) name defined tokens in a prefix the
build never emitted; `border-radius` (1) is a CSS property written where a class
belongs; `from-top-2` (1) is an animation origin, not a colour. Listed so the
count reconciles to 58 honestly rather than being quietly dropped.

### Fix

- 1a and 1b are aliases: three lines in `@theme`, or a rename across 34 call sites.
- 1c is a decision, not a typo — define the dark scale deliberately or delete it.

---

## Finding 2 — every status colour fails WCAG AA for body text

**Severity: high.** Measured on the composited panel `rgb(232, 236, 216)`.

| Token | Ratio | Body 4.5:1 | Large 3.0:1 |
|---|---|---|---|
| `text-primary` `#0f172a` | **14.85** | pass | pass |
| `primary` `#3d15cb` | **8.05** | pass | pass |
| `text-secondary` `#4a5448` | **6.58** | pass | pass |
| `accent` `#8e48ea` | **4.09** | **fail** | pass |
| `error` `#dc2626` | **4.02** | **fail** | pass |
| `success` `#059669` | **3.13** | **fail** | pass |
| `warning` `#d97706` | **2.65** | **fail** | **fail** |

Additional: `white` on `primary` is **9.69** (fine). `text-secondary` on the bare
ash-grey ground is **2.89** — below every threshold; it reads acceptably only
because nearly all copy sits on a panel.

**Worst case:** `text-warning` fails at every size and is exactly what the sidebar
subsystem pip uses at 10px. The one indicator that tells a user OCR or the folder
watcher died is the least readable text in the product.

**Caveat that cuts against the product:** these are *best-case* numbers. The glass
surfaces are translucent and blurred, so real contrast varies with whatever sits
behind them — over a busy backdrop the status colours do worse, not better.

### Fix

Darken the three status hues until they clear 4.5:1 on the panel, keeping the hue
relationship — roughly `#047857` (success), `#b45309` (warning), `#b91c1c` (error)
are the next stops on the same ramps. One line per token; does not disturb the
glass look. It must be done at the source because of Finding 4.

---

## Finding 3 — no typeface, no type scale

**Severity: medium.** There are **zero `font-family` declarations** anywhere in
the frontend. `body` applies Tailwind's `font-sans`, so every glyph in the product
is the OS default — the app has no typographic identity and looks materially
different on Windows, macOS and Linux.

Type sizes are picked per element with no documented ramp:

| Class | Uses |
|---|---|
| `text-xs` | 106 |
| `text-sm` | 104 |
| `text-lg` | 22 |
| `text-xl` | 10 |
| `text-2xl` | 9 |
| `text-3xl` | 2 |
| `text-base` | 1 |

**210 of 254 sizing usages are the two smallest steps.** The interface is very
dense, and `text-xs` (12px) doing the most work is a legibility risk that
compounds Finding 2.

For a product whose pitch is craft on modest hardware, a licensed or well-chosen
open typeface is the cheapest available lift in perceived quality.

---

## Finding 4 — the primitives are rigid, and thin

**Severity: medium.** Four CSS classes carry the whole system — `.glass`,
`.glass-card`, `.glass-button`, `.glass-input`. There is no component library
behind them; everything else is ad-hoc Tailwind.

**Ten `!important` declarations** in `index.css`: four on `.text-primary`,
`.text-success`, `.text-warning`, `.text-error`, and six on the glass and button
backgrounds and borders. Consequences:

- A component cannot tint or theme any of them without another `!important`.
- The Finding 2 colour fix must happen at the token source.
- A second theme could not be layered on even if the dark tokens from 1c existed.

**Missing states:** `.glass-button` defines rest, hover and active — no `disabled`
and no `focus-visible`. The only focus treatment in the system is `.glass-input`'s
4px primary ring, which is not applied to buttons or links. That is a keyboard
accessibility gap on every interactive control.

**Misleading affordance:** `.glass-card:hover` lifts 4px over 500ms and is applied
to cards that are not clickable.

**Radius is unrationalised** — seven radii in live use plus a one-off:

| `rounded-xl` | `rounded-full` | `rounded-lg` | `rounded-2xl` | `rounded-3xl` | `rounded-md` | `rounded-sm` |
|---|---|---|---|---|---|---|
| 72 | 47 | 46 | 31 | 8 | 7 | 2 |

Plus `rounded-[2.5rem]` in `.glass-card`. No rule says which belongs to what;
three tiers would cover every case in the app.

---

## Recommendations, in order

| # | Action | Effort | Why this order |
|---|---|---|---|
| 1 | Add a CI step diffing used utilities against emitted rules | Low | Without it, everything below silently rots again. This is the durable fix. |
| 2 | Resolve the `danger` and `background` aliases (34 sites) | Low | Restores colour to error messaging and the onboarding backdrop. |
| 3 | Darken `success` / `warning` / `error` to clear AA | Low | Three lines. Highest accessibility return per character changed. |
| 4 | Decide the dark vocabulary: define or delete (20 sites) | Medium | A decision, not a typo. Leaving it makes the system unreadable to the next person. |
| 5 | Add `focus-visible` to `.glass-button` and links | Low | Keyboard access is currently unstyled outside inputs. |
| 6 | Choose a typeface and a 5-step type ramp | Medium | Biggest perceived-quality lift; do it after correctness. |
| 7 | Collapse the radius scale to three tiers | Medium | Cosmetic consistency; safe to defer. |

**Item 1 in concrete terms:** neither `scripts\run_ci_checks.bat` nor
`.github/workflows/ci.yml` runs `npm run build`, and neither compares utilities to
emitted rules. Adding the build alone would not have caught these 58 (they are
valid TypeScript); the utility diff is a separate short script. Both belong in
*both* files, since §13 of `CLAUDE.md` records that the local gate and CI have
already drifted twice.

---

## Handoff — UI/UX changes landed this session

All verified with both gate scripts green (`SCRIPT_EXIT=0`); vitest 121 passed
across 21 files, up from 95. Nothing is committed.

**Onboarding (`SetupPage.tsx`)**
- Cloud consent is now collected at the point a key is entered, and gates
  `canProceed`. Previously a user could finish setup with a cloud key and no
  consent, and every subsequent question failed in the dispatch gate.
- The featured provider list is derived from `getProviders()` instead of a
  hardcoded array that had drifted from `PROVIDER_REGISTRY` and omitted
  `spec.kind`.
- Step 2 now offers two forward actions (demo corpus, or hand off to Library)
  instead of ending on an empty index.
- The split-brain restart branch explains itself instead of silently disabling
  Continue.

**Shell and navigation**
- `AppShell.tsx`: a consent banner for already-onboarded installs, deep-linking to
  `/settings/providers#cloud-consent`. The subsystem warning pip now links to
  Diagnostics rather than saying "check the backend logs".
- New `/settings/diagnostics` route and `DiagnosticsPage.tsx` — renders subsystem
  `detail` strings, latency percentiles, compact-DB status and OCR engine facts,
  all of which the backend already returned with no consumer.
- New `NotFoundPage.tsx` and a `*` route; unknown paths previously rendered blank.
- `SettingsPage.tsx`: both reset handlers now clear `pma_tour_completed` as well as
  `pma_setup_complete` — "Restart Onboarding" had been leaving the tour dismissed.

**Providers and settings**
- An edited Base Endpoint URL now saves. It was editable, was sent on Validate,
  and was silently dropped on Save.
- Confirmations added to OCR install, uninstall, cache clear, and Remove
  Connection — all previously one unguarded click. Uses the sonner action-toast
  pattern established in `SearchPage.tsx`, not `confirm()`.

**Answer provenance (`MessageBubble.tsx`)**
- The selected query mode is echoed back as a second pill; `challenge` answers are
  now labelled. The backend had been overwriting the request's `mode` with the
  retrieval path.
- `+N more` sources is now an expander; it was static text with nothing behind it.
- The contradictions banner names the files it is warning about instead of
  asserting an unverifiable conflict.
- **Withdrew the "High Confidence" green styling.** Its only input was the count of
  `[n]` tokens the model emitted — not relevance, not a reranker score, and no
  check that the cited chunk supports the sentence. It now reports the citation
  count and leaves the confidence judgement to the reader. Restoring a confidence
  signal requires a real one to key on.

## Open UI/UX items, not started

- Everything in Recommendations above.
- `watcher_enabled` and `agentic_enabled` are user-facing behaviours with no UI at
  all. Deliberately deferred — `agentic_enabled`'s default should be decided first.
- The contradiction heuristic (`retrieval.py`) fires on any chunk containing
  "but", "however" or "except", so the banner is expected to be noisy. Tightening
  it changes retrieval behaviour and needs an eval run, not a green gate.
- No demand evidence exists for the Diagnostics screen or the provenance work.
  The cheapest real signal is a 5–8 person moderated test on the current build —
  it needs no new code.
