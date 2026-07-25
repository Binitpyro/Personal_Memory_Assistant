import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { SettingsPage } from '../../pages/SettingsPage';
import { renderWithProviders } from '../test-utils';

// Mock useApi directly using cacheKey
vi.mock('../../useApi', () => ({
  useApi: vi.fn((_, opts) => {
    if (opts?.cacheKey === 'auth-status') {
      return { data: { connected: true, email: 'test@example.com' }, loading: false, error: null, refetch: vi.fn() };
    }
    if (opts?.cacheKey === 'local-models') {
      return { data: { ollama: { detected: false, models: [] }, lm_studio: { detected: false, models: [] } }, loading: false, error: null, refetch: vi.fn() };
    }
    if (opts?.cacheKey === 'system-info') {
      return { data: { total_files: 5, total_chunks: 25, database_size_bytes: 2048 }, loading: false, error: null, refetch: vi.fn() };
    }
    if (opts?.cacheKey === 'llm-prefs') {
      return { data: { provider: 'auto', gemini_model: 'default-model', ollama_model: '', lm_studio_model: '' }, loading: false, error: null, refetch: vi.fn() };
    }
    if (opts?.cacheKey === 'drive-info') {
      return { data: { is_portable_fs: false, lancedb_mode: 'local', mount_path: '/mock/path', free_bytes: 5000000 }, loading: false, error: null, refetch: vi.fn() };
    }
    if (opts?.cacheKey === 'providers-list') {
      return { data: [], loading: false, error: null, refetch: vi.fn() };
    }
    return { data: undefined, loading: false, error: null, refetch: vi.fn() };
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
    clearIndex: vi.fn(),
    launchGoogleAuth: vi.fn(),
    getDriveInfo: vi.fn(),
    purgeHostCache: vi.fn(),
    getProviders: vi.fn(),
  };
});

describe('SettingsPage Component', () => {
  it('renders SettingsPage with settings groups', () => {
    renderWithProviders(<SettingsPage />);

    expect(screen.getByText('Settings')).toBeDefined();
    expect(screen.getByText('Model Selection')).toBeDefined();
  });
});
