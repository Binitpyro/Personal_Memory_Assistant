import { useReducer, useCallback, useRef, useEffect } from 'react';
import { subscribeQuery, type QueryStreamChunk, type QuerySource, type ProviderStatus, type TraceEvent } from '../api';
import { useSessionProvider } from '../context/SessionProviderContext';
import { queryClient } from '../queryClient';

export interface Message {
  id: string; // generated using crypto.randomUUID()
  role: 'user' | 'assistant';
  content: string;
  sources?: QuerySource[];
  near_misses?: QuerySource[];
  latency_ms?: number;
  isStreaming?: boolean;
  /** Set when the user stopped generation. The partial answer is kept. */
  stopped?: boolean;
  mode?: 'fast_path' | 'full_rag' | 'degraded_rag' | 'cached';
  graph_hops?: string;
  contradictions_found?: boolean;
  knowledge_gaps?: string[];
  pattern_annotations?: string[];
  trace?: TraceEvent[];
  fallbackTo?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  cost?: number;
  isEstimatedCost?: boolean;
}

type ChatAction =
  | { type: 'ADD_USER_MESSAGE'; payload: { id: string; content: string } }
  | { type: 'START_ASSISTANT_STREAM'; payload: { id: string } }
  | { type: 'APPEND_STREAM'; payload: { text: string } }
  | { type: 'FINISH_STREAM'; payload: { graph_hops?: string; stopped?: boolean } }
  | { type: 'SET_FAST_PATH'; payload: { content: string; sources?: QuerySource[]; latency_ms?: number; graph_hops?: string } }
  | { type: 'SET_SOURCES'; payload: { sources: QuerySource[]; near_misses: QuerySource[]; latency_ms: number; mode: 'fast_path' | 'full_rag' | 'degraded_rag' | 'cached'; graph_hops?: string; contradictions_found?: boolean; knowledge_gaps?: string[] } }
  | { type: 'SET_METADATA'; payload: { pattern_annotations?: string[] } }
  | { type: 'SET_TRACE'; payload: { trace: TraceEvent[] } }
  | { type: 'SET_FALLBACK'; payload: { to: string } }
  | { type: 'SET_USAGE'; payload: { prompt_tokens: number; completion_tokens: number; cost: number; isEstimatedCost: boolean } }
  | { type: 'SET_ERROR'; payload: { text: string } }
  | { type: 'RESET' };

function chatReducer(state: Message[], action: ChatAction): Message[] {
  switch (action.type) {
    case 'RESET':
      return [];
    case 'ADD_USER_MESSAGE':
      return [...state, { id: action.payload.id, role: 'user', content: action.payload.content }];
    case 'START_ASSISTANT_STREAM':
      return [...state, { id: action.payload.id, role: 'assistant', content: '', isStreaming: true }];
    case 'APPEND_STREAM': {
      const last = state.at(-1);
      if (last?.role === 'assistant') {
        return [...state.slice(0, -1), { ...last, content: action.payload.text }];
      }
      return state;
    }
    case 'FINISH_STREAM': {
      const last = state.at(-1);
      if (last?.role === 'assistant') {
        return [...state.slice(0, -1), {
          ...last,
          isStreaming: false,
          ...(action.payload.graph_hops ? { graph_hops: action.payload.graph_hops } : {}),
          ...(action.payload.stopped ? { stopped: true } : {})
        }];
      }
      return state;
    }
    case 'SET_FAST_PATH': {
      const last = state.at(-1);
      if (last?.role === 'assistant') {
        return [...state.slice(0, -1), { 
          ...last, 
          content: action.payload.content, 
          sources: action.payload.sources || last.sources,
          latency_ms: action.payload.latency_ms || last.latency_ms,
          mode: 'fast_path',
          graph_hops: action.payload.graph_hops || last.graph_hops
        }];
      }
      return state;
    }
    case 'SET_SOURCES': {
      const last = state.at(-1);
      if (last?.role === 'assistant') {
        return [...state.slice(0, -1), { 
          ...last, 
          sources: action.payload.sources,
          near_misses: action.payload.near_misses,
          latency_ms: action.payload.latency_ms,
          mode: action.payload.mode,
          graph_hops: action.payload.graph_hops,
          contradictions_found: action.payload.contradictions_found,
          knowledge_gaps: action.payload.knowledge_gaps
        }];
      }
      return state;
    }
    case 'SET_METADATA': {
      const last = state.at(-1);
      if (last?.role === 'assistant') {
        return [...state.slice(0, -1), { 
          ...last, 
          pattern_annotations: action.payload.pattern_annotations || last.pattern_annotations
        }];
      }
      return state;
    }
    case 'SET_TRACE': {
      const last = state.at(-1);
      if (last?.role === 'assistant') {
        return [...state.slice(0, -1), { ...last, trace: action.payload.trace }];
      }
      return state;
    }
    case 'SET_FALLBACK': {
      const last = state.at(-1);
      if (last?.role === 'assistant') {
        return [...state.slice(0, -1), {
          ...last,
          fallbackTo: action.payload.to
        }];
      }
      return state;
    }
    case 'SET_USAGE': {
      const last = state.at(-1);
      if (last?.role === 'assistant') {
        return [...state.slice(0, -1), { 
          ...last, 
          prompt_tokens: action.payload.prompt_tokens,
          completion_tokens: action.payload.completion_tokens,
          cost: action.payload.cost,
          isEstimatedCost: action.payload.isEstimatedCost
        }];
      }
      return state;
    }
    case 'SET_ERROR': {
        const last = state.at(-1);
        if (last?.role === 'assistant') {
            return [...state.slice(0, -1), { ...last, isStreaming: false }];
        }
        return state;
    }
    default:
      return state;
  }
}


const STATIC_PRICING_HINTS: Record<string, number> = {
  'gemini-2.5-flash-lite': 0.075,
  'gemini-2.5-flash': 0.075,
  'gemini-2.5-pro': 1.25,
  'gpt-4o-mini': 0.15,
  'gpt-4o': 2.50,
  'claude-3-5-sonnet-20241022': 3.00,
  'claude-3-5-haiku-20241022': 0.80,
};

function calculateCost(
  providerId: string,
  modelId: string,
  promptTokens: number,
  completionTokens: number
): number {
  const providers = queryClient.getQueryData<ProviderStatus[]>(['providers-list']);
  const p = providers?.find((prov) => prov.spec.id === providerId);
  const m = p?.last_validation?.models?.find((mdl: { id: string }) => mdl.id === modelId);

  
  const pricingHint = m?.pricing_hint ?? STATIC_PRICING_HINTS[modelId] ?? 0;
  const totalTokens = promptTokens + completionTokens;
  return (totalTokens / 1_000_000) * pricingHint;
}

export function useChatStream(onHistoryUpdate: () => void) {
  const [messages, dispatch] = useReducer(chatReducer, []);
  const { sessionModelOverride, addSessionCost } = useSessionProvider();
  
  const streamBufferRef = useRef('');
  const lastUpdateRef = useRef(0);
  const throttleTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // subscribeQuery's teardown, and the terminal transition for the stream it
  // belongs to. Aborting the fetch emits no further chunks - api.ts swallows
  // AbortError deliberately, so 'done' never arrives - which means stopping has
  // to complete the stream on the client side or the UI stays disabled forever.
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const finalizeStreamRef = useRef<((opts?: { stopped?: boolean }) => void) | null>(null);

  useEffect(() => {
    return () => {
      if (throttleTimeoutRef.current) clearTimeout(throttleTimeoutRef.current);
    };
  }, []);

  const flushStreamBuffer = useCallback(() => {
    const text = streamBufferRef.current;
    if (!text) return;
    dispatch({ type: 'APPEND_STREAM', payload: { text } });
    lastUpdateRef.current = Date.now();
  }, []);

  const resetChat = useCallback(() => {
    dispatch({ type: 'RESET' });
  }, []);

  const stopStream = useCallback(() => {
    const unsubscribe = unsubscribeRef.current;
    if (!unsubscribe) return;
    unsubscribeRef.current = null;
    unsubscribe();
    finalizeStreamRef.current?.({ stopped: true });
  }, []);

  const executeSearch = useCallback(async (
    question: string,
    options: {
      file_type?: string;
      folder_tag?: string;
      mode?: string;
      forced_chunk_id?: number;
      selected_chunk_ids?: number[];
      isRetry?: boolean;
    }
  ) => {
    const userMsg = question.trim();
    if (!userMsg) return;

    streamBufferRef.current = '';
    lastUpdateRef.current = 0;

    const userMessageContent = options.isRetry ? `(Retrying) ${userMsg}` : userMsg;
    dispatch({ type: 'ADD_USER_MESSAGE', payload: { id: crypto.randomUUID(), content: userMessageContent } });
    dispatch({ type: 'START_ASSISTANT_STREAM', payload: { id: crypto.randomUUID() } });

    const historyForApi = messages.map(m => ({ role: m.role, content: m.content }));
    historyForApi.push({ role: 'user', content: userMessageContent });
    
    let currentSources: QuerySource[] = [];
    let currentLatency = 0;

    // Resolve initial active provider and model
    const providers = queryClient.getQueryData<ProviderStatus[]>(['providers-list']);
    const primaryProvider = sessionModelOverride?.provider || providers?.find(p => p.is_set)?.spec.id || 'gemini';
    const primaryModel = sessionModelOverride?.model || providers?.find(p => p.is_set)?.default_model || 'default-model';

    let currentProviderId = primaryProvider;
    let currentModelId = primaryModel;
    let receivedUsage = false;

    return new Promise<void>((resolve, reject) => {
      let settled = false;

      /** The stream's single terminal transition, shared by 'done' and stop. */
      const finalize = (opts: { graph_hops?: string; stopped?: boolean } = {}) => {
        if (settled) return;
        settled = true;
        unsubscribeRef.current = null;
        finalizeStreamRef.current = null;

        if (throttleTimeoutRef.current) {
          clearTimeout(throttleTimeoutRef.current);
          throttleTimeoutRef.current = null;
        }
        flushStreamBuffer();
        dispatch({
          type: 'FINISH_STREAM',
          payload: { graph_hops: opts.graph_hops, stopped: opts.stopped }
        });

        // A stopped or dropped stream never delivers the usage packet.
        if (!receivedUsage) {
          const text = streamBufferRef.current;
          const cTokens = Math.max(Math.ceil(text.length / 4), Math.ceil(text.trim().split(/\s+/).length * 1.3));

          const promptText = userMessageContent + "\n" + JSON.stringify(historyForApi);
          const pTokens = Math.max(Math.ceil(promptText.length / 4), Math.ceil(promptText.trim().split(/\s+/).length * 1.3));

          const cost = calculateCost(currentProviderId, currentModelId, pTokens, cTokens);
          addSessionCost(cost);
          dispatch({
            type: 'SET_USAGE',
            payload: {
              prompt_tokens: pTokens,
              completion_tokens: cTokens,
              cost,
              isEstimatedCost: true
            }
          });
        }

        onHistoryUpdate();
        resolve();
      };

      finalizeStreamRef.current = finalize;

      unsubscribeRef.current = subscribeQuery({
        question: userMsg,
        history: historyForApi,
        file_type: options.file_type || null,
        folder_tag: options.folder_tag || null,
        mode: options.mode || null,
        forced_chunk_ids: options.selected_chunk_ids && options.selected_chunk_ids.length > 0 ? options.selected_chunk_ids : (options.forced_chunk_id ? [options.forced_chunk_id] : null),
        override_provider: sessionModelOverride?.provider || null,
        override_model: sessionModelOverride?.model || null
      }, (chunk: QueryStreamChunk) => {
        
        if (chunk.type === 'error') {
          settled = true;
          unsubscribeRef.current = null;
          finalizeStreamRef.current = null;
          dispatch({ type: 'SET_ERROR', payload: { text: chunk.text || 'Search failed' } });
          reject(new Error(chunk.text || 'Search failed'));
          return;
        }

        if (chunk.type === 'fallback') {
          currentProviderId = chunk.to || 'openai';
          const fallbackProviderStatus = providers?.find(p => p.spec.id === currentProviderId);
          currentModelId = fallbackProviderStatus?.default_model || 'default-model';
          dispatch({ type: 'SET_FALLBACK', payload: { to: currentProviderId } });
        }

        if (chunk.type === 'usage') {
          receivedUsage = true;
          const pTokens = chunk.prompt_tokens || 0;
          const cTokens = chunk.completion_tokens || 0;
          const cost = calculateCost(currentProviderId, currentModelId, pTokens, cTokens);
          
          addSessionCost(cost);
          dispatch({
            type: 'SET_USAGE',
            payload: {
              prompt_tokens: pTokens,
              completion_tokens: cTokens,
              cost,
              isEstimatedCost: false
            }
          });
        }

        if (chunk.type === 'sources') {
          currentSources = chunk.sources || [];
          currentLatency = chunk.latency_ms || chunk.retrieval_ms || 0;
          dispatch({ 
            type: 'SET_SOURCES', 
            payload: { 
              sources: currentSources,
              near_misses: chunk.near_misses || [],
              latency_ms: currentLatency,
              mode: (chunk.mode as any) || 'full_rag',
              graph_hops: chunk.graph_hops,
              contradictions_found: chunk.contradictions_found,
              knowledge_gaps: chunk.knowledge_gaps
            }
          });
        }

        if (chunk.type === 'trace' && chunk.trace) {
          dispatch({ type: 'SET_TRACE', payload: { trace: chunk.trace } });
        }

        if (chunk.type === 'fast_path') {
          dispatch({
            type: 'SET_FAST_PATH',
            payload: {
              content: chunk.answer || chunk.text || '',
              sources: chunk.sources || currentSources,
              latency_ms: chunk.latency_ms || currentLatency,
              graph_hops: chunk.graph_hops
            }
          });
        }

        if (chunk.type === 'ping') return;

        if (chunk.type === 'content' && chunk.text) {
          streamBufferRef.current += chunk.text;
          const now = Date.now();
          if (now - lastUpdateRef.current > 50) {
            flushStreamBuffer();
          } else if (!throttleTimeoutRef.current) {
            throttleTimeoutRef.current = setTimeout(() => {
              throttleTimeoutRef.current = null;
              flushStreamBuffer();
            }, Math.max(0, 50 - (now - lastUpdateRef.current)));
          }
        }

        if (chunk.type === 'done') {
          finalize({ graph_hops: chunk.graph_hops });
        }

        if (chunk.type === 'metadata') {
          dispatch({
            type: 'SET_METADATA',
            payload: {
              pattern_annotations: chunk.pattern_annotations
            }
          });
        }

      });
    });
  }, [messages, flushStreamBuffer, onHistoryUpdate, sessionModelOverride, addSessionCost]);

  return { messages, executeSearch, resetChat, stopStream };
}

