import { useState, type CSSProperties } from 'react';
import { type Message } from '../../hooks/useChatStream';
import { type QuerySource } from '../../api';
import { isTauri, openFile } from '../../utils/tauriShell';
import { formatScore } from '../../utils/format';

const fileName = (p: string) => p.split(/[\\/]/).pop() ?? p;
const pad2 = (n: number) => String(n).padStart(2, '0');

/**
 * The passage a frame came from. `sentence_offsets` ships as the literal
 * string "[]" on a default install (offsets are only computed when
 * PMA_SENTENCE_OFFSETS is set), so the highlight branch is guarded on the
 * PARSED array: gating on the string's truthiness once replaced every passage
 * with an empty fragment.
 */
function Passage({ src }: Readonly<{ src: QuerySource }>) {
  const text = src.text ?? '';
  if (src.sentence_offsets) {
    try {
      const offsets = JSON.parse(src.sentence_offsets) as [number, number][];
      if (Array.isArray(offsets) && offsets.length > 0) {
        return (
          <p className="sl-loupe__quote">
            {offsets.map(([start, end], i) => (
              <span key={i} className="relative group">
                <mark>{text.substring(start, end)}</mark>
                <span className="absolute -top-7 left-1/2 -translate-x-1/2 bg-surface border border-edge text-xs text-text-primary px-2 py-1 opacity-0 group-hover:opacity-100 pointer-events-none whitespace-nowrap z-10">
                  Precision match
                </span>{' '}
              </span>
            ))}
          </p>
        );
      }
    } catch (e) {
      console.error('Failed to parse sentence offsets', e);
    }
  }
  return <p className="sl-loupe__quote">{text}</p>;
}

/**
 * One frame. The row is the disclosure: file, folder and score, and it opens
 * the passage under itself. A near miss is marked ·· rather than numbered, and
 * keeps its way back in: force-including it re-asks with that chunk in context.
 */
function FrameRow({ src, n, under, index, onForceInclude }: Readonly<{
  src: QuerySource;
  n: number;
  under: boolean;
  index: number;
  onForceInclude?: () => void;
}>) {
  const [open, setOpen] = useState(false);
  const folder = src.folder_tag || src.file_path.split(/[\\/]/).slice(-2, -1)[0] || '';
  const cls = ['sl-frame', under && 'is-under', src._challenge_source && 'is-challenge', open && 'is-open'].filter(Boolean).join(' ');
  return (
    <li className={cls} style={{ '--i': index } as CSSProperties}>
      <button type="button" className="sl-frame__row" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="sl-frame__n" aria-hidden>{under ? '··' : pad2(n)}</span>
        <span className="sl-frame__file">
          <span className="sl-frame__name">{fileName(src.file_path)}</span>
          {(folder || src.chunk_id !== undefined) && (
            <span className="sl-frame__folder">
              {folder}{folder && src.chunk_id !== undefined ? ' · ' : ''}{src.chunk_id !== undefined ? `chunk ${src.chunk_id}` : ''}
            </span>
          )}
        </span>
        <span className="sl-frame__ev">{src.score !== undefined ? formatScore(src.score) : ''}</span>
      </button>
      {(open && src.text) || onForceInclude ? (
        <div className="sl-loupe">
          {open && src.text && <Passage src={src} />}
          <div className="sl-loupe__foot">
            {/* A browser tab cannot open a local file, so this is desktop-only. */}
            {open && isTauri && (
              <button type="button" className="tap-24 underline underline-offset-4 hover:text-text-primary" onClick={() => { void openFile(src.file_path); }} title={`Open ${src.file_path}`}>
                Open file
              </button>
            )}
            {onForceInclude && (
              <button
                type="button"
                className="tap-24 underline underline-offset-4 hover:text-text-primary"
                onClick={onForceInclude}
                title="Force include this chunk into context and re-query"
              >
                Force include
              </button>
            )}
          </div>
        </div>
      ) : null}
    </li>
  );
}

/**
 * The Frames column: what the latest answer rests on, in rank order, then a
 * lamp rule, then the near misses that were retrieved and not used.
 *
 * The Safelight board labels that rule "FLOOR EV 0.45". PMA's cut is not a
 * score threshold: near misses are the results past the top k
 * (retrieval.py, `results[: k + near_misses]`), and the reranker's floor is a
 * batch-dependent logit. So the rule says what is true — not used — and
 * prints no number.
 */
export function FramesPanel({ message, onForceInclude }: Readonly<{
  message?: Message;
  onForceInclude: (chunkId?: number) => void;
}>) {
  const sources = message?.sources ?? [];
  const near = message?.near_misses ?? [];

  const dates = sources.map(s => (s.modified_at ? new Date(s.modified_at).getTime() : 0)).filter(d => d > 0 && !Number.isNaN(d));
  const day = (t: number) => new Date(t).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  const span = dates.length ? (Math.min(...dates) === Math.max(...dates) ? day(dates[0]) : `${day(Math.min(...dates))} to ${day(Math.max(...dates))}`) : null;

  let empty: string | null = null;
  if (!message) empty = 'Nothing in frame yet. Ask, and the passages the answer rests on come up here.';
  else if (message.isStreaming && sources.length === 0) empty = 'Searching this machine…';
  else if (sources.length === 0 && near.length === 0) empty = 'No passages were used for this answer.';

  return (
    <section className="sl-frames" aria-label="Frames">
      <div className="sl-panehead">
        <span>Frames</span>
        {message && (sources.length > 0 || near.length > 0) && (
          <span>{sources.length} used{near.length > 0 ? ` · ${near.length} near` : ''}</span>
        )}
      </div>
      {empty ? (
        <p className="sl-frames__empty m-0" role={message?.isStreaming ? 'status' : undefined}>{empty}</p>
      ) : (
        // Keyed on the message, so a new answer's frames come up afresh.
        <ol key={message?.id} className="sl-frames__list">
          {sources.map((src, i) => (
            <FrameRow key={`${src.file_path}-${src.chunk_id ?? i}`} src={src} n={i + 1} under={false} index={i} />
          ))}
          {near.length > 0 && (
            <li className="sl-floor" aria-hidden>
              <span className="sl-floor__label">Not used</span>
            </li>
          )}
          {near.map((src, i) => (
            <FrameRow
              key={`near-${src.file_path}-${src.chunk_id ?? i}`}
              src={src}
              n={0}
              under
              index={sources.length + i}
              onForceInclude={() => onForceInclude(src.chunk_id)}
            />
          ))}
        </ol>
      )}
      {span && <p className="sl-frames__empty m-0 mt-auto border-t border-rule">Based on documents from {span}</p>}
    </section>
  );
}
