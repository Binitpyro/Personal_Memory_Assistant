#!/usr/bin/env node
/**
 * Capture the app's surfaces to PNG, both themes, for design review.
 *
 * The Browser pane hands back images, not files, so this uses the Playwright
 * that is already a devDependency to write real PNGs to disk.
 *
 * Requires the backend on 127.0.0.1:8000. Output goes to `PMA Obsidian/files/`,
 * which is gitignored (.gitignore:230) — screenshots should not enter history.
 *
 * Usage:  node scripts/capture-screens.mjs [outDir]
 */
import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = process.argv[2] ?? resolve(HERE, '..', '..', 'PMA Obsidian', 'files', 'screens');
const BASE = 'http://127.0.0.1:8000';

const ROUTES = [
  ['library', '/library'],
  ['search', '/search'],
  ['explorer', '/explorer'],
  ['insights', '/insights'],
  ['settings', '/settings'],
  ['settings-providers', '/settings/providers'],
  ['settings-diagnostics', '/settings/diagnostics'],
];

mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2, // legible when scaled down in a doc
});

// The /setup gate is client-side (localStorage), so seed it before first paint.
await ctx.addInitScript(() => {
  localStorage.setItem('pma_setup_complete', '1');
  localStorage.setItem('pma_tour_completed', '1');
});

const page = await ctx.newPage();
const problems = [];
page.on('console', (m) => { if (m.type() === 'error') problems.push(m.text().slice(0, 160)); });

let n = 0;
for (const theme of ['cabinet', 'paper']) {
  await page.addInitScript((t) => localStorage.setItem('pma_theme', t), theme);
  for (const [name, route] of ROUTES) {
    await page.goto(BASE + route, { waitUntil: 'networkidle' });
    // `animate-fade-in-up` runs on the page container and the font gate holds
    // serif text until document.fonts.ready; both must settle or the capture
    // comes out faint. This caught several half-faded shots by hand.
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(1200);
    const file = `${OUT}/${String(++n).padStart(2, '0')}-${name}-${theme}.png`;
    await page.screenshot({ path: file, fullPage: false });
    console.log('  ' + file.split(/[\\/]/).slice(-1)[0]);
  }
}

await browser.close();
console.log(`\n${n} screenshots -> ${OUT}`);
if (problems.length) {
  console.log('\nconsole errors seen while capturing:');
  [...new Set(problems)].slice(0, 8).forEach((p) => console.log('  ' + p));
}
