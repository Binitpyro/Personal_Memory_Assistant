/**
 * Theme resolution for the Specimen Cabinet system.
 *
 * Two themes: `cabinet` (dark, the default) and `paper` (light).
 * `index.css` carries cabinet values on `:root`, swaps to paper under
 * `prefers-color-scheme: light` when the user has NOT chosen, and honours an
 * explicit `[data-theme]` in both directions.
 *
 * Deliberately NOT an inline <script> in index.html: `app/main.py` serves a
 * per-request nonce CSP whose only permitted inline script is the token
 * injection, and the built SPA is otherwise inline-script-free. Running this
 * from the module entry keeps that property intact. The cost is a possible
 * one-frame flash for a user whose OS scheme disagrees with their stored
 * choice; the common cases (no choice, or choice matching the OS) do not
 * flash, because the CSS already resolves them without JS.
 */

export type Theme = 'cabinet' | 'paper';

const STORAGE_KEY = 'pma_theme';

/** The user's explicit choice, or null when they have never picked one. */
export function getStoredTheme(): Theme | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return v === 'cabinet' || v === 'paper' ? v : null;
  } catch {
    // Private mode / blocked storage. Fall back to the OS.
    return null;
  }
}

/** What is actually on screen, whether chosen or inherited from the OS. */
export function resolveTheme(): Theme {
  const stored = getStoredTheme();
  if (stored) return stored;
  const prefersLight =
    typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: light)').matches;
  return prefersLight ? 'paper' : 'cabinet';
}

/** Persist a choice and apply it. */
export function setTheme(theme: Theme): void {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Non-fatal: the attribute below still applies for this session.
  }
  document.documentElement.setAttribute('data-theme', theme);
}

/** Clear the choice and follow the OS again. */
export function clearTheme(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Non-fatal.
  }
  document.documentElement.removeAttribute('data-theme');
}

/**
 * Apply the stored choice, if any, as early as the module graph allows.
 * When there is no stored choice the attribute is left OFF on purpose, so
 * the CSS `prefers-color-scheme` branch keeps tracking the OS live.
 */
export function initTheme(): void {
  const stored = getStoredTheme();
  if (stored) document.documentElement.setAttribute('data-theme', stored);
}
