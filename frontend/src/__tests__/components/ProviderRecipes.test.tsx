import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ProviderRecipes } from '../../providers/ProviderRecipes';
import { setProviderSettings, setLLMPreferences, getLLMPreferences } from '../../api';

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
