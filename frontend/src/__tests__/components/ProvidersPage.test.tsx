import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { ProvidersPage } from '../../pages/ProvidersPage';
import { renderWithProviders } from '../test-utils';
import { setProviderKey, deleteProviderKey } from '../../api';

// TourOverlay mounts inside this page and scrolls its anchor into view; jsdom
// does not implement scrollIntoView.
Element.prototype.scrollIntoView = vi.fn();

const state = vi.hoisted(() => ({
  providers: [] as Record<string, unknown>[],
  routingSettings: { provider: 'auto', fallback_chain: [], cloud_privacy_consent: true } as Record<string, unknown>,
}));

vi.mock('../../useApi', () => ({
  useApi: vi.fn((_fn, opts) => {
    const byKey: Record<string, unknown> = {
      'providers-list': state.providers,
      'llm-preferences': { provider: 'auto' },
      'provider-settings': state.routingSettings,
    };
    return {
      data: byKey[opts?.cacheKey as string],
      loading: false,
      error: null,
      refetch: vi.fn(),
    };
  }),
  invalidateCache: vi.fn(),
}));

vi.mock('../../useOptimisticMutation', () => ({
  useOptimisticMutation: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('../../api', () => ({
  getProviders: vi.fn(),
  getLLMPreferences: vi.fn(),
  setLLMPreferences: vi.fn(),
  getProviderSettings: vi.fn(),
  setProviderSettings: vi.fn(),
  selfTestProvider: vi.fn(),
  validateProvider: vi.fn(),
  setProviderKey: vi.fn().mockResolvedValue({ status: 'ok' }),
  deleteProviderKey: vi.fn().mockResolvedValue({ status: 'ok' }),
  setProviderDefaultModel: vi.fn(),
}));

/** A provider whose endpoint the user is allowed to edit. */
const editable = {
  spec: {
    id: 'openai_compatible',
    display_name: 'OpenAI-compatible',
    kind: 'custom',
    default_base_url: 'http://localhost:8000/v1',
    base_url_editable: true,
    api_key_docs_url: '',
    supported_features: [],
  },
  is_set: true,
  preview: 'sk-...',
  stored_in: 'keyring',
  base_url: 'http://localhost:8000/v1',
  default_model: null,
};

function selectProvider() {
  fireEvent.click(screen.getByRole('button', { name: /OpenAI-compatible/i }));
}

describe('ProvidersPage connection details', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.providers = [editable];
    state.routingSettings = { provider: 'auto', fallback_chain: [], cloud_privacy_consent: true };
  });

  it('saves an edited Base Endpoint URL', async () => {
    // The field was editable and was sent on Validate, but handleSave only ever
    // passed the API key — so an endpoint change appeared to save and silently
    // did not. The backend has accepted base_url on this route all along.
    renderWithProviders(<ProvidersPage />);
    selectProvider();

    const url = screen.getByDisplayValue('http://localhost:8000/v1');
    fireEvent.change(url, { target: { value: 'http://192.168.1.9:9000/v1' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Configuration/i }));

    await waitFor(() =>
      expect(setProviderKey).toHaveBeenCalledWith(
        'openai_compatible',
        null,
        'http://192.168.1.9:9000/v1',
      ),
    );
  });

  it('does not touch the endpoint when only the key changed', async () => {
    // Sending base_url unconditionally would overwrite a stored endpoint every
    // time someone rotated a key.
    renderWithProviders(<ProvidersPage />);
    selectProvider();

    const key = screen.getByDisplayValue('••••••••••••••••');
    fireEvent.change(key, { target: { value: 'sk-newkey' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Configuration/i }));

    await waitFor(() => expect(setProviderKey).toHaveBeenCalled());
    expect(vi.mocked(setProviderKey).mock.calls[0][2]).toBeUndefined();
  });

  it('does not remove a connection on the first click', async () => {
    // Deletes the key from the OS keyring with no undo.
    renderWithProviders(<ProvidersPage />);
    selectProvider();

    fireEvent.click(screen.getByRole('button', { name: /Remove Connection/i }));

    expect(deleteProviderKey).not.toHaveBeenCalled();
  });
});
