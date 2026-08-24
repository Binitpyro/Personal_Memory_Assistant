import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor, act } from '@testing-library/react';
import { SettingsPage } from '../../pages/SettingsPage';
import { renderWithProviders } from '../test-utils';
import { invalidateCache } from '../../useApi';
import { launchProvider } from '../../api';

const mockAuthStatus = { connected: true, email: 'test@example.com' };
const mockSystemInfo = { total_files: 5, total_chunks: 25, database_size_bytes: 2048 };
const mockLLMPrefs = { provider: 'auto', gemini_model: 'default-model', ollama_model: '', lm_studio_model: '' };
const mockDriveInfo = { is_portable_fs: false, lancedb_mode: 'local', mount_path: '/mock/path', free_bytes: 5000000 };
const mockProviders: any[] = [];
const mockRefetch = vi.fn();

const state = vi.hoisted(() => ({
  localModels: {
    ollama: { detected: false, models: [] as string[] },
    lm_studio: { detected: false, models: [] as string[] },
  },
  launchStatus: {} as Record<string, unknown>,
  ocrStatus: {
    installed: true,
    tier: 'cpu',
    enabled: true,
    ep: 'CPUExecutionProvider',
    uv_available: true,
    cache_mb: 10,
    queue: { done: 5, failed: 0 },
  } as Record<string, unknown>,
  ocrTiers: {
    installed: 'cpu',
    tiers: [
      { id: 'cpu', unavailable_reason: '', installed: true, active: true, needs_install: true },
      { id: 'gpu', unavailable_reason: '', installed: true, active: false, needs_install: true },
      { id: 'vlm', unavailable_reason: '', installed: false, active: false, needs_install: false },
    ],
  } as Record<string, unknown>,
  vlmModels: {
    providers: [
      { provider: 'ollama', display_name: 'Ollama', base_url: 'http://localhost:11434', is_local: true, reachable: false, models: [], error: null },
      { provider: 'lm_studio', display_name: 'LM Studio', base_url: 'http://localhost:1234', is_local: true, reachable: false, models: [], error: null },
    ],
    has_vision_model: false,
    suggestions: ['llama3.2-vision', 'minicpm-v'],
  } as Record<string, unknown>,
}));

const offlineInstalled = (id: string) => ({
  provider_id: id,
  supported: true,
  installed: true,
  running: false,
  method: 'Ollama desktop app',
  install_url: 'https://ollama.com/download',
});

// Mock useApi directly using cacheKey
vi.mock('../../useApi', () => ({
  useApi: vi.fn((_, opts) => {
    const wrap = (data: unknown) => ({ data, loading: false, error: null, refetch: mockRefetch });

    if (opts?.cacheKey === 'auth-status') return wrap(mockAuthStatus);
    if (opts?.cacheKey === 'local-models') return wrap(state.localModels);
    if (opts?.cacheKey === 'system-info') return wrap(mockSystemInfo);
    if (opts?.cacheKey === 'llm-preferences') return wrap(mockLLMPrefs);
    if (opts?.cacheKey === 'drive-info') return wrap(mockDriveInfo);
    if (opts?.cacheKey === 'providers-list') return wrap(mockProviders);
    if (opts?.cacheKey === 'ocr-status') return wrap(state.ocrStatus);
    if (opts?.cacheKey === 'ocr-tiers') return wrap(state.ocrTiers);
    if (opts?.cacheKey === 'ocr-vlm-models') return wrap(state.vlmModels);
    if (opts?.cacheKey === 'ocr-vlm-selection') return wrap({ selection: null });
    if (opts?.cacheKey?.startsWith('launch-status-')) {
      return wrap(state.launchStatus[opts.cacheKey.replace('launch-status-', '')]);
    }
    return wrap(undefined);
  }),
  invalidateCache: vi.fn(),
}));

// Mock api endpoints
vi.mock('../../api', () => {
  return {
    getAuthStatus: vi.fn(),
    disconnectAuth: vi.fn(),
    getLocalModels: vi.fn(),
    getSystemInfo: vi.fn(),
    getLLMPreferences: vi.fn(),
    setLLMPreferences: vi.fn(),
    setProviderDefaultModel: vi.fn(),
    clearIndex: vi.fn(),
    launchGoogleAuth: vi.fn(),
    getDriveInfo: vi.fn(),
    purgeHostCache: vi.fn(),
    getProviders: vi.fn(),
    getProviderLaunchStatus: vi.fn(),
    launchProvider: vi.fn(),
    // OcrSection's imports. Absent from the factory these threw at render, which
    // is what failed all seven SettingsPage cases rather than any assertion.
    getOcrStatus: vi.fn(),
    getOcrTiers: vi.fn(),
    selectOcrTier: vi.fn(),
    // Only reached on the VLM tier, so a missing entry here would not fail
    // until someone selected it - the stale-factory trap this file has hit
    // before.
    getVlmModels: vi.fn(),
    getVlmSelection: vi.fn(),
    selectVlmModel: vi.fn(),
    getOcrInstallState: vi.fn(),
    // OcrSection awaits this and reads .items - a bare vi.fn() resolves
    // undefined and sends every test down loadFailed()'s catch branch.
    getOcrQueue: vi.fn(() => Promise.resolve({ items: [] })),
    installOcrTier: vi.fn(),
    uninstallOcrTier: vi.fn(),
    cancelOcrInstall: vi.fn(),
    resumeOcr: vi.fn(),
    setOcrEnabled: vi.fn(),
    retryOcr: vi.fn(),
    clearOcrCache: vi.fn(),
  };
});

// OcrSection's loadFailed() effect calls getOcrQueue() and setStates in the
// resulting microtask. A synchronous test body never yields, so that update
// lands outside act(). Awaiting inside act() flushes it - React keeps the act
// queue installed across the await, so the continuation is covered.
async function renderSettings() {
  const result = renderWithProviders(<SettingsPage />);
  await act(async () => {});
  return result;
}

describe('SettingsPage Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.localModels = {
      ollama: { detected: false, models: [] },
      lm_studio: { detected: false, models: [] },
    };
    state.launchStatus = {
      ollama: offlineInstalled('ollama'),
      lm_studio: offlineInstalled('lm_studio'),
    };
  });

  it('renders SettingsPage with settings groups', async () => {
    await renderSettings();

    expect(screen.getByText('Settings')).toBeDefined();
    expect(screen.getByText('Model Selection')).toBeDefined();
  });

  it('offers to start an installed local provider that is offline', async () => {
    await renderSettings();

    expect(screen.getByRole('button', { name: /Start Ollama/i })).toBeDefined();
    expect(screen.getByRole('button', { name: /Start LM Studio/i })).toBeDefined();
  });

  it('links to the installer when the provider is not installed', async () => {
    state.launchStatus.ollama = { ...offlineInstalled('ollama'), installed: false, method: null };
    await renderSettings();

    expect(screen.queryByRole('button', { name: /Start Ollama/i })).toBeNull();
    const link = screen.getByRole('link', { name: /Install Ollama/i });
    expect(link.getAttribute('href')).toBe('https://ollama.com/download');
  });

  it('hides the start button once the provider is detected', async () => {
    state.localModels.ollama = { detected: true, models: ['llama3:8b'] };
    await renderSettings();

    expect(screen.queryByRole('button', { name: /Start Ollama/i })).toBeNull();
    expect(screen.getByText('llama3:8b')).toBeDefined();
  });

  it('falls back to the manual hint when launch status is unavailable', async () => {
    state.launchStatus = {};
    await renderSettings();

    expect(screen.getByText(/Ensure Ollama is running on localhost:11434/i)).toBeDefined();
  });

  it('starts the provider and refreshes detection on success', async () => {
    vi.mocked(launchProvider).mockResolvedValue({
      ok: true,
      running: true,
      already_running: false,
      message: 'Ollama is running.',
      error_code: null,
      elapsed_ms: 2100,
    });

    await renderSettings();
    fireEvent.click(screen.getByRole('button', { name: /Start Ollama/i }));

    await waitFor(() => expect(launchProvider).toHaveBeenCalledWith('ollama'));
    await waitFor(() => expect(invalidateCache).toHaveBeenCalledWith('local-models'));
    expect(mockRefetch).toHaveBeenCalled();
  });

  it('surfaces the backend message when the launch fails', async () => {
    vi.mocked(launchProvider).mockResolvedValue({
      ok: false,
      running: false,
      already_running: false,
      message: "Ollama doesn't appear to be installed.",
      error_code: 'not_installed',
      elapsed_ms: 5,
    });

    await renderSettings();
    fireEvent.click(screen.getByRole('button', { name: /Start Ollama/i }));

    expect(await screen.findByText(/doesn't appear to be installed/i)).toBeDefined();
  });

  it('switches between installed OCR tiers', async () => {
    const { selectOcrTier } = await import('../../api');
    vi.mocked(selectOcrTier).mockResolvedValue({ ok: true, tier: 'gpu' });

    await renderSettings();

    // Click GPU tier tab ("High accuracy")
    fireEvent.click(screen.getByRole('button', { name: /High accuracy/i }));

    // Click "Switch to High accuracy" button
    const switchBtn = screen.getByRole('button', { name: /Switch to High accuracy/i });
    expect(switchBtn).toBeDefined();
    fireEvent.click(switchBtn);

    await waitFor(() => expect(selectOcrTier).toHaveBeenCalledWith('gpu'));
  });

  it('renders VLM picker with check again and start buttons when offline', async () => {
    await renderSettings();

    // Click Vision model tab ("Your own AI model")
    fireEvent.click(screen.getByRole('button', { name: /Your own AI model/i }));

    expect(screen.getByText(/Neither Ollama nor LM Studio is currently reachable/i)).toBeDefined();
    expect(screen.getByRole('button', { name: /Check again/i })).toBeDefined();
  });
});

