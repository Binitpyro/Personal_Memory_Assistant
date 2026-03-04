/**
 * Centralised API client for PMA backend.
 * All fetch calls go through here so we get consistent error handling,
 * base URL resolution, and easy-to-mock endpoints for tests.
 */

const BASE = '/api'; // Prefix all calls with /api to avoid collision with React Router paths

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, init);
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

export const postQuery = (question: string, options: { file_type?: string, folder_tag?: string, history?: {role: string, content: string}[] } = {}) =>
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
  file_count: number;
  database_size_bytes: number;
  top_files: { path: string; size: number }[];
  cold_files: { path: string; usage_count: number }[];
  type_breakdown: Record<string, { count: number; size: number }>;
  error: string | null;
}

export const getInsights = () => json<InsightsResponse>('/insights');

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
  const es = new EventSource(`${BASE}/index/progress-stream`);
  es.addEventListener('progress', (e) => {
    try {
      onData(JSON.parse(e.data));
    } catch { /* ignore malformed */ }
  });
  es.onerror = () => {
    es.close();
  };
  return () => es.close();
}

// ── SSE Query Stream ──────────────────────────────────────────────────

export interface QueryStreamChunk {
  type: 'content' | 'sources' | 'fast_path' | 'error' | 'cached_full';
  text?: string;
  answer?: string;
  sources?: QuerySource[];
  data?: QueryResponse;
  latency_ms?: number;
  retrieval_ms?: number;
}

export function subscribeQuery(
  question: string,
  onChunk: (chunk: QueryStreamChunk) => void,
  options: { file_type?: string; folder_tag?: string, history?: {role: string, content: string}[] } = {}
): () => void {
  const controller = new AbortController();
  
  fetch(`${BASE}/query/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      question,
      file_type: options.file_type || null,
      folder_tag: options.folder_tag || null,
      history: options.history || null,
    }),
    signal: controller.signal,
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
  }).catch(err => {
    if (err.name !== 'AbortError') {
      onChunk({ type: 'error', text: err.message });
    }
  });

  return () => controller.abort();
}
