import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ProviderRecipes } from '../../providers/ProviderRecipes';
import { setProviderSettings, setLLMPreferences, getLLMPreferences, setProviderDefaultModel, validateProvider } from '../../api';

/**
 * First coverage for this component.
 *
 * It had none, and it is not decorative: applying a recipe rewrites the routing
 * fallback chain AND the default model in two sequential calls, so a partial
 * failure leaves the two disagreeing. It also renders inside ProvidersPage, so
 * it survived the four-page design pass untouched and was the one panel still
 * carrying raw palette values.
 *
 * Nothing here asserts on a class name. §10 of 06_DESIGN_SYSTEM.md records a
 * test that broke on a restyle for exactly that, and `check-utilities.mjs`
 * already proves every class emits a rule.
 */
vi.mock('../../api', () => ({
  setProviderSettings: vi.fn(() => Promise.resolve({})),
  setLLMPreferences: vi.fn(() => Promise.resolve({})),
  getLLMPreferences: vi.fn(() => Promise.resolve({ provider: 'ollama' })),
  setProviderDefaultModel: vi.fn(() => Promise.resolve({ status: 'success' })),
  validateProvider: vi.fn(),
}));

vi.mock('../../useApi', () => ({
  invalidateCache: vi.fn(),
}));

describe('ProviderRecipes', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('offers all three recipes', () => {
    render(<ProviderRecipes onRecipeApplied={vi.fn()} />);

    expect(screen.getByText('Free & Local')).toBeDefined();
    expect(screen.getByText('Maximum Quality')).toBeDefined();
    expect(screen.getByText('Fast & Cheap')).toBeDefined();
  });

  it('applies the fallback chain and the default model together', async () => {
    const onApplied = vi.fn();
    render(<ProviderRecipes onRecipeApplied={onApplied} />);

    fireEvent.click(screen.getByText('Fast & Cheap'));

    await waitFor(() => expect(onApplied).toHaveBeenCalled());

    // The chain and the provider must agree - the whole point of a "recipe".
    expect(setProviderSettings).toHaveBeenCalledWith({
      provider: 'groq',
      fallback_chain: ['groq', 'gemini', 'openrouter'],
    });
    // Merged onto whatever preferences already exist, not replacing them.
    expect(getLLMPreferences).toHaveBeenCalled();
    expect(setLLMPreferences).toHaveBeenCalledWith(
      expect.objectContaining({ provider: 'groq', groq_model: 'llama3-8b-8192' }),
    );
    // Dispatch reads per_provider.default_model first; without this a model
    // saved on the Providers page overrides the recipe.
    expect(setProviderDefaultModel).toHaveBeenCalledWith('groq', 'llama3-8b-8192');
  });

  it('surfaces a failure inline instead of silently doing nothing', async () => {
    vi.mocked(setProviderSettings).mockRejectedValueOnce(new Error('network is down'));
    const onApplied = vi.fn();

    render(<ProviderRecipes onRecipeApplied={onApplied} />);
    fireEvent.click(screen.getByText('Maximum Quality'));

    expect(await screen.findByText('network is down')).toBeDefined();
    // A half-applied recipe must not report success.
    expect(onApplied).not.toHaveBeenCalled();
    expect(setLLMPreferences).not.toHaveBeenCalled();
  });

  describe('Free & Local', () => {
    const models = (...ids: [string, string][]) =>
      ids.map(([id, family]) => ({ id, family, context_length: null, pricing_hint: 0 }));
    const validation = (ms: ReturnType<typeof models>, ok = true) =>
      Promise.resolve({ ok, latency_ms: 1, models: ms, error: null, error_code: null, server_time: null });

    /** Click the recipe, wait for the installed-model select, then apply. */
    const listThenApply = async (onApplied: () => void, pick?: string) => {
      fireEvent.click(screen.getByText('Free & Local'));
      const select = (await screen.findByLabelText('Local model')) as HTMLSelectElement;
      if (pick) fireEvent.change(select, { target: { value: pick } });
      fireEvent.click(screen.getByRole('button', { name: 'Use this model' }));
      await waitFor(() => expect(onApplied).toHaveBeenCalled());
      return select;
    };

    it('writes the lm_studio id, not lmstudio', async () => {
      vi.mocked(validateProvider).mockReturnValue(validation(models(['qwen3:4b', 'chat'])));
      const onApplied = vi.fn();
      render(<ProviderRecipes onRecipeApplied={onApplied} />);

      await listThenApply(onApplied);

      expect(setProviderSettings).toHaveBeenCalledWith({
        provider: 'ollama',
        fallback_chain: ['ollama', 'lm_studio'],
      });
    });

    it('lists only installed chat models and writes nothing until one is chosen', async () => {
      vi.mocked(validateProvider).mockReturnValue(
        validation(models(['glm-ocr', 'vision'], ['nomic-embed-text', 'embedding'], ['qwen3:4b', 'chat'], ['gemma2:2b', 'chat'])),
      );
      const onApplied = vi.fn();
      render(<ProviderRecipes onRecipeApplied={onApplied} />);

      fireEvent.click(screen.getByText('Free & Local'));
      const select = (await screen.findByLabelText('Local model')) as HTMLSelectElement;

      expect(validateProvider).toHaveBeenCalledWith('ollama', {});
      expect([...select.options].map(o => o.value)).toEqual(['qwen3:4b', 'gemma2:2b']);
      expect(select.value).toBe('qwen3:4b');
      expect(setProviderSettings).not.toHaveBeenCalled();
      expect(setLLMPreferences).not.toHaveBeenCalled();
    });

    it('writes the model the user picked', async () => {
      vi.mocked(validateProvider).mockReturnValue(
        validation(models(['qwen3:4b', 'chat'], ['gemma2:2b', 'chat'])),
      );
      const onApplied = vi.fn();
      render(<ProviderRecipes onRecipeApplied={onApplied} />);

      await listThenApply(onApplied, 'gemma2:2b');

      expect(setLLMPreferences).toHaveBeenCalledWith(
        expect.objectContaining({ provider: 'ollama', ollama_model: 'gemma2:2b' }),
      );
      expect(setProviderDefaultModel).toHaveBeenCalledWith('ollama', 'gemma2:2b');
      expect(screen.queryByLabelText('Local model')).toBeNull();
    });

    it('preselects the saved model when it is still installed', async () => {
      const saved = { provider: 'ollama', ollama_model: 'gemma2:2b' };
      // Read once to list, once to apply.
      vi.mocked(getLLMPreferences).mockResolvedValueOnce(saved).mockResolvedValueOnce(saved);
      vi.mocked(validateProvider).mockReturnValue(
        validation(models(['qwen3:4b', 'chat'], ['gemma2:2b', 'chat'])),
      );
      const onApplied = vi.fn();
      render(<ProviderRecipes onRecipeApplied={onApplied} />);

      const select = await listThenApply(onApplied);

      expect(select.value).toBe('gemma2:2b');
      expect(setLLMPreferences).toHaveBeenCalledWith(
        expect.objectContaining({ ollama_model: 'gemma2:2b' }),
      );
    });

    it.each([
      ['no models installed', () => validation([])],
      ['only a vision model', () => validation(models(['glm-ocr', 'vision']))],
      ['Ollama reports not ok', () => validation(models(['qwen3:4b', 'chat']), false)],
      ['Ollama unreachable', () => Promise.reject(new Error('Failed to fetch'))],
    ])('writes nothing and says so when %s', async (_name, result) => {
      vi.mocked(validateProvider).mockImplementation(result as () => ReturnType<typeof validateProvider>);
      const onApplied = vi.fn();
      render(<ProviderRecipes onRecipeApplied={onApplied} />);

      fireEvent.click(screen.getByText('Free & Local'));

      expect(
        await screen.findByText(/no local models found\. install one in ollama or lm studio/i),
      ).toBeDefined();
      expect(setProviderSettings).not.toHaveBeenCalled();
      expect(setLLMPreferences).not.toHaveBeenCalled();
      expect(onApplied).not.toHaveBeenCalled();
    });
  });

  it('dismisses, and stays dismissed across a remount', () => {
    const { unmount } = render(<ProviderRecipes onRecipeApplied={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: /dismiss quick start recipes/i }));
    expect(screen.queryByText('Free & Local')).toBeNull();

    // The dismissal is persisted, so it must not come back on the next mount.
    unmount();
    render(<ProviderRecipes onRecipeApplied={vi.fn()} />);
    expect(screen.queryByText('Free & Local')).toBeNull();
  });

  it('names the close control for assistive tech', () => {
    // It was an icon-only button with no accessible name at all.
    render(<ProviderRecipes onRecipeApplied={vi.fn()} />);
    expect(screen.getByRole('button', { name: /dismiss quick start recipes/i })).toBeDefined();
  });
});
