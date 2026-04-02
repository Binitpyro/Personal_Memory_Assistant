/**
 * Centralised API client for PMA backend.
 * All fetch calls go through here so we get consistent error handling,
 * base URL resolution, and easy-to-mock endpoints for tests.
 */

const BASE = '/api'; export const ENDPOINT = (import.meta as any).env.VITE_API_URL || "http://127.0.0.1:8000"

// ── Security Token Injection ──────────────────────────────────────────

const params = new URLSearchParams(window.location.search);
const tokenFromUrl = params.get('token');
export const localToken = tokenFromUrl || sessionStorage.getItem('pma_token') || '';

if (tokenFromUrl) {
  sessionStorage.setItem('pma_token', tokenFromUrl);
  // Clean up URL so the token isn't sitting in the address bar
  window.history.replaceState({}, document.title, window.location.pathname);
}

// ── API Wrappers ──────────────────────────────────────────────────────

/** Basic fetch wrapper for JSON responses */
export async function json<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as any),
  };
  if (localToken) headers['X-Local-Access-Token'] = localToken;

  const res = await fetch(`/api${endpoint}`, { ...options, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(body.error || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// ── Health ────────────────────────────────────────────────────────────

export interface HealthResponse {
  version: string;
  status: 'ok' | 'degraded';
  db: string;
  model_ready: boolean;
  indexing: string;
}

export const getHealth = () => json<HealthResponse>('/health');

export interface AppConfig {
  app_version: string
  embedding_model: string
  gemini_model: string
  gemini_max_output_tokens: number
  dev_mode: boolean
  prompt_version: string
}

export const getAppConfig = () => json<AppConfig>('/system/config')

// ── Indexing ──────────────────────────────────────────────────────────

export interface IndexStatus {
  status: string;
  files_indexed: number;
  chunks_indexed: number;
  progress_percent: number;
  scan_method: string;
  scan_duration_ms: number;
  skipped_files: number;
  new_files: number;
  changed_files: number;
  total_files: number;
  processed_files: number;
}

export const getIndexStatus = () => json<IndexStatus>('/index/status');

export async function* streamGenerator(endpoint: string, payload: any, signal?: AbortSignal) {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (localToken) headers['X-Local-Access-Token'] = localToken;

  const response = await fetch(`/api${endpoint}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
    signal,
  });

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    yield decoder.decode(value);
  }
}

export const startIndexing = (folders: string[]) =>
  json<{ message: string }>('/index/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folders }),
  });

export const removeFolderIndex = (folders: string[]) =>
  json<{ message: string }>('/index/folder/remove', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folders }),
  });

export const clearIndex = () =>
  json<{ files_removed: number; chunks_removed: number }>('/index/clear', {
    method: 'POST',
  });

export interface CompactStatus {
  is_running: boolean;
  last_run: string | null;
  error: string | null;
}

export const compactDatabase = () =>
  json<{ message: string }>('/system/compact-db', {
    method: 'POST',
  });

export const getCompactStatus = () => json<CompactStatus>('/system/compact-db/status');

// ── System ────────────────────────────────────────────────────────────

export interface Volume {
  letter: string;
  total_gb: number;
  free_gb: number;
  used_gb: number;
}

export interface SystemInfo {
  os: string;
  is_admin: boolean;
  scan_method: string;
  volumes: Volume[];
}

export const getSystemInfo = () => json<SystemInfo>('/system/info');

// ── Folder picker ─────────────────────────────────────────────────────

export const pickFolder = () => json<{ path: string }>('/pick/folder');

// ── Query ─────────────────────────────────────────────────────────────

export interface QuerySource {
  file_path: string;
  folder_tag?: string;
  text?: string;
  score?: number;
}

export interface QueryResponse {
  answer: string;
  sources: QuerySource[];
  retrieved_count: number;
  latency_ms: number;
  mode?: string;
  timing?: Record<string, number>;
}

export const postQuery = (question: string, options: { file_type?: string, folder_tag?: string, history?: { role: string, content: string }[] } = {}) =>
  json<QueryResponse>('/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      question,
      file_type: options.file_type || null,
      folder_tag: options.folder_tag || null,
      history: options.history || null
    }),
  });

export interface HistoryItem {
  question: string;
  answer: string;
  source_count?: number;
  latency_ms?: number;
  created_at?: string;
}

export const getQueryHistory = (limit = 20) =>
  json<{ history: HistoryItem[] }>(`/query/history?limit=${limit}`);

export const clearQueryHistory = () =>
  json<{ message: string }>('/query/history/clear', { method: 'POST' });

// ── File tree ─────────────────────────────────────────────────────────

export interface FileEntry {
  path: string;
  size: number;
  type: string;
  usage_count: number;
}

export interface FileTree {
  folders: Record<string, FileEntry[]>;
  total_files: number;
  total_size: number;
}

export const getFileTree = () => json<FileTree>('/files/tree');

// ── Insights ──────────────────────────────────────────────────────────

export interface InsightsResponse {
  total_size_bytes: number;
  database_size_bytes: number;
  file_count: number;
  top_files: { path: string; size: number }[];
  cold_files: { path: string; usage_count: number }[];
  type_breakdown: Record<string, { count: number; size: number }>;
  error: string | null;
}

export const getInsights = () => json<InsightsResponse>('/insights');

export const getVisualizerStream = async (filter?: string | null): Promise<ArrayBuffer> => {
  const url = filter
    ? `${BASE}/visualizer/stream?extension=${encodeURIComponent(filter)}`
    : `${BASE}/visualizer/stream`;

  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Failed to fetch visualizer stream: HTTP ${res.status}`);
  }
  return await res.arrayBuffer();
};
// ── Clear Caches ──────────────────────────────────────────────────────

export const clearBackendCaches = () =>
  json<{ message: string }>('/system/clear-cache', { method: 'POST' });

// ── Insights by type ──────────────────────────────────────────────────

export interface InsightsByTypeResponse {
  top_files: { path: string; size: number }[];
  cold_files: { path: string; size: number }[];
  error?: string;
}

export const getInsightsByType = (typeFilter: string) =>
  json<InsightsByTypeResponse>(`/insights/by-type?extension=${encodeURIComponent(typeFilter)}`);

// ── Demo ──────────────────────────────────────────────────────────────

export const seedDemo = () =>
  json<{ message: string; folder: string }>('/demo/seed', { method: 'POST' });

// ── SSE Progress Stream ───────────────────────────────────────────────

export function subscribeProgress(onData: (data: IndexStatus & { current_file: string }) => void): () => void {
  let es: EventSource | null = null;
  let closed = false;
  let retries = 0;
  const MAX_RETRIES = 10;

  function connect() {
    if (closed) return;
    es = new EventSource(`${BASE}/index/progress-stream`);
    es.addEventListener('progress', (e) => {
      retries = 0; // reset on success
      try {
        onData(JSON.parse(e.data));
      } catch { /* ignore malformed */ }
    });
    es.onerror = () => {
      es?.close();
      if (!closed && retries < MAX_RETRIES) {
        retries++;
        setTimeout(connect, 1000);
      }
    };
  }

  // Small delay to allow backend SSE to be ready
  setTimeout(connect, 300);
  return () => { closed = true; es?.close(); };
}

// ── SSE Query Stream ──────────────────────────────────────────────────

export interface QueryStreamChunk {
  type: 'content' | 'sources' | 'fast_path' | 'error' | 'cached_full' | 'metadata' | 'done';
  text?: string;
  answer?: string;
  sources?: QuerySource[];
  data?: QueryResponse;
  latency_ms?: number;
  retrieval_ms?: number;
}

export function subscribeQuery(
  payload: any,
  onChunk: (chunk: QueryStreamChunk) => void
): () => void {
  const controller = new AbortController()

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  if (localToken) headers['X-Local-Access-Token'] = localToken

  fetch('/api/query/stream', {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
    signal: controller.signal
  }).then(async (response) => {
    if (!response.ok) throw new Error('Stream request failed');
    const reader = response.body?.getReader();
    if (!reader) return;

    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = decoder.decode(value);
      const lines = chunk.split('\n');
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          onChunk(JSON.parse(line));
        } catch { /* ignore malformed */ }
      }
    }
    onChunk({ type: 'done' });
  }).catch(err => {
    if (err.name !== 'AbortError') {
      onChunk({ type: 'error', text: err.message });
    }
  });

  return () => controller.abort();
}

// ── Auth ──────────────────────────────────────────────────────────────

export interface AuthStatus {
  connected: boolean;
}

export const getAuthStatus = () => json<AuthStatus>('/auth/google/status');
export const disconnectAuth = () => json<{ message: string }>('/auth/google/disconnect', { method: 'POST' });

// ── Models ────────────────────────────────────────────────────────────

export interface LocalModelDetection {
  ollama: { detected: boolean; models: string[] };
  lm_studio: { detected: boolean; models: string[] };
}

export const getLocalModels = () => json<LocalModelDetection>('/llm/detect');

export interface LLMPreferences {
  provider: 'auto' | 'gemini' | 'ollama' | 'lm_studio';
  gemini_model?: string | null;
  ollama_model?: string | null;
  lm_studio_model?: string | null;
}

export const getLLMPreferences = () => json<LLMPreferences>('/llm/preferences');

export const setLLMPreferences = (prefs: LLMPreferences) =>
  json<{ message: string; llm: LLMPreferences }>('/llm/preferences', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(prefs)
  });
