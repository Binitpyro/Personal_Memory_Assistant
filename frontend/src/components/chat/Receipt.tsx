import { useEffect, useState, type ReactNode } from 'react';
import { type Message } from '../../hooks/useChatStream';
import { useWorking } from '../ui';

/** `--duration-print` in safelight.css: how long the paper takes to feed out. */
const PRINT_MS = 1400;

const MODE_LABEL: Record<NonNullable<Message['mode']>, string> = {
  fast_path: 'Fast answer',
  degraded_rag: 'Degraded RAG',
  cached: 'Saved answer',
  full_rag: 'RAG Answer',
};

/**
 * The receipt: what an answer cost and what it rested on, printed on paper in
 * edge type. It feeds out of its slot when it mounts; key it on the message so
 * each new answer prints afresh. It prints only what the stream reported.
 *
 * Deliberately absent, though the Safelight board draws them:
 * - Route and "Left this machine". The stream never says which provider wrote
 *   the answer — only a fallback carries `to` — and useChatStream's provider
 *   is a guess kept for cost. A receipt that said "nothing left" on a guess
 *   would be the one claim this app cannot get wrong.
 * - Per-frame lines. The model's `[n]` numbers snippets after dedup and the
 *   per-file cap (context_builder.py), so they do not index `sources`.
 * - Receipt number and fingerprint: nothing produces them yet.
 */
export function Receipt({ msg }: Readonly<{ msg: Message }>) {
  // Printing is work too: the grain crawls until the paper is out.
  const [printing, setPrinting] = useState(true);
  useEffect(() => {
    const t = setTimeout(() => setPrinting(false), PRINT_MS);
    return () => clearTimeout(t);
  }, []);
  useWorking(printing);

  // Without a usage packet both figures are estimated in useChatStream.
  const approx = msg.isEstimatedCost ? '≈ ' : '';
  const tokens = msg.prompt_tokens != null || msg.completion_tokens != null
    ? (msg.prompt_tokens || 0) + (msg.completion_tokens || 0)
    : null;
  const near = msg.near_misses?.length ?? 0;
  const row = (k: string, v: ReactNode, mute = false) => (
    <div className={`sl-receipt__row ${mute ? 'sl-receipt__mute' : ''}`}>
      <span>{k}</span>
      <span>{v}</span>
    </div>
  );

  return (
    <div className="sl-receipt-wrap">
      <div className="sl-receipt__slot" aria-hidden="true" />
      <div className="sl-receipt__feed">
        <div className="sl-receipt is-printing" role="group" aria-label="Receipt">
          <div className="sl-receipt__head">
            <b className="sl-receipt__brand sl-stock">PMA</b>
            <span>Personal Memory Assistant</span>
          </div>
          <div className="sl-receipt__block">
            {msg.mode && row('Path', MODE_LABEL[msg.mode])}
            {msg.query_mode && row('Style', msg.query_mode)}
            {msg.fallbackTo && row('Backup model', msg.fallbackTo)}
          </div>
          <div className="sl-receipt__block">
            {row('Frames used', msg.sources?.length ?? 0)}
            {near > 0 && row('Near misses, not used', near, true)}
          </div>
          {((msg.latency_ms ?? 0) > 0 || tokens != null) && (
            <div className="sl-receipt__block">
              {msg.latency_ms != null && msg.latency_ms > 0 && row('Search', `${(msg.latency_ms / 1000).toFixed(2)} s`)}
              {tokens != null && row('Tokens', approx + tokens.toLocaleString())}
            </div>
          )}
          {msg.cost != null && (
            <div className="sl-receipt__total" title={msg.isEstimatedCost ? 'Estimated cost' : 'Token usage cost'}>
              <span>Total</span>
              <b className="sl-stock">{approx}${msg.cost.toFixed(4)}</b>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
