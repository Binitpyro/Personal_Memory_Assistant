# PMA frontend

React 19 + TypeScript + Vite, styled with Tailwind CSS and the Safelight design system (`../PMA Obsidian/06_DESIGN_SYSTEM.md`). Shipped as a Tauri v2 desktop shell (`src-tauri/`) and also served by the FastAPI backend from `../static/react`.

```bash
npm run dev          # Vite dev server on localhost:5173
npm run build        # tsc -b + vite build -> ../static/react
npm run lint         # eslint
npm test             # vitest
npm run test:e2e     # Playwright, against the Vite dev server (not the backend)
node scripts/check-utilities.mjs   # every Tailwind class used must exist in the built CSS
```

Notes:

- `npm run build` runs `tsc -b`, which applies `tsconfig.app.json` (`noUnusedLocals`); `npx tsc --noEmit` is not the same check.
- Fonts are vendored through `@fontsource` (`src/fonts.ts`); nothing is fetched from a CDN at runtime.
- Stop any server on port 5173 before `scripts\Run-Tests.bat`: Playwright reuses an existing server there.
