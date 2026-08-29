#!/usr/bin/env node
/**
 * Utility-diff guard.
 *
 * Every Tailwind utility referenced in src/**\/*.tsx must actually produce a
 * rule in the built stylesheet. Tailwind emits nothing for a token it does not
 * know: no build error, no lint warning, no failing test. The UI/UX audit found
 * 58 such usages across 22 class names, several of which landed on error text.
 *
 * Two traps this script exists to avoid, both of which produced wrong answers
 * during the audit and again while verifying the fix:
 *
 *   1. ESCAPING. Tailwind writes `.border-error\/20`, `.\!bg-plate`,
 *      `.hover\:bg-raised:hover`. Matching on the raw name misses all of them,
 *      which is what made `border-error` look dead when it was fine. We strip
 *      backslashes from the CSS before matching.
 *   2. NET WIDTH. The audit only checked colour-ish prefixes, so a dead
 *      `animate-fade-in` (4 uses), a bare `rounded` (26) and 86 arbitrary
 *      `text-[9px]`-style sizes were invisible to it. We check every utility.
 *
 * Usage:  node scripts/check-utilities.mjs [path/to/built.css]
 * Exit 1 if any referenced utility produces no rule.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, '..', 'src');
const CSS_DIR = resolve(HERE, '..', '..', 'static', 'react', 'assets');

/**
 * One variant segment, e.g. `hover:` or `group-hover:`. Applied in a loop
 * rather than as `(?:…+:)+` — the nested quantifier is a ReDoS shape and
 * eslint-plugin-security flags it.
 */
const VARIANT_SEGMENT = /^[a-z0-9@[\]&_.>~+-]+:/i;

/**
 * Utilities we never expect to find in CSS because they are not CSS-producing:
 * `group`/`peer` are markers, and arbitrary properties carry their own value.
 */
const NOT_A_RULE = new Set(['group', 'peer', 'sr-only-focusable']);

function walk(dir, out = []) {
  for (const e of readdirSync(dir)) {
    const p = join(dir, e);
    if (statSync(p).isDirectory()) {
      if (e !== '__tests__' && e !== 'node_modules') walk(p, out);
    } else if (p.endsWith('.tsx') || p.endsWith('.ts')) out.push(p);
  }
  return out;
}

/** Pull class strings out of className="..." , '...' and `...` (incl. template literals). */
function extractClassStrings(src) {
  const out = [];
  const re = /class(?:Name)?\s*=\s*(?:"([^"]*)"|'([^']*)'|\{`([\s\S]*?)`\}|\{([\s\S]*?)\})/g;
  let m;
  while ((m = re.exec(src))) {
    const body = m[1] ?? m[2] ?? m[3] ?? m[4] ?? '';
    // Inside an expression, only quoted/backticked runs are class lists.
    if (m[4] !== undefined) {
      const inner = /["'`]([^"'`]*)["'`]/g;
      let q;
      while ((q = inner.exec(body))) out.push(q[1]);
    } else {
      out.push(body);
    }
  }
  return out;
}

function baseUtility(token) {
  // Class lists are harvested out of JSX expressions, so tokens arrive with
  // stray quotes, braces and — worse — bare JS identifiers from ternaries.
  // Everything below exists to throw those away without also throwing away
  // real utilities like `w-[1.5px]` or `bg-black/10`.
  let t = token.trim().replace(/^[`'"{(]+/, '').replace(/[`'"})]+$/, '');
  if (!t) return null;
  if (t.includes('${') || t.includes('$')) return null;

  t = t.replace(/^!/, '');             // leading important
  while (VARIANT_SEGMENT.test(t)) t = t.replace(VARIANT_SEGMENT, ''); // hover: md: …
  t = t.replace(/^!/, '');             // `hover:!bg-x`
  if (!t) return null;

  // An arbitrary value may legally contain almost anything; outside brackets a
  // utility is lowercase kebab with optional `/opacity`. Uppercase or a dot
  // outside brackets means we caught an identifier or a property access.
  const outside = t.replace(/\[[^\]]*\]/g, '');
  if (/[A-Z]/.test(outside)) return null;
  if (outside.includes('.')) return null;
  if (!/^[a-z]/.test(t)) return null;
  if (!/^[a-z0-9\-[\]/%.,#()_:+*]+$/i.test(t)) return null;

  return t;
}

const cssPath =
  process.argv[2] ??
  readdirSync(CSS_DIR)
    .filter((f) => f.endsWith('.css'))
    .map((f) => join(CSS_DIR, f))
    .sort((a, b) => statSync(b).size - statSync(a).size)[0];

if (!cssPath) {
  console.error('check-utilities: no built stylesheet found. Run `npm run build` first.');
  process.exit(2);
}

// Strip Tailwind's selector escaping so `.\!bg-plate` matches `bg-plate`.
const css = readFileSync(cssPath, 'utf8').replace(/\\/g, '');

const used = new Map(); // base utility -> Set(files)
for (const file of walk(SRC)) {
  const src = readFileSync(file, 'utf8');
  for (const chunk of extractClassStrings(src)) {
    for (const token of chunk.split(/\s+/)) {
      const base = baseUtility(token);
      if (!base || NOT_A_RULE.has(base)) continue;
      if (!used.has(base)) used.set(base, new Set());
      used.get(base).add(file.slice(SRC.length + 1));
    }
  }
}

/** A utility is satisfied if its name appears in a selector position. */
function emitted(base) {
  const i = css.indexOf(base);
  if (i === -1) return false;
  // Must be preceded by `.`, `:` or `!` (selector context) at least once.
  let from = 0;
  for (;;) {
    const at = css.indexOf(base, from);
    if (at === -1) return false;
    const prev = css[at - 1];
    const next = css[at + base.length];
    const boundaryOk = next === undefined || !/[a-z0-9-]/i.test(next);
    if (boundaryOk && (prev === '.' || prev === ':' || prev === '!')) return true;
    from = at + 1;
  }
}

const absent = [...used.keys()].filter((u) => !emitted(u)).sort();

/**
 * A bare lowercase word with no hyphen, slash or bracket is far more likely to
 * be a string compared in a ternary (`mode === 'folder'`) than a utility, and
 * this harvester cannot tell them apart. Those are reported but do not fail the
 * build; anything shaped like a utility does.
 */
const looksLikeUtility = (u) => /[-/[]/.test(u);
const missing = absent.filter(looksLikeUtility);
const unverified = absent.filter((u) => !looksLikeUtility(u));

console.log(`stylesheet : ${cssPath.split(/[\\/]/).pop()}`);
console.log(`utilities  : ${used.size} distinct, referenced in src/`);

if (unverified.length) {
  console.log(`unverified : ${unverified.length} bare words, probably string values — ${unverified.join(', ')}`);
}

if (missing.length === 0) {
  console.log('result     : OK — every referenced utility produces a rule');
  process.exit(0);
}

console.log(`result     : ${missing.length} utilities produce NO CSS\n`);
for (const u of missing) {
  const files = [...used.get(u)].sort();
  console.log(`  ${u}`);
  for (const f of files.slice(0, 4)) console.log(`      ${f}`);
  if (files.length > 4) console.log(`      … and ${files.length - 4} more`);
}
process.exit(1);
