/**
 * Centralised API client for PMA backend.
 * All fetch calls go through here so we get consistent error handling,
 * base URL resolution, and easy-to-mock endpoints for tests.
 */

import { isTauri, initTauriConnection as _initTauri } from './utils/tauriShell';

const BASE = '/api'; 
const metaEnv = (import.meta as unknown as { env: Record<string, string | undefined> }).env;
export let ENDPOINT = metaEnv.VITE_API_URL || "http://127.0.0.1:8000";

// ─── Security Token Injection ─────────────────────────────────────────────

const params = new URLSearchParams(globalThis.location.search);
const tokenFromUrl = params.get('token');
const envToken = metaEnv.VITE_DEV_TOKEN || '';
// Injected into index.html by the backend for loopback clients. Without this a
// browser opening http://127.0.0.1:8000 had no token source at all - ?token= is
// absent, VITE_DEV_TOKEN is baked in at build time and unset in a release
// build, and sessionStorage is empty on a first visit - so every /api/ call
// returned 401 against a page that otherwise looked fine.
const injectedToken = (globalThis as { __PMA_TOKEN__?: string }).__PMA_TOKEN__ || '';
export let localToken =
  tokenFromUrl || injectedToken || envToken || sessionStorage.getItem('pma_token') || '';

if (tokenFromUrl) {
  sessionStorage.setItem('pma_token', tokenFromUrl);
  // Clean up URL so the token isn't sitting in the address bar
  globalThis.history.replaceState({}, document.title, globalThis.location.pathname);
}

export async function initTauriConnection() {
  await _initTauri((e) => ENDPOINT = e, (t) => localToken = t);
}

// â”€â”€ API Wrappers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

/** Basic fetch wrapper for JSON responses */
export async function json<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };
  if (localToken) headers['X-Local-Access-Token'] = localToken;

  const res = await fetch(`${ENDPOINT}/api${endpoint}`, { ...options, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(body.error || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// â”€â”€ Health â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export interface HealthResponse {
  version: string;
  status: 'ok' | 'degraded';
  db: string;
  model_ready: boolean;
  indexing: string;
  /** Boot-time sync status for Split-Brain mode: idle | syncing | done | error */
  split_brain_sync_status?: 'idle' | 'syncing' | 'done' | 'error';
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

// â”€â”€ Indexing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

// ── OCR ──────────────────────────────────────────────────────────────────

export interface OcrQueueCounts {
  pending: number;
  running: number;
  done: number;
  failed: number;
  skipped: number;
  pages_pending: number;
}

export interface OcrStatus {
  tier: string;
  enabled: boolean;
  installed: boolean;
  uv_available: boolean;
  queue: OcrQueueCounts;
  pages_pending: number;
  worker_running: boolean;
  current_file: string;
  unhealthy: boolean;
  fatal: string;
  last_error: string;
  cache_mb: number;
  cache_max_mb: number;
  /** Execution provider recorded in the install stamp, e.g. "CPUExecutionProvider". */
  ep?: string | null;
  /** Non-empty when the running engine disagrees with the install stamp. */
  engine_mismatch?: string;
}

export interface OcrInstallState {
  status: 'idle' | 'running' | 'ok' | 'failed' | 'cancelled';
  step: string;
  pct: number;
  message: string;
  error_code: string;
  log_tail: string[];
}

export interface OcrQueueItem {
  file_path: string;
  file_name: string;
  page_count: number;
  pages_done: number;
  pages_pending: number;
  status: string;
  attempts: number;
  last_error: string;
  updated_at: string;
}

export const getOcrStatus = () => json<OcrStatus>('/ocr/status');
export const getOcrInstallState = () => json<OcrInstallState>('/ocr/install/status');

export interface OcrTierInfo {
  id: string;
  /** Non-empty when this tier cannot run on this machine; shown instead of Install. */
  unavailable_reason: string;
  installed: boolean;
  /** False for the VLM tier: it is chosen, not provisioned, so "Install" is wrong. */
  needs_install?: boolean;
}

export const getOcrTiers = () =>
  json<{ installed: string; tiers: OcrTierInfo[] }>('/ocr/tiers');

export const installOcrTier = (tier = 'cpu') =>
  json<OcrInstallState>('/ocr/install', {
    method: 'POST',
    body: JSON.stringify({ tier }),
  });

export const cancelOcrInstall = () =>
  json<{ ok: boolean }>('/ocr/install/cancel', { method: 'POST' });

/** Clear a fatal stop. The only exit when a handshake failure left no failed rows. */
export const resumeOcr = () =>
  json<{ ok: boolean }>('/ocr/resume', { method: 'POST' });

export interface VlmModel { id: string; vision: boolean }

export interface VlmProviderInfo {
  provider: string;
  display_name: string;
  base_url: string;
  /** False when the endpoint is off this machine — page images would leave the device. */
  is_local: boolean;
  reachable: boolean;
  models: VlmModel[];
  error: string | null;
}

export const getVlmModels = () =>
  json<{
    providers: VlmProviderInfo[];
    has_vision_model: boolean;
    suggestions: string[];
  }>('/ocr/vlm/models');

export const getVlmSelection = () =>
  json<{ selection: { provider: string; model: string } | null }>('/ocr/vlm/selection');

export const selectVlmModel = (provider: string, model: string) =>
  json<{ ok: boolean; error_code?: string }>('/ocr/vlm/select', {
    method: 'POST',
    body: JSON.stringify({ provider, model }),
  });

export const uninstallOcrTier = () =>
  json<{ ok: boolean; removed: string[] }>('/ocr/uninstall', { method: 'POST' });

export const setOcrEnabled = (enabled: boolean) =>
  json<{ ok: boolean; enabled: boolean; error_code?: string }>('/ocr/enable', {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  });

export const getOcrQueue = (status?: string, limit = 50) =>
  json<{ items: OcrQueueItem[]; counts: OcrQueueCounts }>(
    `/ocr/queue?limit=${limit}${status ? `&status=${encodeURIComponent(status)}` : ''}`,
  );

export const retryOcr = (filePath: string) =>
  json<{ ok: boolean; error_code?: string }>('/ocr/retry', {
    method: 'POST',
    body: JSON.stringify({ file_path: filePath }),
  });

export const forceOcr = (filePath: string) =>
  json<{ ok: boolean; pages_queued?: number; error_code?: string }>('/ocr/force', {
    method: 'POST',
    body: JSON.stringify({ file_path: filePath }),
  });

export const clearOcrCache = () =>
  json<{ removed: number }>('/ocr/cache', { method: 'DELETE' });

export async function* streamGenerator(endpoint: string, payload: any, signal?: AbortSignal) {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (localToken) headers['X-Local-Access-Token'] = localToken;

  const response = await fetch(`${ENDPOINT}/api${endpoint}`, {
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

export const cancelIndexing = () =>
  json<{ message: string }>('/index/cancel', {
    method: 'POST',
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

// â”€â”€ System â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

export interface DriveInfo {
  drive: string;
  fs_type: string;
  is_portable_fs: boolean;
  lancedb_mode: string;
}

export const getDriveInfo = () => json<DriveInfo>('/system/drive_info');

export const enableSplitBrain = () =>
  json<{ message: string }>('/system/enable-split-brain', { method: 'POST' });

export const purgeHostCache = () =>
  json<{ message: string }>('/system/purge-host-cache', { method: 'POST' });

// â”€â”€ Folder picker â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

// P2-2: Use native Tauri dialog when running inside the desktop shell,
// fall back to legacy HTTP endpoint for browser-based dev mode.
export async function pickFolder(): Promise<{ path: string; error?: string }> {
  if (isTauri) {
    const { open } = await import('@tauri-apps/plugin-dialog');
    const selected = await open({ directory: true, multiple: false, title: 'Select a folder to index' });
    if (typeof selected === 'string') return { path: selected };
    if (Array.isArray(selected) && (selected as string[]).length > 0) return { path: selected[0] };
    return { path: '' };
  }
  // Browser dev mode: use backend tkinter fallback
  return json<{ path: string; error?: string }>('/pick/folder');
}

// â”€â”€ Query â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export interface QuerySource {
  file_path: string;
  folder_tag?: string;
  text?: string;
  score?: number;
  sentence_offsets?: string;
  segmenter_version?: string;
  modified_at?: string;
  _challenge_source?: boolean;
  chunk_id?: number;
}

export interface QueryResponse {
  answer: string;
  sources: QuerySource[];
  retrieved_count: number;
  latency_ms: number;
  mode?: string;
  timing?: Record<string, number>;
  graph_hops?: string;
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
  json<{ message: string; semantic_cache_cleared?: boolean; warning?: string }>(
    '/query/history/clear',
    { method: 'POST' },
  );

// â”€â”€ File tree â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

// â”€â”€ Insights â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    ? `${ENDPOINT}${BASE}/visualizer/stream?extension=${encodeURIComponent(filter)}`
    : `${ENDPOINT}${BASE}/visualizer/stream`;

  const headers: Record<string, string> = {};
  if (localToken) headers['X-Local-Access-Token'] = localToken;

  const res = await fetch(url, { headers });
  if (!res.ok) {
    throw new Error(`Failed to fetch visualizer stream: HTTP ${res.status}`);
  }
  return await res.arrayBuffer();
};

// ─── Visualizer Meta ────────────────────────────────────────────────────────

export interface VisualizerNodeMeta {
  name: string;
  path: string;
  size: number;
  usage_count: number;
  file_count?: number;
  is_folder: boolean;
}

export const getVisualizerMeta = (filter?: string | null) =>
  json<Record<string, VisualizerNodeMeta>>(
    filter ? `/visualizer/meta?extension=${encodeURIComponent(filter)}` : '/visualizer/meta'
  );

// â”€â”€ Clear Caches â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const clearBackendCaches = () =>
  json<{ message: string }>('/system/clear-cache', { method: 'POST' });

// â”€â”€ Insights by type â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export interface InsightsByTypeResponse {
  top_files: { path: string; size: number }[];
  cold_files: { path: string; size: number }[];
  error?: string;
}

export const getInsightsByType = (typeFilter: string) =>
  json<InsightsByTypeResponse>(`/insights/by-type?extension=${encodeURIComponent(typeFilter)}`);

export interface PortraitTheme {
  name: string;
  description: string;
  weight: number;
}

export interface PortraitResponse {
  themes: PortraitTheme[];
}

export const getPortrait = () => json<PortraitResponse>('/insights/portrait');

// â”€â”€ Demo â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const seedDemo = () =>
  json<{ message: string; folder: string }>('/demo/seed', { method: 'POST' });

// ── Stream Tracking ─────────────────────────────────────────────────────────

// ── Stream Tracking (Removed in favor of local hook state) ──────────────────

// ── SSE Progress Stream ─────────────────────────────────────────────────────

export function subscribeProgress(onData: (data: IndexStatus & { current_file: string }) => void): () => void {
  let es: EventSource | null = null;
  let closed = false;
  let retries = 0;
  const MAX_RETRIES = 10;
  const controller = new AbortController();

  function connect() {
    if (closed) return;
    // No ?token= here. The endpoint is auth-exempt in main.py and progress_stream
    // takes no token parameter, so the server never read it - but uvicorn's access
    // log prints the query string, so it leaked the token to the console on every
    // index run. EventSource cannot set headers; if this stream ever needs auth it
    // has to be a short-lived ticket or fetch-based SSE, not a bare token in a URL.
    es = new EventSource(`${ENDPOINT}${BASE}/index/progress-stream`);
    es.onopen = () => {
    };
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
  return () => { 
    closed = true; 
    controller.abort();
    es?.close(); 
  };
}

// â”€â”€ SSE Query Stream â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

/**
 * One step of the bounded agentic retrieval loop. `kind` comes from
 * app/search/agentic.py's TraceEvent; `detail` is already human-readable.
 * `subqueries` is populated on 'decompose' and 'not_found'.
 */
export interface TraceEvent {
  kind: 'start' | 'decompose' | 'retrieve' | 'not_found' | 'stop' | 'done';
  detail: string;
  subqueries?: string[];
  sources?: string[];
  count?: number;
  stop_reason?: string;
  iterations?: number;
}

export interface QueryStreamChunk {
  type: 'content' | 'sources' | 'fast_path' | 'error' | 'cached_full' | 'metadata' | 'done' | 'ping' | 'fallback' | 'usage' | 'trace';
  mode?: string;
  text?: string;
  answer?: string;
  sources?: QuerySource[];
  near_misses?: QuerySource[];
  data?: QueryResponse;
  latency_ms?: number;
  retrieval_ms?: number;
  graph_hops?: string;
  contradictions_found?: boolean;
  knowledge_gaps?: string[];
  pattern_annotations?: string[];
  trace?: TraceEvent[];
  to?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
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

  fetch(`${ENDPOINT}/api/query/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
    signal: controller.signal
  }).then(async (response) => {
    if (!response.ok) throw new Error('Stream request failed');
    const reader = response.body?.getReader();
    if (!reader) return;

    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        if (buffer.trim()) {
          try {
            onChunk(JSON.parse(buffer));
          } catch { /* ignore */ }
        }
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      
      // Handle Case 3: Two complete objects concatenated without \n between them (e.g. }{ )
      buffer = buffer.replace(/\}\{/g, '}\n{');
      
      const lines = buffer.split('\n');
      buffer = lines.pop() || ''; // Keep the last incomplete line in the buffer
      
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          onChunk(JSON.parse(line));
        } catch (err) { 
          // If JSON parse fails, it could be a deeply fragmented chunk that needs more data.
          // However, since we split by \n, this line should be complete.
          // In case of extreme edge cases, we could push it back to the buffer,
          // but for now we follow the existing pattern of ignoring genuinely malformed data.
        }
      }
    }
    onChunk({ type: 'done' });
  }).catch(err => {
    if (err.name !== 'AbortError') {
      onChunk({ type: 'error', text: err.message });
      // M-09: Network drop won't fire 'done' from the read loop.
      // Send 'done' explicitly so the UI spinner always clears.
      onChunk({ type: 'done' });
    }
  });

  return () => {
    controller.abort();
  };
}


// â”€â”€ Models â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export interface LocalModelDetection {
  ollama: { detected: boolean; models: string[] };
  lm_studio: { detected: boolean; models: string[] };
}

export const getLocalModels = () => json<LocalModelDetection>('/llm/detect');

export interface LLMPreferences {
  provider: 'auto' | 'gemini' | 'ollama' | 'lm_studio' | string;
  gemini_model?: string | null;
  ollama_model?: string | null;
  lm_studio_model?: string | null;
  [key: string]: any;
}

export const getLLMPreferences = () => json<LLMPreferences>('/llm/preferences');

export const setLLMPreferences = (prefs: LLMPreferences) =>
  json<{ message: string; llm: LLMPreferences }>('/llm/preferences', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(prefs)
  });

export interface ProviderSpec {
  id: string;
  display_name: string;
  kind: 'cloud' | 'local' | 'aggregator' | 'custom';
  default_base_url: string | null;
  base_url_editable: boolean;
  auth: 'bearer' | 'x-api-key' | 'x-goog-api-key' | 'none';
  models_endpoint: string;
  models_parser: string;
  api_key_pattern: string | null;
  api_key_docs_url: string;
  supports_streaming: boolean;
  supports_tools: boolean;
  supports_vision: boolean;
  supported_features: string[];
}

export interface ProviderStatus {
  spec: ProviderSpec;
  is_set: boolean;
  preview: string | null;
  stored_in: 'env' | 'keyring' | 'unset';
  base_url: string | null;
  default_model: string | null;
  last_validation?: ValidationResponse | null;
}


export interface ModelInfo {
  id: string;
  context_length: number;
  pricing_hint: number;
  family: string;
}

export interface ValidationResponse {
  ok: boolean;
  latency_ms: number;
  models: ModelInfo[];
  error: string | null;
  error_code: string | null;
  server_time: string | null;
  cached_offline?: boolean;
}

export const getProviders = () => json<ProviderStatus[]>('/providers');
export const validateProvider = (id: string, body: { api_key?: string | null; base_url?: string | null }) =>
  json<ValidationResponse>(`/providers/${id}/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
export const selfTestProvider = (id: string) => json<ValidationResponse>(`/providers/${id}/self_test`, { method: 'POST' });
export const setProviderKey = (id: string, api_key: string) =>
  json<{ status: string }>(`/providers/${id}/key`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key })
  });
export const deleteProviderKey = (id: string) => json<{ status: string }>(`/providers/${id}/key`, { method: 'DELETE' });
export const setProviderDefaultModel = (id: string, model: string) =>
  json<{ status: string }>(`/providers/${id}/default_model`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model })
  });
export const getCurrentProvider = () => json<{ provider: string; model: string }>('/providers/current');

/** Whether PMA can start this provider itself (Ollama / LM Studio only). */
export interface ProviderLaunchStatus {
  provider_id: string;
  supported: boolean;
  installed: boolean;
  running: boolean;
  /** Human label for how it would be started, e.g. "Ollama desktop app". */
  method: string | null;
  install_url: string | null;
}

export interface ProviderLaunchResult {
  ok: boolean;
  running: boolean;
  already_running: boolean;
  message: string;
  /** not_supported | unsupported_platform | not_installed | launch_failed | timeout | manual_step_required */
  error_code: string | null;
  elapsed_ms: number;
}

export const getProviderLaunchStatus = (id: string) =>
  json<ProviderLaunchStatus>(`/providers/${id}/launch_status`);

/** Starts the provider and resolves once its port answers (can take ~30s). */
export const launchProvider = (id: string) =>
  json<ProviderLaunchResult>(`/providers/${id}/launch`, { method: 'POST' });

export interface ProviderRoutingSettings {
  provider: string;
  fallback_chain: string[];
  cloud_privacy_consent?: boolean;
  cloud_privacy_notice?: string;
}

export const getProviderSettings = () => json<ProviderRoutingSettings>('/providers/settings');
export const setProviderSettings = (settings: Partial<ProviderRoutingSettings>) =>
  json<{ status: string }>('/providers/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings)
  });


