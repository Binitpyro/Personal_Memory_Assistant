import { createContext, useContext, useEffect, useRef, useState } from 'react';

/**
 * Safelight's motion and figure primitives, ported from the Safelight Design
 * System artifact (project/components/src). Styles live in src/safelight.css.
 */

/** Tracks prefers-reduced-motion live. With it on, every state still changes, instantly. */
export function useReducedMotion(): boolean {
  const q = '(prefers-reduced-motion: reduce)';
  const [reduced, setReduced] = useState(() => typeof matchMedia === 'function' && matchMedia(q).matches);
  useEffect(() => {
    if (typeof matchMedia !== 'function') return;
    const m = matchMedia(q);
    const sync = () => setReduced(m.matches);
    m.addEventListener('change', sync);
    return () => m.removeEventListener('change', sync);
  }, []);
  return reduced;
}

/**
 * Work reported to the window's one Grain, as a count so overlapping work
 * (a search while an index runs) stops the grain only when the last ends.
 * AppShell provides it; outside AppShell a report goes nowhere.
 */
const WorkContext = createContext<(delta: number) => void>(() => {});
export const WorkProvider = WorkContext.Provider;

/** Keeps the grain crawling while `on` is true, and stops it if the caller unmounts mid-work. */
export function useWorking(on: boolean) {
  const report = useContext(WorkContext);
  useEffect(() => {
    if (!on) return;
    report(1);
    return () => report(-1);
  }, [on, report]);
}

/**
 * Film grain over the whole window, so the room itself says whether PMA is
 * busy: it crawls at 10 fps while PMA searches, writes, prints or indexes and
 * freezes the moment it stops. Frozen with reduced motion; hidden in a Windows
 * contrast theme. Render it once, in AppShell.
 */
export function Grain({ busy }: Readonly<{ busy: boolean }>) {
  const ref = useRef<HTMLCanvasElement>(null);
  const reduced = useReducedMotion();
  useEffect(() => {
    const c = ref.current;
    // jsdom has no 2D context; nothing to draw there.
    const ctx = c?.getContext?.('2d');
    if (!c || !ctx) return;
    const size = () => {
      const r = c.getBoundingClientRect();
      // A third of the resolution: the noise is texture, not detail.
      c.width = Math.max(1, Math.ceil(r.width / 3));
      c.height = Math.max(1, Math.ceil(r.height / 3));
    };
    const draw = () => {
      const img = ctx.createImageData(c.width, c.height);
      const d = img.data;
      for (let i = 0; i < d.length; i += 4) {
        const v = (Math.random() * 255) | 0;
        d[i] = v; d[i + 1] = v; d[i + 2] = v; d[i + 3] = 255;
      }
      ctx.putImageData(img, 0, 0);
    };
    const onResize = () => { size(); draw(); };
    onResize();
    window.addEventListener('resize', onResize);
    const timer = busy && !reduced ? window.setInterval(draw, 100) : undefined;
    return () => {
      window.removeEventListener('resize', onResize);
      if (timer) window.clearInterval(timer);
    };
  }, [busy, reduced]);
  return <canvas ref={ref} className="sl-grain" aria-hidden="true" />;
}

const fmt = (n: number) => Math.round(n).toLocaleString();

/**
 * A figure that counts up once, the first time it appears, then changes in
 * place. While counting, the moving number is hidden from assistive tech and
 * the final value is read instead; once settled it is one plain text node.
 */
export function Tally({ value }: Readonly<{ value: number }>) {
  const reduced = useReducedMotion();
  // null means settled: render the value itself.
  const [shown, setShown] = useState<number | null>(reduced ? null : 0);
  const counted = useRef(false);
  useEffect(() => {
    if (counted.current || reduced) { setShown(null); return; }
    counted.current = true;
    let raf = 0;
    const t0 = performance.now();
    const step = (t: number) => {
      const p = Math.min(1, Math.max(0, (t - t0) / 1200));
      if (p >= 1) { setShown(null); return; }
      setShown(value * (1 - Math.pow(1 - p, 4)));
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value, reduced]);
  if (shown === null) return <span className="sl-tally">{fmt(value)}</span>;
  return (
    <span className="sl-tally">
      <span aria-hidden="true">{fmt(shown)}</span>
      <span className="sl-visually-hidden">{fmt(value)}</span>
    </span>
  );
}

export interface Figure {
  label: string;
  value: number;
  /** Set at half size in ink2: MB, files. */
  unit?: string;
}

/** Figures set as stock type on a single ruled line. Safelight has no stat cards. */
export function FigureLine({ items }: Readonly<{ items: Figure[] }>) {
  return (
    <dl className="sl-figures">
      {items.map((f) => (
        <div key={f.label} className="sl-figure">
          <dt>{f.label}</dt>
          <dd className="sl-stock">
            <Tally value={f.value} />
            {f.unit && <small> {f.unit}</small>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export interface StripFile {
  /** Bytes: the bar's height, on a log scale so small notes still show beside large scans. */
  size: number;
  /** How often it was used: the bar's brightness. */
  opens: number;
}

export interface StripGroup {
  name: string;
  count: number;
  detail?: string;
}

/**
 * The library as one roll of film: each indexed file is a frame. Height is its
 * size, brightness how often it has been used.
 */
export function FilmStrip({ files, groups, note }: Readonly<{ files: StripFile[]; groups: StripGroup[]; note?: string }>) {
  const maxS = Math.max(1, ...files.map((f) => f.size));
  const maxO = Math.max(1, ...files.map((f) => f.opens));
  return (
    <figure className="sl-strip">
      <div
        className="sl-strip__bars"
        role="img"
        aria-label={`${files.length} files. Height is size, brightness is how often each has been used.`}
      >
        {files.map((f, i) => (
          <span key={i} className="sl-strip__bar" style={{ height: `${Math.max(2, (Math.log1p(f.size) / Math.log1p(maxS)) * 100)}%` }}>
            <i style={{ opacity: 0.3 + 0.7 * (f.opens / maxO) }} />
          </span>
        ))}
      </div>
      <figcaption className="sl-strip__groups">
        {groups.map((g) => (
          <div key={g.name} className="sl-strip__group" style={{ flex: `${g.count} 1 0` }}>
            <b>{g.name}</b>
            <span>{g.detail ?? g.count}</span>
          </div>
        ))}
      </figcaption>
      {note && <p className="sl-strip__note m-0">{note}</p>}
    </figure>
  );
}

/** Work in progress as a row of cells: ink for done, the lamp for the one being read, outlines for what is left. */
export function CellProgress({ label, done, total, cells = 20 }: Readonly<{ label: string; done: number; total: number; cells?: number }>) {
  const filled = Math.min(cells, Math.floor((done / Math.max(1, total)) * cells));
  const width = String(total).length;
  return (
    <div className="sl-cells" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
      <div className="sl-cells__head">
        <span className="sl-cells__label">{label}</span>
        <span className="sl-cells__count">{String(done).padStart(width, '0')} / {total}</span>
      </div>
      <div className="sl-cells__grid" aria-hidden="true">
        {Array.from({ length: cells }, (_, i) => (
          <span key={i} className={i < filled ? 'is-done' : i === filled && done < total ? 'is-now' : undefined} />
        ))}
      </div>
    </div>
  );
}
