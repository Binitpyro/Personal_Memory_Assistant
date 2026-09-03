import { useState } from 'react';
import { Bot, User, Sparkles, ChevronDown, ChevronRight, Clock, Plus, Network, SearchX, Split, ExternalLink } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import { type Message } from '../../hooks/useChatStream';
import { type QuerySource, type TraceEvent } from '../../api';
import { CrystalGraphTrace } from '../CrystalGraphTrace';
import { isTauri, openFile } from '../../utils/tauriShell';
import { formatCurrency } from '../../utils/format';

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
            <SearchX className="w-4 h-4" />
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
              <Split className="w-3.5 h-3.5" aria-hidden /> How this answer was assembled
            </span>
            {isOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          </button>
          {isOpen && (
            <div className="p-3 border-t border-primary/10 flex flex-col gap-2 text-xs text-text-secondary">
              {steps.map((e, i) => (
                <div key={`${e.kind}-${i}`} className="flex items-start gap-2">
                  <span className="text-primary-light/60 font-mono text-[10px] mt-0.5 shrink-0">
                    {e.kind}
                  </span>
                  <span>{e.detail}</span>
                </div>
              ))}
              {summary && (
                <div className="mt-1 pt-2 border-t border-rule text-[10px] text-text-secondary/70">
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
        <span className="flex items-center gap-1.5"><Network className="w-3.5 h-3.5" aria-hidden /> Graph Trace: Crystal Dreamscape</span>
        {isOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
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

const SourceViewer = ({ src, onForceInclude }: Readonly<{ src: QuerySource, onForceInclude?: () => void }>) => {
  const [isOpen, setIsOpen] = useState(false);
  
  let content = <>{src.text}</>;
  if (src.text && src.sentence_offsets) {
    try {
      const offsets = JSON.parse(src.sentence_offsets) as [number, number][];
      // Guard on the PARSED value, not on the truthiness of the string. Every
      // chunk ships sentence_offsets as the literal string "[]" — the offsets
      // are only computed when PMA_SENTENCE_OFFSETS is set, and it defaults to
      // "0" (app/indexing/service.py). "[]" is truthy, so this branch was
      // always taken, offsets.map produced nothing, and `content` was
      // overwritten with an empty fragment — discarding the src.text assigned
      // just above. The panel opened (it is gated on src.text, which is fine)
      // and showed a blank box for every source.
      if (Array.isArray(offsets) && offsets.length > 0) {
        content = (
          <>
            {offsets.map(([start, end], i) => (
              <span key={i} className="hover:bg-primary/30 transition-colors rounded px-0.5 cursor-text relative group">
                {src.text!.substring(start, end)}
                <span className="absolute -top-7 left-1/2 -translate-x-1/2 bg-surface border border-edge text-[11px] text-text-primary px-2 py-1 rounded-sm shadow-md opacity-0 group-hover:opacity-100 pointer-events-none whitespace-nowrap z-10 transition-opacity">
                  Precision match
                </span>
              </span>
            ))}
          </>
        );
      }
    } catch (e) {
      console.error("Failed to parse sentence offsets", e);
    }
  }

  return (
    <div className="flex flex-col gap-1 w-full max-w-sm mt-1">
      {/* A shelf mark, not a chip. The answer is the text and its sources are
          the margin: file, folder tag, then the catalogue line (chunk id and
          score). Those four are exactly what QuerySource carries — there are no
          section anchors in the data, so none are invented here. */}
      <div className="flex items-start gap-1.5">
        <button
          onClick={() => setIsOpen(!isOpen)}
          className="flex items-start gap-2 text-left w-full group/mark"
          aria-expanded={isOpen}
        >
          <span
            aria-hidden
            className={`font-mono text-[10px] mt-px shrink-0 ${src._challenge_source ? 'text-error' : 'text-primary'}`}
          >
            {isOpen ? '−' : '+'}
          </span>
          <span className="min-w-0">
            <span className={`block font-mono text-[11px] leading-relaxed truncate group-hover/mark:text-primary transition-colors ${
              src._challenge_source ? 'text-error' : 'text-text-secondary'
            }`}>
              {src.file_path.split(/[\\/]/).pop()}
            </span>
            {src.folder_tag && (
              <span className="block font-mono text-[10px] leading-relaxed text-text-tertiary truncate">
                {src.folder_tag}
              </span>
            )}
            {(src.chunk_id !== undefined || src.score !== undefined) && (
              <span className="block font-mono text-[10px] leading-relaxed text-text-tertiary">
                {src.chunk_id !== undefined ? `chunk ${src.chunk_id}` : ''}
                {src.chunk_id !== undefined && src.score !== undefined ? ' · ' : ''}
                {src.score !== undefined ? src.score.toFixed(2) : ''}
              </span>
            )}
          </span>
        </button>
        {onForceInclude && (
          <button 
            onClick={(e) => { e.stopPropagation(); onForceInclude(); }}
            className="flex items-center gap-1 px-1.5 py-1 bg-primary/10 hover:bg-primary/20 transition-colors rounded-lg text-[10px] text-primary-light border border-primary/20 ml-auto"
            title="Force include this chunk into context and re-query"
          >
            <Plus className="w-3 h-3" />
            <span>Force Include</span>
          </button>
        )}
      </div>
      {isOpen && src.text && (
        <div className="flex flex-col gap-1">
          <div className="p-2 text-xs text-text-secondary bg-raised border border-rule rounded-lg max-h-48 overflow-y-auto whitespace-pre-wrap leading-relaxed">
            {content}
          </div>
          {/* Until now the expanded panel was a dead end: you could read the
              matched passage but had no way to reach the document it came
              from. Hidden outside the desktop shell, where a browser tab
              cannot open a local file. */}
          {isTauri && (
            <button
              onClick={() => { void openFile(src.file_path); }}
              className="flex items-center gap-1.5 px-2 py-1 self-start bg-raised hover:bg-raised transition-colors rounded-lg text-[10px] text-text-secondary border border-rule"
              title={`Open ${src.file_path}`}
            >
              <ExternalLink className="w-3 h-3 text-primary-light" />
              <span>Open file</span>
            </button>
          )}
        </div>
      )}
    </div>
  );
};

export interface MessageBubbleProps {
  readonly message: Message;
  readonly onNearMissClick: (suggestion: string, forcedChunkId?: number) => void;
}

export function MessageBubble({ message: msg, onNearMissClick }: Readonly<MessageBubbleProps>) {
  const [annotationsOpen, setAnnotationsOpen] = useState(true);
  // The '+N more' label was static text with nothing behind it, so sources
  // beyond the third were unreachable.
  const [showAllSources, setShowAllSources] = useState(false);

  return (
    <div className={`flex gap-4 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
      {msg.role === 'assistant' && (
        <div className="w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center shrink-0 border border-primary/30 shadow-lg">
          <Bot className="w-4 h-4 text-primary-light" />
        </div>
      )}
      <div className={`flex flex-col gap-2 max-w-[85%] ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
        <div className={`px-5 py-3 rounded-2xl text-sm leading-relaxed shadow-sm border ${msg.role === 'user'
          ? 'bg-plate text-on-plate border-primary-light/20 rounded-tr-none'
          : 'glass-card !p-3 text-text-primary border-edge rounded-tl-none'
          }`}>
          {msg.isStreaming && !msg.content ? (
            <div className="flex gap-1 py-1" role="status" aria-label="Generating answer…">
              <span aria-hidden className="w-1.5 h-1.5 bg-primary-light rounded-full animate-bounce"></span>
              <span aria-hidden className="w-1.5 h-1.5 bg-primary-light rounded-full animate-bounce [animation-delay:0.2s]"></span>
              <span aria-hidden className="w-1.5 h-1.5 bg-primary-light rounded-full animate-bounce [animation-delay:0.4s]"></span>
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
                        <span className="underline decoration-primary-light/60 decoration-2 underline-offset-4 px-1 rounded cursor-help" title={`Cited ${numSources} sources: ${sourcesStr}`} {...rest}>
                          {children}
                          <span className="sr-only"> (cited {numSources} sources: {sourcesStr})</span>
                        </span>
                      );
                    }

                    const label = numSources === 1 ? `Cited 1 source: ${sourcesStr}` : `Sources: ${sourcesStr}`;
                    return (
                      <span className="underline decoration-primary-light/40 decoration-1 underline-offset-4 hover:bg-primary/5 px-1 rounded transition-colors cursor-help" title={label} {...rest}>
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
        </div>

        {/* Stopped by the user. Rendered separately from the mode badge below,
            which only appears once sources arrive - a stream stopped before
            that point has no mode and would otherwise be indistinguishable
            from an answer that simply ended. */}
        {msg.role === 'assistant' && msg.stopped && (
          <div className="flex items-center gap-2 mt-1">
            <span className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full border bg-raised text-text-secondary border-rule">
              Stopped · partial answer
            </span>
          </div>
        )}

        {/* Mode Badge */}
        {msg.role === 'assistant' && !msg.isStreaming && msg.mode && (
          <div className="flex flex-wrap items-center gap-2 mt-1">
            {/* "cached" is provenance, not a warning: it states what happened
                rather than that something went wrong, so it reads at the same
                weight as the rest of the metadata row. Before this, a cached
                answer arrived with no sources and defaulted to "full_rag" -
                presented as fresh. */}
            <span className={`inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full border ${
              msg.mode === 'fast_path'
                ? 'bg-surface text-warning border-edge'
                : msg.mode === 'degraded_rag'
                  ? 'bg-surface text-warning border-edge'
                  : msg.mode === 'cached'
                    ? 'bg-raised text-text-secondary border-rule'
                    : 'bg-primary/10 text-primary-light border-primary/20'
              }`}>
              {msg.mode === 'fast_path'
                ? '⚡ Fast Answer'
                : msg.mode === 'degraded_rag'
                  ? '⚠️ Degraded RAG'
                  : msg.mode === 'cached'
                    ? '⟳ Saved answer'
                    : '🔍 RAG Answer'}
            </span>
            {msg.query_mode && (
              <span
                title="The answering style you selected"
                className={`inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full border capitalize ${
                  msg.query_mode === 'challenge'
                    ? 'bg-surface text-error border-error/40'
                    : 'bg-raised text-text-secondary border-rule'
                }`}
              >
                {msg.query_mode === 'challenge' ? '⚔' : '◆'} {msg.query_mode}
              </span>
            )}
            {msg.fallbackTo && (
              <span className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full border bg-surface text-error border-error/40">
                ⚠️ Backup: {msg.fallbackTo}
              </span>
            )}
            {msg.latency_ms != null && msg.latency_ms > 0 && (
              <span className="text-[10px] text-text-secondary/50">{msg.latency_ms.toFixed(0)}ms</span>
            )}
            {(msg.prompt_tokens != null || msg.completion_tokens != null) && (
              <span className="text-[10px] text-text-secondary/50">
                • {((msg.prompt_tokens || 0) + (msg.completion_tokens || 0)).toLocaleString()} tokens
              </span>
            )}
            {msg.cost != null && msg.cost > 0 && (
              <span className="text-[10px] text-success/80 font-semibold" title={msg.isEstimatedCost ? 'Estimated cost' : 'Token usage cost'}>
                • {msg.isEstimatedCost ? '~' : ''}{formatCurrency(msg.cost)}
              </span>
            )}
          </div>
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
              <Sparkles className="w-4 h-4" />
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
                <User className="w-4 h-4" aria-hidden />
                Personal Pattern Annotator
              </span>
              {annotationsOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
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

        {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
          // The provenance apparatus. A left rule and a mono column set this
          // apart from the answer as a different KIND of text — the margin of a
          // page rather than a row of chips under it.
          <div className="flex flex-col gap-3 mt-3 pl-4 border-l border-rule">
            <div className="font-mono text-[10px] tracking-[0.16em] uppercase text-text-tertiary">
              Provenance
            </div>
            {(showAllSources ? msg.sources : msg.sources.slice(0, 3)).map((src) => (
              <SourceViewer key={`${src.file_path}-${src.score || 0}`} src={src} />
            ))}
            {msg.sources.length > 3 && (
              <button
                onClick={() => setShowAllSources(v => !v)}
                className="font-mono text-[10px] text-text-tertiary self-start underline underline-offset-4 hover:text-primary transition-colors"
              >
                {showAllSources
                  ? 'Show fewer sources'
                  : `+${msg.sources.length - 3} more`}
              </button>
            )}
            {(() => {
              const dates = msg.sources
                ?.map(s => s.modified_at ? new Date(s.modified_at).getTime() : 0)
                .filter(d => d > 0 && !isNaN(d)) || [];
              if (dates.length > 0) {
                const minDate = new Date(Math.min(...dates)).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
                const maxDate = new Date(Math.max(...dates)).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
                const dateText = minDate === maxDate ? minDate : `${minDate} to ${maxDate}`;
                return (
                  <div className="text-[10px] text-text-secondary mt-1 flex items-center gap-1.5 border-t border-rule pt-2 pl-2">
                    <Clock className="w-3 h-3 opacity-70" />
                    Based on documents from {dateText}
                  </div>
                );
              }
              return null;
            })()}
          </div>
        )}

        {msg.role === 'assistant' && msg.near_misses && msg.near_misses.length > 0 && (
          <div className="mt-4 pt-4 border-t border-rule">
            <details className="group cursor-pointer">
              <summary className="text-[11px] font-medium text-text-secondary/70 hover:text-text-primary transition-colors flex items-center gap-2 select-none mb-2">
                <span className="w-4 h-4 flex items-center justify-center rounded-sm bg-surface-elevation-2/50 group-hover:bg-surface-elevation-3 transition-colors">
                  <span className="group-open:rotate-90 transition-transform text-[10px]">▶</span>
                </span>
                Near Misses ({msg.near_misses.length})
                <span className="text-[9px] text-text-secondary/50 font-normal ml-auto group-hover:text-text-secondary/80 transition-colors">Click to expand • May contain relevant context</span>
              </summary>
              <div className="flex flex-col gap-2 pl-6 mt-3 animate-in slide-in-from-top-2 duration-200">
                {msg.near_misses.map((src) => (
                  <SourceViewer 
                    key={`near-${src.file_path}-${src.score || 0}`} 
                    src={src} 
                    onForceInclude={() => onNearMissClick('', src.chunk_id)} 
                  />
                ))}
              </div>
            </details>
          </div>
        )}

        {msg.role === 'assistant' && msg.graph_hops && (
          <GraphTraceViewer traceData={msg.graph_hops} />
        )}
      </div>
      {msg.role === 'user' && (
        <div className="w-8 h-8 rounded-full bg-surface-lighter flex items-center justify-center shrink-0 border border-rule shadow-lg">
          <User className="w-4 h-4 text-text-secondary" />
        </div>
      )}
    </div>
  );
}
