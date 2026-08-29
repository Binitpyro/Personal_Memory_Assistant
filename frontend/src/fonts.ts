/**
 * Self-hosted webfaces, and the gate that stops them popping in.
 *
 * Vendored via @fontsource so nothing is fetched from a CDN at runtime: a
 * Google Fonts <link> would be a network call on first run, which contradicts
 * the privacy-first default the whole product rests on.
 *
 * Every @fontsource stylesheet ships `font-display: swap`, so the serif would
 * otherwise render in the fallback and then visibly re-flow to Newsreader.
 * `initFonts` adds `fonts-ready` to <html> once the faces resolve; index.css
 * holds serif text back until then.
 *
 * The race against a timeout is the important part: `document.fonts.ready` can
 * hang if a face fails to decode, and text hidden forever is far worse than
 * text in a fallback. Whichever settles first wins.
 */
import '@fontsource-variable/newsreader';
import '@fontsource-variable/ibm-plex-sans';
import '@fontsource/ibm-plex-mono/400.css';
import '@fontsource/ibm-plex-mono/500.css';

const READY_CLASS = 'fonts-ready';
const MAX_BLOCK_MS = 800;

export function initFonts(): void {
  const mark = () => document.documentElement.classList.add(READY_CLASS);

  // jsdom and older engines have no FontFaceSet; never gate on it there.
  if (typeof document === 'undefined' || !('fonts' in document)) {
    mark();
    return;
  }

  const settled = document.fonts.ready.then(() => undefined).catch(() => undefined);
  const timeout = new Promise<void>((resolve) => setTimeout(resolve, MAX_BLOCK_MS));
  void Promise.race([settled, timeout]).then(mark);
}
