import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { SetupPage } from '../../pages/SetupPage';
import { renderWithProviders } from '../test-utils';

// Mutable so a test can vary what useApi reports without re-mocking the module.
const state = {
  driveInfo: {
    is_portable_fs: false,
    lancedb_mode: 'local',
    mount_path: '/mock/path',
    free_bytes: 100000000,
  } as Record<string, unknown> | undefined,
  loading: false,
};

// Mock useApi directly using cacheKey
vi.mock('../../useApi', () => ({
  useApi: vi.fn((_, opts) => {
    if (opts?.cacheKey === 'providers-list') {
      return { data: [], loading: state.loading, error: null, refetch: vi.fn() };
    }
    if (opts?.cacheKey === 'local-models') {
      return { data: { ollama: { detected: false, models: [] }, lm_studio: { detected: false, models: [] } }, loading: state.loading, error: null, refetch: vi.fn() };
    }
    if (opts?.cacheKey === 'drive-info') {
      return { data: state.driveInfo, loading: state.loading, error: null, refetch: vi.fn() };
    }

    return { data: undefined, loading: false, error: null, refetch: vi.fn() };
  }),
  invalidateCache: vi.fn(),
}));

// Mock api endpoints
vi.mock('../../api', () => ({
  getProviders: vi.fn(),
  getLocalModels: vi.fn(),
  getDriveInfo: vi.fn(),
  enableSplitBrain: vi.fn(),
  setProviderKey: vi.fn(),
}));

describe('SetupPage Component', () => {
  beforeEach(() => {
    state.driveInfo = {
      is_portable_fs: false,
      lancedb_mode: 'local',
      mount_path: '/mock/path',
      free_bytes: 100000000,
    };
    state.loading = false;
  });

  it('renders Welcome to PMA header and step 1 content', () => {
    renderWithProviders(<SetupPage />);

    expect(screen.getByText('Welcome to PMA')).toBeDefined();
    expect(screen.getByText("Your offline-first personal memory assistant. Let's get your intelligence engine connected.")).toBeDefined();
    expect(screen.getByText('Cloud Intelligence')).toBeDefined();
  });

  it('keeps the storage warning visible while a background refetch is in flight', () => {
    // useApi reports `isLoading || isFetching`, so `loading` goes true on every
    // background refetch - and this page registers focus/visibilitychange
    // listeners that refetch, so it went true whenever the user alt-tabbed back.
    // The banner used to be gated on `!isLoading`, which meant a storage
    // *incompatibility warning* silently vanished at exactly that moment.
    state.driveInfo = {
      is_portable_fs: true,
      lancedb_mode: 'local', // portable filesystem without split_brain == unsafe
      mount_path: '/mock/path',
      free_bytes: 100000000,
    };

    state.loading = false;
    const { unmount } = renderWithProviders(<SetupPage />);
    expect(screen.queryByText('Incompatible Storage Detected')).not.toBeNull();
    unmount();

    state.loading = true;
    renderWithProviders(<SetupPage />);
    expect(
      screen.queryByText('Incompatible Storage Detected'),
      'the warning disappeared during a background refetch',
    ).not.toBeNull();
  });

  it('does not flash the storage warning before drive info has arrived', () => {
    // The other half: absent data must not render a warning about data we do
    // not have yet. `isDriveConfigSafe` defaults to true while driveInfo is
    // undefined, and the banner also requires `driveInfo` to be present.
    state.driveInfo = undefined;
    state.loading = true;

    renderWithProviders(<SetupPage />);
    expect(screen.queryByText('Incompatible Storage Detected')).toBeNull();
  });
});
