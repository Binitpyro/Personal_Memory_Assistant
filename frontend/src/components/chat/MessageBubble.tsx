import { memo, useState } from 'react';
import { Link } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import { type Message } from '../../hooks/useChatStream';
import { type TraceEvent } from '../../api';
import { CrystalGraphTrace } from '../CrystalGraphTrace';

/**
 * Sanitisation schema for model output.
 *
 * `rehypeRaw` is load-bearing, not decorative: llm_client.py instructs the model
 * to wrap grounded assertions in `<claim sources="[n]">`, capability_detector.py
 * probes whether it can, and the `components` map below turns those tags into
 * the citation UI. Dropping raw HTML would render them as literal text.
 *
 * But the model's answer is derived from documents the user did not write, so it
 * is untrusted input: a poisoned chunk can steer the model into emitting
 * `<img src=x onerror=...>`, and `window.__PMA_TOKEN__` sits in the same page
 * authorising every /api/ route. The CSP blocks the exfiltration channels in the
 * browser, but Tauri ships `script-src 'self' 'unsafe-inline'`
 * (tauri.conf.json), where an inline handler *would* run. This closes it at the
 * source instead.
 *
 * Extends the GitHub default rather than replacing it, so everything remark-gfm
 * legitimately emits - tables, code, lists, links - keeps rendering. Only the
 * two custom tags and their one attribute are added. `on*` handlers are not in
 * the default allowlist and are therefore dropped.
 */
const claimSchema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), 'claim', 'inference'],
  attributes: {
    ...defaultSchema.attributes,
    claim: ['sources'],
    inference: ['sources'],
  },
};

/**
 * Renders the bounded retrieval loop's trace.
 *
 * The not-found list leads and is always visible: reporting "nothing in your
 * research notes on this" is the thing a chatbot with search cannot do, and it
 * is worthless buried inside a collapsed panel. The step-by-step breakdown is
 * supporting detail and stays folded away.
 */
const ReasoningTrace = ({ trace }: Readonly<{ trace: TraceEvent[] }>) => {
  const [isOpen, setIsOpen] = useState(false);

  const notFound = trace.find((e) => e.kind === 'not_found');
  const missing = notFound?.subqueries ?? [];
  const steps = trace.filter((e) => e.kind === 'decompose' || e.kind === 'retrieve');
  const summary = trace.find((e) => e.kind === 'done');

  if (steps.length === 0 && missing.length === 0) return null;

  return (
    <div className="mt-2 flex flex-col gap-2">
      {missing.length > 0 && (
        <div className="bg-surface border border-rule rounded-lg overflow-hidden">
          <div className="px-3 py-2 flex items-center gap-2 text-text-primary text-xs font-bold border-b border-rule">
            Searched for, but not found in your files
          </div>
          <div className="p-3 text-xs text-text-secondary flex flex-col gap-1.5">
            {missing.map((q) => (
              <span key={q} className="flex items-start gap-2">
                <span className="text-text-tertiary mt-px">–</span>
                <span>{q}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {steps.length > 0 && (
        <div className="border border-primary/20 rounded-xl overflow-hidden bg-surface-dark/30">
          <button
            type="button"
            onClick={() => setIsOpen(!isOpen)}
            aria-expanded={isOpen}
            className="w-full flex items-center justify-between px-3 py-2 text-xs font-bold text-primary-light hover:bg-primary/5 transition-colors"
          >
            <span className="flex items-center gap-1.5">
              How this answer was assembled
            </span>
            <span aria-hidden>{isOpen ? '▾' : '›'}</span>
          </button>
          {isOpen && (
            <div className="p-3 border-t border-primary/10 flex flex-col gap-2 text-xs text-text-secondary">
              {steps.map((e, i) => (
                <div key={`${e.kind}-${i}`} className="flex items-start gap-2">
                  <span className="text-primary-light/60 font-mono text-xs mt-0.5 shrink-0">
                    {e.kind}
                  </span>
                  <span>{e.detail}</span>
                </div>
              ))}
              {summary && (
                <div className="mt-1 pt-2 border-t border-rule text-xs text-text-secondary/70">
                  {summary.detail}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const GraphTraceViewer = ({ traceData }: Readonly<{ traceData: string }>) => {
  const [isOpen, setIsOpen] = useState(false);
  return (
    <div className="mt-3 border border-primary/20 rounded-xl overflow-hidden bg-surface-dark/30">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        aria-expanded={isOpen}
        className="w-full flex items-center justify-between px-3 py-2 text-xs font-bold text-primary-light hover:bg-primary/5 transition-colors"
      >
        <span className="flex items-center gap-1.5">Graph Trace: Crystal Dreamscape</span>
        <span aria-hidden>{isOpen ? '▾' : '›'}</span>
      </button>
      {isOpen && (
        <div className="p-3 border-t border-primary/10">
          <CrystalGraphTrace traceData={traceData} />
          <div className="mt-4 text-xs font-mono text-text-secondary overflow-x-auto whitespace-pre p-2 bg-raised rounded-md">
            {traceData}
          </div>
        </div>
      )}
    </div>
  );
};

const ERROR_SENTENCES: Record<string, string> = {
  context_overflow: "The question plus its sources is too long for this model's context. Narrow the folder/type filter, or load the model with a larger context.",
  empty_answer: 'The model returned an empty reply. Try again or pick another model.',
  local_provider_down: "The local model server isn't running. Start Ollama/LM Studio and try again.",
};

/** A fault: fog, a title, what still worked. Lives in the turn that failed, so it cannot scroll away from it. */
const StreamError = ({ error, hasContent }: Readonly<{ error: NonNullable<Message['error']>; hasContent: boolean }>) => (
  <div className="border border-warning p-3 text-sm" role="alert">
    <span className="block font-mono text-[10.5px] font-semibold tracking-[.12em] uppercase text-warning mb-1">
      <span aria-hidden>■ </span>{hasContent ? 'Answer cut short' : 'No answer written'}
    </span>
    {(error.code && ERROR_SENTENCES[error.code]) || error.text}
    {error.code === 'cloud_consent_required' && (
      <Link to="/settings/providers#cloud-consent" className="block mt-2 underline underline-offset-4 font-semibold">
        Review cloud settings
      </Link>
    )}
  </div>
);

export interface MessageBubbleProps {
  readonly message: Message;
  /** The newest question is set at 40px; earlier ones at pane size. */
  readonly latest?: boolean;
}

/**
 * One turn in the Answer pane: a question in stock, or an answer in print with
 * what the loop reported about it. Its frames and receipt are not here — they
 * have their own panes (FramesPanel, Receipt).
 *
 * Memoised: the reducer keeps the identity of every message but the streaming
 * one, so a 50 ms flush re-parses only that answer instead of the whole chat.
 */
export const MessageBubble = memo(function MessageBubble({ message: msg, latest = false }: Readonly<MessageBubbleProps>) {
  const [annotationsOpen, setAnnotationsOpen] = useState(true);

  // The question, set in stock. Safelight has no avatars and no bubbles.
  if (msg.role === 'user') {
    return <h2 className={`sl-answer__q sl-stock ${latest ? 'sl-answer__q--latest' : ''}`}>{msg.content}</h2>;
  }

  return (
    <div className="min-w-0">
      <div className="flex flex-col gap-2 min-w-0 items-start">
        {/* A failed turn with no text shows only its error, not an empty bubble. */}
        {!(msg.error && !msg.content.trim()) && <div className="max-w-[60ch]">
          {msg.isStreaming && !msg.content ? (
            <div className="edge-type text-text-tertiary py-1" role="status" aria-label="Generating answer…">
              Writing…
            </div>
          ) : (
            // `prose prose-invert prose-sm` emitted no CSS at all —
            // @tailwindcss/typography is not a dependency of this project.
            <div
              className="prose-answer max-w-none"
              aria-live={msg.role === 'assistant' && msg.isStreaming ? 'polite' : undefined}
              aria-busy={msg.isStreaming || undefined}
            >
              <ReactMarkdown 
                remarkPlugins={[remarkGfm]}
                // Order is load-bearing: rehypeRaw parses the raw HTML into the
                // tree, rehypeSanitize then strips what is not allowlisted.
                // Reversed, sanitisation runs before the dangerous nodes exist.
                rehypePlugins={[rehypeRaw, [rehypeSanitize, claimSchema]]}
                components={{
                  // The citation detail used to live ONLY in `title`, which is
                  // a mouse-hover affordance: not focusable, not announced by
                  // screen readers, and unreachable by keyboard. It stays for
                  // pointer users, but the same text now also renders as an
                  // inline sr-only suffix so it is read as part of the
                  // sentence. Deliberately NOT a tab stop — a focusable span
                  // per citation would put dozens of stops inside one answer.
                  claim: ({ sources, node, children, ...rest }: Readonly<Record<string, any>>) => {
                    const sourcesStr = String(sources || "");
                    const isInference = sourcesStr.toLowerCase().includes("inference");
                    const numSources = (sourcesStr.match(/\[\d+\]/g) || []).length;

                    if (isInference) {
                      return (
                        <span className="text-text-secondary px-0.5 border-b border-dotted border-warning/70" title="Inference (Ungrounded)" {...rest}>
                          {children}
                          <span className="sr-only"> (inference — not grounded in a retrieved passage)</span>
                        </span>
                      );
                    }
                    
                    // D10: this used to paint >=3 citations green and call it
                    // "High Confidence". The only input is how many [n] tokens
                    // the model emitted in the attribute - not a relevance
                    // score, not a reranker score, and not any check that the
                    // cited chunk supports the sentence. A model that cites
                    // three times confidently and wrongly scored highest, which
                    // is the "hallucination with a citation" failure mode
                    // exactly. Report the citation count, which is what is
                    // actually known, and leave the confidence judgement to the
                    // reader until a real signal exists to key on.
                    if (numSources >= 3) {
                      return (
                        <span className="underline decoration-accent/60 decoration-2 underline-offset-4 px-1 rounded cursor-help" title={`Cited ${numSources} sources: ${sourcesStr}`} {...rest}>
                          {children}
                          <span className="sr-only"> (cited {numSources} sources: {sourcesStr})</span>
                        </span>
                      );
                    }

                    const label = numSources === 1 ? `Cited 1 source: ${sourcesStr}` : `Sources: ${sourcesStr}`;
                    return (
                      <span className="underline decoration-accent/40 decoration-1 underline-offset-4 hover:bg-primary/5 px-1 rounded transition-colors cursor-help" title={label} {...rest}>
                        {children}
                        <span className="sr-only"> ({label})</span>
                      </span>
                    );
                  },
                  inference: ({ node, children, ...rest }: Readonly<Record<string, any>>) => (
                    <span className="text-text-secondary px-0.5 border-b border-dotted border-warning/70" title="Inference (Ungrounded)" {...rest}>
                      {children}
                      <span className="sr-only"> (inference — not grounded in a retrieved passage)</span>
                    </span>
                  )
                } as Record<string, React.ComponentType<any>>}
              >
                {msg.content}
              </ReactMarkdown>
            </div>
          )}
        </div>}

        {msg.error && (
          <div className="max-w-[60ch] w-full">
            <StreamError error={msg.error} hasContent={!!msg.content.trim()} />
          </div>
        )}

        {/* Stopped by the user. Rendered separately from the mode badge below,
            which only appears once sources arrive - a stream stopped before
            that point has no mode and would otherwise be indistinguishable
            from an answer that simply ended. */}
        {msg.role === 'assistant' && msg.stopped && (
          <span className="edge-type mt-1 px-2 py-0.5 border border-rule text-text-secondary">
            Stopped · partial answer
          </span>
        )}


        {/* Contradictions Banner */}
        {msg.role === 'assistant' && msg.contradictions_found && (
          <div className="mt-2 bg-surface border border-rule border-l-2 border-l-warning rounded-lg px-3 py-2 flex items-start gap-2 text-text-secondary text-xs">
            <span className="text-warning mt-0.5">⚠️</span>
            <div className="flex flex-col">
              <span className="font-bold text-warning">Potential Source Conflicts</span>
              {(() => {
                // The banner used to assert a conflict and never say where, which
                // the reader could neither check nor dismiss. Name the files it
                // is actually pointing at.
                const ids = new Set((msg.contradiction_sources ?? []).map(String));
                const named = (msg.sources ?? [])
                  .filter(src => ids.has(String(src.chunk_id)))
                  .map(src => src.file_path.split(/[\\/]/).pop())
                  .filter((n, i, a): n is string => !!n && a.indexOf(n) === i);
                if (named.length === 0) {
                  return <span>Some retrieved passages may disagree. Check the sources below.</span>;
                }
                return (
                  <span>
                    Possible disagreement in {named.join(', ')} — worth reading those passages yourself.
                  </span>
                );
              })()}
            </div>
          </div>
        )}

        {/* Bounded retrieval loop trace (agentic mode only) */}
        {msg.role === 'assistant' && msg.trace && msg.trace.length > 0 && (
          <ReasoningTrace trace={msg.trace} />
        )}

        {/* Knowledge Gaps Panel */}
        {msg.role === 'assistant' && msg.knowledge_gaps && msg.knowledge_gaps.length > 0 && (
          <div className="mt-2 bg-surface border border-rule rounded-lg overflow-hidden">
            <div className="px-3 py-2 flex items-center gap-2 text-text-primary text-xs font-bold border-b border-rule">
              What You Don't Know
            </div>
            <div className="p-3 text-xs text-text-secondary flex flex-wrap gap-2">
              {msg.knowledge_gaps.map((gap, i) => (
                <span key={i} className="bg-raised px-2 py-1 rounded-md border border-edge">
                  {gap}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Pattern Annotator Panel */}
        {msg.role === 'assistant' && msg.pattern_annotations && msg.pattern_annotations.length > 0 && (
          <div className="mt-2 bg-surface border border-rule rounded-lg overflow-hidden">
            <button
              type="button"
              onClick={() => setAnnotationsOpen(!annotationsOpen)}
              aria-expanded={annotationsOpen}
              className="w-full px-3 py-2 flex items-center justify-between text-text-primary text-xs font-bold border-b border-rule hover:bg-raised transition-colors"
            >
              <span className="flex items-center gap-2">
                Personal Pattern Annotator
              </span>
              <span aria-hidden>{annotationsOpen ? '▾' : '›'}</span>
            </button>
            {annotationsOpen && (
              <div className="p-3 text-xs text-text-secondary flex flex-col gap-1.5">
                {msg.pattern_annotations.map((annotation, i) => (
                  <div key={i} className="flex items-start gap-1.5">
                    <span className="text-info mt-0.5">•</span>
                    <span>{annotation}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {msg.role === 'assistant' && msg.graph_hops && (
          <GraphTraceViewer traceData={msg.graph_hops} />
        )}
      </div>
    </div>
  );
});
