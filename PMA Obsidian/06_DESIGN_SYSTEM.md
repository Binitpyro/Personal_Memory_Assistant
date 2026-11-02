# Design System — Safelight

Reference document for AI-assisted engineering on the frontend.

**Last verified against source: 2026-10-14, `UI/UX` at `87f7e66`.** The shipped system is **Safelight**, ported in `b592e46` (2026-09-29) from the Safelight Design System artifact, which is its source of truth. Specimen Cabinet (2026-08-29, `4360876`) is superseded; its palette, typography and per-page pass are history and were removed from this document. Anything here that a source read contradicts is stale — re-verify before acting on it.

---

## 1) History, in one paragraph each

1. **Violet/indigo with glass and gradient glow — rejected as AI slop.** Glow and floating translucent panels are the visual language of cloud intelligence; PMA reads your own files on your own machine.
2. **Archival palette on the untouched skeleton — rejected as "just dark mode".** The lesson that survived: **the metaphor has to be carried by layout**, not palette, and never by texture.
3. **Specimen Cabinet** (brass, ink, bone; IBM Plex + Newsreader; recessed wells) shipped 2026-08-29 and was replaced by Safelight on 2026-09-29. Its utility guard (§6) and the theme storage keys (`cabinet`, `paper`) survive.

---

## 2) Direction

**A dark room lit by one red lamp** — square and icon-free (`b592e46`; rules at the top of `frontend/src/index.css`).

- **The lamp marks the one live thing.** `--pma-accent` / `--pma-plate` is only ever written explicitly (`text-accent`, `decoration-accent`, `bg-plate`). It is never a focus colour, and ink text never sits on it.
- **Faults are fog.** There is no green and no blue: state is a word beside a square, so success and info fall back to ink2.
- **Depth is tone.** Nothing is rounded (every `--radius-*` is 0; `rounded-full` is off the scale and left alone); only floating things — menus, toasts — cast a shadow.
- **Icons are glyphs or words.** `lucide-react` is removed.

---

## 3) Structure

| Element | Where |
|---|---|
| A 72px numbered rail: Ask, Library, Explorer, Insights, Settings (Ask keeps the `/search` route) | `AppShell.tsx` `navItems` |
| A 28px status bar and one film grain over the window. The grain crawls while PMA searches, writes, prints a receipt or indexes, and is frozen otherwise; pages report work through `useWorking` | `AppShell.tsx` |
| Ask: the ask bar over **Frames \| Answer \| Receipt**. Frames lists ranked passages, the "Not used" rule and near misses; the receipt prints path, frames, timing, tokens and cost | `components/chat/FramesPanel.tsx`, `Receipt.tsx` |
| Library: a figure line, the index as a film strip, cell progress | `LibraryPage.tsx` |

Scores are **truncated** to two places (`formatScore`), never rounded.

**Deliberately not drawn, though the board shows them:** a route / "left this machine" row (the stream does not report which provider answered), numbered cite marks (the model's `[n]` do not index sources), an EV floor number (the real floor is a batch-dependent rerank logit), and a receipt number or fingerprint (nothing produces one).

---

## 4) Tokens and type — read them in `frontend/src/index.css`

Themes keep their storage values so no stored choice needs migrating: `[data-theme="cabinet"]` is Safelight (dark, default) and `[data-theme="paper"]` is Lights on (light). Every ratio is annotated next to its hex value in `index.css`, computed against the opaque grounds room · sheet · raise. Do not copy them here; they drift.

- Control edges use ink3 (`--pma-edge`) so they clear WCAG 1.4.11's 3:1; `--pma-rule` is a decorative hairline only.
- Focus ring is ink (`--pma-ring`), never the lamp.
- **Type**, vendored through `@fontsource` (no CDN, `fonts.ts`): **Archivo Variable** (wdth 62–125) for interface and stock text, **Newsreader** for answers and quotes only, **Geist Mono** for receipts, paths, scores and status. `fonts.ts` adds `fonts-ready` on `document.fonts.ready`, raced against an 800 ms timeout.

---

## 5) The vitrine

The Crystal Dreamscape **stays and is re-graded, never reduced** — it is the one element that could not be mistaken for a dark mode. `renderer/palette.ts` is now the single source; 19 WGSL literals and 11 three.js literals read from it via a const preamble injected into `common.wgsl` (which every shader module is already concatenated onto). Bloom intensity 1.0 → **0.35**.

Grade: ink ground, warm brass key, cool daylight fill through glass, brass rim. No violet sky.

**It is deliberately not promoted to an always-on ambient layer.** The ~4GB VRAM target binds and the local LLM needs that memory. It mounts in exactly one place, `InsightsPage.tsx:159`.

---

## 6) The guard — `frontend/scripts/check-utilities.mjs`

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

The guard runs in both gates; read its output there rather than trusting a count written here.

---

## 7) Constraints that bite

- **A toast-based confirmation makes a test's trailing async work race jsdom teardown.** `ExplorerPage.test.tsx`'s in-flight-removal test resolved its deferred mutation promise as the **last statement** and returned, so react-query ran `onSuccess` — cache invalidation plus state — after the test finished. Intermittently that surfaced as `ReferenceError: window is not defined` under **Unhandled Errors**, which fails the whole vitest stage *while every test still reports as passed*, so the summary line lies. Resolve inside `await act(async () => ...)` instead. Introduced when the `confirm()` became a sonner action-toast: the old synchronous dialog had no trailing async work to strand.

- **Stop the preview dev server before running `Run-Tests.bat`.** `frontend/playwright.config.ts` sets `webServer.reuseExistingServer: !process.env.CI`, and the batch script does not set `CI` — so Playwright **adopts whatever is already serving port 5173** rather than starting its own. If that borrowed server dies, all four E2E tests fail together with "element(s) not found" on ordinary inputs in files the change never touched, which reads exactly like a global render regression. Seen 2026-08-30: `SCRIPT_EXIT=1`, `[FAIL] Playwright`, while Python, Rust, Miri and vitest all passed; `npx playwright test` with nothing on 5173 then passed 4/4. Check the port before reading the diff for a cause.

- **`h-[560px]` on the Insights hierarchy panel is load-bearing.** `InsightsPage.tsx:120-126` records why: `flex-1` was inert there and an indefinite height chain closed a ResizeObserver feedback loop that multiplied the canvas by DPR every cycle. Any redesign of that panel must keep a definite height.
- **No inline `<script>` in `index.html`.** `app/main.py` serves a per-request nonce CSP whose only permitted inline script is the token injection. Theme init therefore runs from `main.tsx`; the cost is a possible one-frame flash only when a stored choice disagrees with the OS.
- **The 3D view is a filter control, not an ornament.** Clicking a type fires `getInsightsByType(ext)` and re-renders the Top/Cold lists beside it. There is also a real **3D CRYSTAL / 2D TREEMAP** toggle.
- **`FileEntry` is `{path, size, type, usage_count}`** — that is the entire per-file surface the API exposes. No indexed date, no per-file chunk count, no status. `QuerySource` carries `file_path`, `folder_tag`, `score`, `chunk_id` and **no section anchors**.
- **`MessageBubble.tsx` renders both `<claim sources>` and `<inference sources>`** and separates them. Grounded-vs-inferred is a real signal with real data behind it — unlike the "High Confidence" badge the audit correctly withdrew for counting `[n]` tokens.

---

## 8) Retractions

Recorded because each was asserted before being checked.

- **"No test asserts on a class name" — false.** `SearchPage.test.tsx` selected the send button with `container.querySelector('.relative.flex.items-center.glass.rounded-2xl button')`. A grep for `className` / `toHaveClass` / `class` cannot match a CSS selector string. It broke on the composer restyle and now locates the button by accessible name. Exactly one test was coupled to presentation; the others select by tag name for sanitiser assertions, which is correct.
- **"Bloom is dialled down" — was false when written.** `bloomStrength` sat in `palette.ts` unreferenced. Now wired at `WebGPURenderer.ts:1153`.
- **A background command's reported exit code is the whole shell line's status.** `cmd //c "...bat" > log 2>&1; echo "SCRIPT_EXIT=$?"` always reports 0, because `echo` runs last. This produced a real false green: the gate had aborted at step 0 (`uv sync` could not replace `rust_core.pyd` while the dev backend held it) and the notification said success. **Append the status into the redirected log, and read it there.** This is the inverse of §13's warning — there the exit code is authoritative and the tail lies; here the tail was right.
- **A script that reports success while matching nothing.** An exact-string replacement pass on `InsightsPage.tsx` printed "292 lines changed" having applied only an import; `tsc` flagging the unused import was the only reason it was caught. Verify the artefact, not the report.
