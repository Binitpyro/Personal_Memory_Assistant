import { useState } from 'react';
import { Bot, User, Sparkles, RotateCcw, FileText, ChevronDown, ChevronRight, Clock, Plus, Network, SearchX, Split, ExternalLink } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import { type Message } from '../../hooks/useChatStream';
import { type QuerySource, type TraceEvent } from '../../api';
import { CrystalGraphTrace } from '../CrystalGraphTrace';
import { isTauri, openFile } from '../../utils/tauriShell';

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
        <div className="bg-slate-500/10 border border-slate-400/20 rounded-lg overflow-hidden">
          <div className="px-3 py-2 flex items-center gap-2 text-slate-200/90 text-xs font-bold border-b border-slate-400/10">
            <SearchX className="w-4 h-4" />
            Searched for, but not found in your files
          </div>
          <div className="p-3 text-xs text-slate-200/70 flex flex-col gap-1.5">
            {missing.map((q) => (
              <span key={q} className="flex items-start gap-2">
                <span className="text-slate-400 mt-px">–</span>
                <span>{q}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {steps.length > 0 && (
        <div className="border border-primary/20 rounded-xl overflow-hidden bg-surface-dark/30">
          <button
            onClick={() => setIsOpen(!isOpen)}
            className="w-full flex items-center justify-between px-3 py-2 text-xs font-bold text-primary-light hover:bg-primary/5 transition-colors"
          >
            <span className="flex items-center gap-1.5">
              <Split className="w-3.5 h-3.5" /> How this answer was assembled
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
                <div className="mt-1 pt-2 border-t border-white/5 text-[10px] text-text-secondary/70">
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
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between px-3 py-2 text-xs font-bold text-primary-light hover:bg-primary/5 transition-colors"
      >
        <span className="flex items-center gap-1.5"><Network className="w-3.5 h-3.5" /> Graph Trace: Crystal Dreamscape</span>
        {isOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
      </button>
      {isOpen && (
        <div className="p-3 border-t border-primary/10">
          <CrystalGraphTrace traceData={traceData} />
          <div className="mt-4 text-xs font-mono text-text-secondary overflow-x-auto whitespace-pre p-2 bg-black/20 rounded-md">
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
      content = (
        <>
          {offsets.map(([start, end], i) => (
            <span key={i} className="hover:bg-primary/30 transition-colors rounded px-0.5 cursor-text relative group">
              {src.text!.substring(start, end)}
              <span className="absolute -top-6 left-1/2 -translate-x-1/2 bg-black/80 text-[9px] text-white px-2 py-1 rounded opacity-0 group-hover:opacity-100 pointer-events-none whitespace-nowrap z-10 transition-opacity">
                Precision Match
              </span>
            </span>
          ))}
        </>
      );
    } catch (e) {
      console.error("Failed to parse sentence offsets", e);
    }
  }

  return (
    <div className="flex flex-col gap-1 w-full max-w-sm mt-1">
      <div className="flex items-center gap-1.5">
        <button 
          onClick={() => setIsOpen(!isOpen)}
          className={`flex items-center gap-1.5 px-2 py-1 transition-colors rounded-lg text-[10px] border w-fit ${
            src._challenge_source 
              ? 'bg-red-500/10 hover:bg-red-500/20 text-red-300 border-red-500/30' 
              : 'bg-white/5 hover:bg-white/10 text-text-secondary border-white/5'
          }`}
        >
          <FileText className={`w-3 h-3 ${src._challenge_source ? 'text-red-400' : 'text-primary-light'}`} />
          <span className="max-w-[150px] truncate">{src.file_path.split(/[\\/]/).pop()}</span>
          {isOpen ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
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
          <div className="p-2 text-xs text-text-secondary bg-black/20 border border-white/5 rounded-lg max-h-48 overflow-y-auto whitespace-pre-wrap leading-relaxed">
            {content}
          </div>
          {/* Until now the expanded panel was a dead end: you could read the
              matched passage but had no way to reach the document it came
              from. Hidden outside the desktop shell, where a browser tab
              cannot open a local file. */}
          {isTauri && (
            <button
              onClick={() => { void openFile(src.file_path); }}
              className="flex items-center gap-1.5 px-2 py-1 self-start bg-white/5 hover:bg-white/10 transition-colors rounded-lg text-[10px] text-text-secondary border border-white/5"
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

  return (
    <div className={`flex gap-4 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
      {msg.role === 'assistant' && (
        <div className="w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center shrink-0 border border-primary/30 shadow-lg">
          <Bot className="w-4 h-4 text-primary-light" />
        </div>
      )}
      <div className={`flex flex-col gap-2 max-w-[85%] ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
        <div className={`px-5 py-3 rounded-2xl text-sm leading-relaxed shadow-sm border ${msg.role === 'user'
          ? 'bg-primary text-white border-primary-light/20 rounded-tr-none'
          : 'glass-card !p-3 text-text-primary border-white/80 rounded-tl-none'
          }`}>
          {msg.isStreaming && !msg.content ? (
            <div className="flex gap-1 py-1">
              <span className="w-1.5 h-1.5 bg-primary-light rounded-full animate-bounce"></span>
              <span className="w-1.5 h-1.5 bg-primary-light rounded-full animate-bounce [animation-delay:0.2s]"></span>
              <span className="w-1.5 h-1.5 bg-primary-light rounded-full animate-bounce [animation-delay:0.4s]"></span>
            </div>
          ) : (
            <div className="prose prose-invert prose-sm max-w-none">
              <ReactMarkdown 
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeRaw]}
                components={{
                  claim: ({ ...props }: Readonly<Record<string, any>>) => {
                    const sourcesStr = String(props.sources || "");
                    const isInference = sourcesStr.toLowerCase().includes("inference");
                    const numSources = (sourcesStr.match(/\[\d+\]/g) || []).length;
                    
                    if (isInference) {
                      return <span className="bg-amber-500/10 text-amber-200/90 px-1 rounded border-b border-amber-500/30" title="Inference (Ungrounded)" {...props} />;
                    }
                    
                    if (numSources >= 3) {
                      return <span className="underline decoration-green-500/50 decoration-2 underline-offset-4 bg-green-500/10 px-1 rounded text-green-100" title={`High Confidence (Sources: ${sourcesStr})`} {...props} />;
                    }
                    
                    return <span className="underline decoration-primary-light/40 decoration-1 underline-offset-4 hover:bg-primary/5 px-1 rounded transition-colors cursor-help" title={`Sources: ${sourcesStr}`} {...props} />;
                  },
                  inference: ({ ...props }: Readonly<Record<string, any>>) => <span className="bg-amber-500/10 text-amber-200/90 px-1 rounded border-b border-amber-500/30" title="Inference (Ungrounded)" {...props} />
                } as Record<string, React.ComponentType<any>>}
              >
                {msg.content}
              </ReactMarkdown>
            </div>
          )}
        </div>

        {/* Mode Badge */}
        {msg.role === 'assistant' && !msg.isStreaming && msg.mode && (
          <div className="flex flex-wrap items-center gap-2 mt-1">
            <span className={`inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full border ${
              msg.mode === 'fast_path'
                ? 'bg-amber-500/10 text-amber-400 border-amber-500/20'
                : msg.mode === 'degraded_rag'
                  ? 'bg-orange-500/10 text-orange-400 border-orange-500/20'
                  : 'bg-primary/10 text-primary-light border-primary/20'
              }`}>
              {msg.mode === 'fast_path' ? '⚡ Fast Answer' : msg.mode === 'degraded_rag' ? '⚠️ Degraded RAG' : '🔍 RAG Answer'}
            </span>
            {msg.fallbackTo && (
              <span className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full border bg-red-500/10 text-red-400 border-red-500/20">
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
                • {msg.isEstimatedCost ? '~' : ''}${msg.cost.toFixed(5)}
              </span>
            )}
          </div>
        )}


        {/* Contradictions Banner */}
        {msg.role === 'assistant' && msg.contradictions_found && (
          <div className="mt-2 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2 flex items-start gap-2 text-amber-200/90 text-xs">
            <span className="text-amber-400 mt-0.5">⚠️</span>
            <div className="flex flex-col">
              <span className="font-bold text-amber-300">Potential Source Conflicts</span>
              <span>The system identified information in your files that might be conflicting.</span>
            </div>
          </div>
        )}

        {/* Bounded retrieval loop trace (agentic mode only) */}
        {msg.role === 'assistant' && msg.trace && msg.trace.length > 0 && (
          <ReasoningTrace trace={msg.trace} />
        )}

        {/* Knowledge Gaps Panel */}
        {msg.role === 'assistant' && msg.knowledge_gaps && msg.knowledge_gaps.length > 0 && (
          <div className="mt-2 bg-purple-500/10 border border-purple-500/20 rounded-lg overflow-hidden">
            <div className="px-3 py-2 flex items-center gap-2 text-purple-200/90 text-xs font-bold border-b border-purple-500/10">
              <Sparkles className="w-4 h-4" />
              What You Don't Know
            </div>
            <div className="p-3 text-xs text-purple-200/70 flex flex-wrap gap-2">
              {msg.knowledge_gaps.map((gap, i) => (
                <span key={i} className="bg-purple-500/20 px-2 py-1 rounded-md border border-purple-500/30">
                  {gap}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Pattern Annotator Panel */}
        {msg.role === 'assistant' && msg.pattern_annotations && msg.pattern_annotations.length > 0 && (
          <div className="mt-2 bg-blue-500/10 border border-blue-500/20 rounded-lg overflow-hidden">
            <button 
              onClick={() => setAnnotationsOpen(!annotationsOpen)}
              className="w-full px-3 py-2 flex items-center justify-between text-blue-200/90 text-xs font-bold border-b border-blue-500/10 hover:bg-blue-500/5 transition-colors"
            >
              <span className="flex items-center gap-2">
                <User className="w-4 h-4" />
                Personal Pattern Annotator
              </span>
              {annotationsOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
            </button>
            {annotationsOpen && (
              <div className="p-3 text-xs text-blue-200/70 flex flex-col gap-1.5">
                {msg.pattern_annotations.map((annotation, i) => (
                  <div key={i} className="flex items-start gap-1.5">
                    <span className="text-blue-400/80 mt-0.5">•</span>
                    <span>{annotation}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Answer Evolution Panel */}
        {msg.role === 'assistant' && msg.answer_evolution_diff && (
          <div className="mt-2 bg-emerald-500/10 border border-emerald-500/20 rounded-lg overflow-hidden">
            <div className="px-3 py-2 flex items-center gap-2 text-emerald-200/90 text-xs font-bold border-b border-emerald-500/10">
              <RotateCcw className="w-4 h-4" />
              Answer Evolution
            </div>
            <div className="p-3 text-xs text-emerald-200/70">
              {msg.answer_evolution_diff}
            </div>
          </div>
        )}

        {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
          <div className="flex flex-col gap-2 mt-2">
            {msg.sources.slice(0, 3).map((src) => (
              <SourceViewer key={`${src.file_path}-${src.score || 0}`} src={src} />
            ))}
            {msg.sources.length > 3 && (
              <span className="text-[10px] text-text-secondary self-start ml-2">+{msg.sources.length - 3} more</span>
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
                  <div className="text-[10px] text-text-secondary mt-1 flex items-center gap-1.5 border-t border-white/5 pt-2 pl-2">
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
          <div className="mt-4 pt-4 border-t border-white/5">
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
        <div className="w-8 h-8 rounded-full bg-surface-lighter flex items-center justify-center shrink-0 border border-white/10 shadow-lg">
          <User className="w-4 h-4 text-text-secondary" />
        </div>
      )}
    </div>
  );
}
