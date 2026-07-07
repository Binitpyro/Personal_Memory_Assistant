import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { SetupPage } from '../../pages/SetupPage';
import { renderWithProviders } from '../test-utils';
import * as api from '../../api';

// Mock useApi directly using cacheKey
vi.mock('../../useApi', () => ({
  useApi: vi.fn((_, opts) => {
    if (opts?.cacheKey === 'auth-status') {
      return { data: { connected: false, email: '' }, loading: false, error: null, refetch: vi.fn() };
    }
    if (opts?.cacheKey === 'local-models') {
      return { data: { ollama: { detected: false, models: [] }, lm_studio: { detected: false, models: [] } }, loading: false, error: null, refetch: vi.fn() };
    }
    if (opts?.cacheKey === 'drive-info') {
      return { data: { is_portable_fs: false, lancedb_mode: 'local', mount_path: '/mock/path', free_bytes: 100000000 }, loading: false, error: null, refetch: vi.fn() };
    }
    if (opts?.cacheKey?.startsWith('api-key-')) {
      return { data: { is_set: false, preview: '' }, loading: false, error: null, refetch: vi.fn() };
    }
    return { data: undefined, loading: false, error: null, refetch: vi.fn() };
  }),
  invalidateCache: vi.fn(),
}));

// Mock api endpoints
vi.mock('../../api', () => ({
  getAuthStatus: vi.fn(),
  getLocalModels: vi.fn(),
  getDriveInfo: vi.fn(),
  enableSplitBrain: vi.fn(),
  launchGoogleAuth: vi.fn(),
  getApiKeyStatus: vi.fn(),
  setApiKey: vi.fn(),
}));

describe('SetupPage Component', () => {
  it('renders Welcome to PMA header and step 1 content', () => {
    renderWithProviders(<SetupPage />);

    expect(screen.getByText('Welcome to PMA')).toBeDefined();
    expect(screen.getByText("Your offline-first personal memory assistant. Let's get your intelligence engine connected.")).toBeDefined();
    expect(screen.getByText('Cloud Intelligence')).toBeDefined();
  });

  it('handles Google Auth trigger on click', () => {
    renderWithProviders(<SetupPage />);
    
    const connectBtn = screen.getByText('OAuth (Gemini)');
    expect(connectBtn).toBeDefined();
    
    fireEvent.click(connectBtn);
    expect(api.launchGoogleAuth).toHaveBeenCalled();
  });
});
