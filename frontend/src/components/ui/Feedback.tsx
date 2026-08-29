import type { ReactNode } from 'react';
import { Moon, Sun } from 'lucide-react';
import { resolveTheme, setTheme, type Theme } from '../../theme';
import { useState } from 'react';

export type Tone = 'neutral' | 'success' | 'warning' | 'error' | 'info' | 'accent';

const TONE_TEXT: Record<Tone, string> = {
  neutral: 'text-text-secondary',
  success: 'text-success',
  warning: 'text-warning',
  error: 'text-error',
  info: 'text-info',
  accent: 'text-primary',
};

const TONE_DOT: Record<Tone, string> = {
  neutral: 'bg-text-tertiary',
  success: 'bg-success',
  warning: 'bg-warning',
  error: 'bg-error',
  info: 'bg-info',
  accent: 'bg-plate',
};

/**
 * A status chip. The surface and edge stay constant across tones and only the
 * text and dot carry the colour — a tinted fill per tone would put six
 * differently-lit shapes on one row.
 */
export function Badge({
  tone = 'neutral',
  dot = false,
  mono = false,
  className = '',
  children,
}: Readonly<{ tone?: Tone; dot?: boolean; mono?: boolean; className?: string; children: ReactNode }>) {
  return (
    <span
      className={
        `inline-flex items-center gap-1.5 h-6 px-2.5 rounded-sm text-xs bg-surface border border-edge ` +
        `${TONE_TEXT[tone]} ${mono ? 'font-mono text-[11px]' : ''} ${className}`
      }
    >
      {dot && <span aria-hidden className={`w-1.5 h-1.5 rounded-full shrink-0 ${TONE_DOT[tone]}`} />}
      {children}
    </span>
  );
}

/**
 * Flat blocks, deliberately without a shimmer sweep: a moving gradient is the
 * glow language in a different costume. The app had `animate-spin` in 30 places
 * and zero skeletons — a spinner says "wait", a skeleton says what is coming.
 */
export function Skeleton({ className = '' }: Readonly<{ className?: string }>) {
  return <div aria-hidden className={`bg-raised rounded-sm ${className}`} />;
}

export function SkeletonText({ lines = 3 }: Readonly<{ lines?: number }>) {
  const widths = ['w-5/12', 'w-11/12', 'w-9/12', 'w-10/12', 'w-7/12'];
  return (
    <div className="flex flex-col gap-2" aria-hidden>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={`h-3 ${widths[i % widths.length]}`} />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  body,
  actions,
}: Readonly<{ title: string; body?: ReactNode; actions?: ReactNode }>) {
  return (
    <div className="bg-surface border border-rule rounded-xl px-8 py-9">
      <div className="font-mono text-[10px] tracking-[0.16em] uppercase text-text-tertiary mb-4">Empty</div>
      <div className="font-serif text-xl mb-2">{title}</div>
      {body && <p className="font-serif text-base text-text-secondary leading-relaxed max-w-[46ch] m-0">{body}</p>}
      {actions && <div className="flex gap-2.5 mt-5">{actions}</div>}
    </div>
  );
}

export function ErrorState({
  title,
  body,
  actions,
}: Readonly<{ title: string; body?: ReactNode; actions?: ReactNode }>) {
  return (
    <div className="bg-surface border border-rule border-l-2 border-l-error rounded-xl px-8 py-9" role="alert">
      <div className="font-mono text-[10px] tracking-[0.16em] uppercase text-error mb-4">Error</div>
      <div className="font-serif text-xl mb-2">{title}</div>
      {body && <p className="font-serif text-base text-text-secondary leading-relaxed max-w-[48ch] m-0">{body}</p>}
      {actions && <div className="flex gap-2.5 mt-5">{actions}</div>}
    </div>
  );
}

/**
 * A citation, set as a shelf mark.
 *
 * Carries exactly what `QuerySource` provides — file name, folder tag, chunk id
 * and score. There are no section anchors in the data, so this does not invent
 * them.
 */
export function ShelfMark({
  marker,
  file,
  folder,
  chunkId,
  score,
  inferred = false,
}: Readonly<{
  marker: string;
  file: string;
  folder?: string;
  chunkId?: number;
  score?: number;
  inferred?: boolean;
}>) {
  return (
    <div className="flex gap-2 min-w-0">
      <span className={`font-mono text-[10px] shrink-0 ${inferred ? 'text-text-tertiary' : 'text-primary'}`}>
        {marker}
      </span>
      <div className="min-w-0">
        {inferred ? (
          <div className="font-mono text-[10px] tracking-wider uppercase text-text-tertiary leading-relaxed">
            Inferred — not grounded in a retrieved passage
          </div>
        ) : (
          <>
            <div className="font-mono text-[11px] text-text-secondary leading-relaxed truncate">{file}</div>
            {folder && (
              <div className="font-mono text-[10px] text-text-tertiary leading-relaxed truncate">{folder}</div>
            )}
            {(chunkId !== undefined || score !== undefined) && (
              <div className="font-mono text-[10px] text-text-tertiary leading-relaxed">
                {chunkId !== undefined ? `chunk ${chunkId}` : ''}
                {chunkId !== undefined && score !== undefined ? ' · ' : ''}
                {score !== undefined ? score.toFixed(2) : ''}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** Cabinet / Paper. Writes the choice through so it survives a reload. */
export function ThemeToggle({ className = '' }: Readonly<{ className?: string }>) {
  const [theme, setLocal] = useState<Theme>(() => resolveTheme());

  const choose = (next: Theme) => {
    setTheme(next);
    setLocal(next);
  };

  return (
    <div className={`inline-flex border border-edge rounded-sm overflow-hidden ${className}`} role="group" aria-label="Theme">
      <button
        type="button"
        onClick={() => choose('cabinet')}
        aria-pressed={theme === 'cabinet'}
        title="Cabinet — dark"
        className={`w-7 h-5 flex items-center justify-center transition-colors ${
          theme === 'cabinet' ? 'bg-surface text-text-primary' : 'text-text-tertiary hover:text-text-secondary'
        }`}
      >
        <Moon className="w-3 h-3" />
      </button>
      <button
        type="button"
        onClick={() => choose('paper')}
        aria-pressed={theme === 'paper'}
        title="Paper — light"
        className={`w-7 h-5 flex items-center justify-center border-l border-edge transition-colors ${
          theme === 'paper' ? 'bg-surface text-text-primary' : 'text-text-tertiary hover:text-text-secondary'
        }`}
      >
        <Sun className="w-3 h-3" />
      </button>
    </div>
  );
}
