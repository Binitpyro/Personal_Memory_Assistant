import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
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
  routingSettings: undefined as Record<string, unknown> | undefined,
  providers: [] as Record<string, unknown>[],
};

/** A gemini card with a key already stored - the state that creates the obligation. */
const geminiConnected = {
  spec: { id: 'gemini', display_name: 'Google Gemini', kind: 'cloud' },
  is_set: true,
  stored_in: 'keyring',
  preview: 'AIza...',
  base_url: null,
  default_model: null,
};

// Mock useApi directly using cacheKey
vi.mock('../../useApi', () => ({
  useApi: vi.fn((_, opts) => {
    if (opts?.cacheKey === 'providers-list') {
      return { data: state.providers, loading: state.loading, error: null, refetch: vi.fn() };
    }
    if (opts?.cacheKey === 'local-models') {
      return { data: { ollama: { detected: false, models: [] }, lm_studio: { detected: false, models: [] } }, loading: state.loading, error: null, refetch: vi.fn() };
    }
    if (opts?.cacheKey === 'drive-info') {
      return { data: state.driveInfo, loading: state.loading, error: null, refetch: vi.fn() };
    }
    if (opts?.cacheKey === 'provider-settings') {
      return { data: state.routingSettings, loading: state.loading, error: null, refetch: vi.fn() };
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
  getProviderSettings: vi.fn(),
  setProviderSettings: vi.fn(),
  seedDemo: vi.fn(),
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
    state.routingSettings = undefined;
    state.providers = [];
  });

  it('renders Welcome to PMA header and step 1 content', () => {
    renderWithProviders(<SetupPage />);

    expect(screen.getByText('Welcome to PMA')).toBeDefined();
    // Copy changed with the Specimen Cabinet pass. "Let's get your intelligence
    // engine connected" contradicted the system's own stated purpose - the user
    // is getting their own material back, not conjuring intelligence - so both
    // this line and the "Cloud Intelligence" heading lost the word.
    expect(
      screen.getByText('Everything stays on this machine. Point PMA at a model, then at your files.'),
    ).toBeDefined();
    // Regex, not an exact string: the heading now carries a "secure keyring"
    // catalogue mark in a nested span, so the h3's textContent is both.
    expect(screen.getByText(/Cloud models/)).toBeDefined();
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

  // ── Cloud consent ────────────────────────────────────────────────────────
  //
  // Setup could store a cloud API key and finish without ever asking for
  // consent. `auto` then resolved to that provider and the very first question
  // died in the dispatch gate, with the only consent control on a page absent
  // from the nav. Finishing setup must now be impossible in that state.

  const continueButton = () => screen.getByRole('button', { name: /continue/i });

  it('blocks Continue while a cloud provider still needs consent', () => {
    state.providers = [geminiConnected];
    state.routingSettings = { consent_required: true, cloud_privacy_consent: false };

    renderWithProviders(<SetupPage />);

    expect(screen.queryByText(/I understand and consent to cloud data processing/)).not.toBeNull();
    expect((continueButton() as HTMLButtonElement).disabled).toBe(true);
  });

  it('allows Continue once consent is no longer required', () => {
    state.providers = [geminiConnected];
    state.routingSettings = { consent_required: false, cloud_privacy_consent: true };
    state.driveInfo = {
      is_portable_fs: false,
      lancedb_mode: 'local',
      mount_path: '/mock/path',
      free_bytes: 100000000,
    };

    renderWithProviders(<SetupPage />);

    expect((continueButton() as HTMLButtonElement).disabled).toBe(false);
  });

  it('never asks for consent when nothing leaves the machine', () => {
    // A local-only install must not see a cloud privacy prompt at all.
    state.routingSettings = { consent_required: false, cloud_privacy_consent: false };

    renderWithProviders(<SetupPage />);

    expect(screen.queryByText(/I understand and consent to cloud data processing/)).toBeNull();
  });
});

/**
 * Replacing a key that is already stored.
 *
 * "Update" called `setKey('')` and `setSaving(false)` - both already their
 * current values - while the branch it sat in still keyed off `pData.is_set`.
 * So it re-rendered the identical "Ready" view and a stored key could not be
 * replaced from onboarding at all. Negative control: drop the `editing` flag
 * from the branch condition and the first test here fails.
 */
describe('SetupPage stored-key replacement', () => {
  beforeEach(() => {
    state.driveInfo = {
      is_portable_fs: false,
      lancedb_mode: 'local',
      mount_path: '/mock/path',
      free_bytes: 100000000,
    };
    state.loading = false;
    state.routingSettings = undefined;
    state.providers = [geminiConnected];
  });

  it('reopens the key field when Update is clicked', () => {
    renderWithProviders(<SetupPage />);

    expect(screen.queryByLabelText('Google Gemini API key')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /update/i }));

    expect(screen.getByLabelText('Google Gemini API key')).toBeDefined();
  });

  it('offers a way back out once the field is reopened', () => {
    renderWithProviders(<SetupPage />);
    fireEvent.click(screen.getByRole('button', { name: /update/i }));

    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));

    expect(screen.queryByLabelText('Google Gemini API key')).toBeNull();
  });

  it('does not offer Update for a key held in .env', () => {
    // Not ours to replace - the same rule ProvidersPage enforces by disabling
    // save when stored_in is 'env'.
    state.providers = [{ ...geminiConnected, stored_in: 'env' }];

    renderWithProviders(<SetupPage />);

    expect(screen.queryByRole('button', { name: /update/i })).toBeNull();
  });
});
