import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
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

// Mutable per-test state. vi.hoisted keeps it initialised before the mock factories run.
const state = vi.hoisted(() => ({
  localModels: {
    ollama: { detected: false, models: [] as string[] },
    lm_studio: { detected: false, models: [] as string[] },
  },
  launchStatus: {} as Record<string, unknown>,
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
    if (opts?.cacheKey === 'llm-prefs') return wrap(mockLLMPrefs);
    if (opts?.cacheKey === 'drive-info') return wrap(mockDriveInfo);
    if (opts?.cacheKey === 'providers-list') return wrap(mockProviders);
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
  };
});

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

  it('renders SettingsPage with settings groups', () => {
    renderWithProviders(<SettingsPage />);

    expect(screen.getByText('Settings')).toBeDefined();
    expect(screen.getByText('Model Selection')).toBeDefined();
  });

  it('offers to start an installed local provider that is offline', () => {
    renderWithProviders(<SettingsPage />);

    expect(screen.getByRole('button', { name: /Start Ollama/i })).toBeDefined();
    expect(screen.getByRole('button', { name: /Start LM Studio/i })).toBeDefined();
  });

  it('links to the installer when the provider is not installed', () => {
    state.launchStatus.ollama = { ...offlineInstalled('ollama'), installed: false, method: null };
    renderWithProviders(<SettingsPage />);

    expect(screen.queryByRole('button', { name: /Start Ollama/i })).toBeNull();
    const link = screen.getByRole('link', { name: /Install Ollama/i });
    expect(link.getAttribute('href')).toBe('https://ollama.com/download');
  });

  it('hides the start button once the provider is detected', () => {
    state.localModels.ollama = { detected: true, models: ['llama3:8b'] };
    renderWithProviders(<SettingsPage />);

    expect(screen.queryByRole('button', { name: /Start Ollama/i })).toBeNull();
    expect(screen.getByText('llama3:8b')).toBeDefined();
  });

  it('falls back to the manual hint when launch status is unavailable', () => {
    state.launchStatus = {};
    renderWithProviders(<SettingsPage />);

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

    renderWithProviders(<SettingsPage />);
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

    renderWithProviders(<SettingsPage />);
    fireEvent.click(screen.getByRole('button', { name: /Start Ollama/i }));

    expect(await screen.findByText(/doesn't appear to be installed/i)).toBeDefined();
  });
});
