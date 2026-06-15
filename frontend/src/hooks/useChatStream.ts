import { useReducer, useCallback, useRef, useEffect } from 'react';
import { subscribeQuery, type QueryStreamChunk, type QuerySource } from '../api';
import { useStreamActivity } from '../context/StreamActivityContext';

export interface Message {
  id: string; // generated using crypto.randomUUID()
  role: 'user' | 'assistant';
  content: string;
  sources?: QuerySource[];
  near_misses?: QuerySource[];
  latency_ms?: number;
  isStreaming?: boolean;
  mode?: 'fast_path' | 'full_rag' | 'degraded_rag';
  graph_hops?: string;
  contradictions_found?: boolean;
  knowledge_gaps?: string[];
  pattern_annotations?: string[];
  answer_evolution_diff?: string;
}

type ChatAction =
  | { type: 'ADD_USER_MESSAGE'; payload: { id: string; content: string } }
  | { type: 'START_ASSISTANT_STREAM'; payload: { id: string } }
  | { type: 'APPEND_STREAM'; payload: { text: string } }
  | { type: 'FINISH_STREAM'; payload: { graph_hops?: string } }
  | { type: 'SET_FAST_PATH'; payload: { content: string; sources?: QuerySource[]; latency_ms?: number; graph_hops?: string } }
  | { type: 'SET_SOURCES'; payload: { sources: QuerySource[]; near_misses: QuerySource[]; latency_ms: number; mode: 'fast_path' | 'full_rag' | 'degraded_rag'; graph_hops?: string; contradictions_found?: boolean; knowledge_gaps?: string[] } }
  | { type: 'SET_METADATA'; payload: { pattern_annotations?: string[]; answer_evolution_diff?: string } }
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
          ...(action.payload.graph_hops ? { graph_hops: action.payload.graph_hops } : {})
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
          pattern_annotations: action.payload.pattern_annotations || last.pattern_annotations,
          answer_evolution_diff: action.payload.answer_evolution_diff || last.answer_evolution_diff
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

export function useChatStream(onHistoryUpdate: () => void) {
  const [messages, dispatch] = useReducer(chatReducer, []);
  const { setIsStreamActive } = useStreamActivity();
  
  const streamBufferRef = useRef('');
  const lastUpdateRef = useRef(0);
  const throttleTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  const executeSearch = useCallback(async (
    question: string,
    options: {
      file_type?: string;
      folder_tag?: string;
      mode?: string;
      forced_chunk_id?: number;
      isRetry?: boolean;
    }
  ) => {
    const userMsg = question.trim();
    if (!userMsg) return;

    streamBufferRef.current = '';
    lastUpdateRef.current = 0;
    
    setIsStreamActive(true);

    const userMessageContent = options.isRetry ? `(Retrying) ${userMsg}` : userMsg;
    dispatch({ type: 'ADD_USER_MESSAGE', payload: { id: crypto.randomUUID(), content: userMessageContent } });
    dispatch({ type: 'START_ASSISTANT_STREAM', payload: { id: crypto.randomUUID() } });

    const historyForApi = messages.map(m => ({ role: m.role, content: m.content }));
    historyForApi.push({ role: 'user', content: userMessageContent });
    
    let currentSources: QuerySource[] = [];
    let currentLatency = 0;

    return new Promise<void>((resolve, reject) => {
      subscribeQuery({
        question: userMsg,
        history: historyForApi,
        file_type: options.file_type || null,
        folder_tag: options.folder_tag || null,
        mode: options.mode || null,
        forced_chunk_ids: options.forced_chunk_id ? [options.forced_chunk_id] : null
      }, (chunk: QueryStreamChunk) => {
        
        if (chunk.type === 'error') {
          dispatch({ type: 'SET_ERROR', payload: { text: chunk.text || 'Search failed' } });
          setIsStreamActive(false);
          reject(new Error(chunk.text || 'Search failed'));
          return;
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
          if (throttleTimeoutRef.current) {
            clearTimeout(throttleTimeoutRef.current);
            throttleTimeoutRef.current = null;
          }
          flushStreamBuffer();
          dispatch({ type: 'FINISH_STREAM', payload: { graph_hops: chunk.graph_hops } });
          setIsStreamActive(false);
          onHistoryUpdate();
          resolve();
        }

        if (chunk.type === 'metadata') {
          dispatch({
            type: 'SET_METADATA',
            payload: {
              pattern_annotations: chunk.pattern_annotations,
              answer_evolution_diff: chunk.answer_evolution_diff
            }
          });
        }

      });
    });
  }, [messages, flushStreamBuffer, setIsStreamActive, onHistoryUpdate]);

  return { messages, executeSearch, resetChat };
}
