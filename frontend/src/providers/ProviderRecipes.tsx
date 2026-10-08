import { useState } from 'react';
import { setProviderSettings, setLLMPreferences, getLLMPreferences, validateProvider } from '../api';
import { invalidateCache } from '../useApi';
import { CACHE_KEYS } from '../cacheKeys'
import { Panel } from '../components/ui';

export function ProviderRecipes({
  onRecipeApplied,
}: {
  onRecipeApplied: () => void;
}) {
  const [isDismissed, setIsDismissed] = useState(
    localStorage.getItem('pma_recipes_dismissed') === 'true'
  );
  const [applying, setApplying] = useState<string | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);
  // Installed chat models offered by "Free & Local"; null until it is clicked.
  const [localModels, setLocalModels] = useState<string[] | null>(null);
  const [localChoice, setLocalChoice] = useState('');

  if (isDismissed) return null;

  const handleApply = async (
    id: string,
    fallback: string[],
    defaultModel: { provider: string; model: string | null },
    chosen?: string,
  ) => {
    setApplying(id);
    setApplyError(null);
    try {
      const currentPrefs = await getLLMPreferences();
      // A null model means "whatever is installed": the first click lists it and
      // writes nothing, so the user picks before settings change.
      const model = defaultModel.model ?? chosen ?? null;
      if (model === null) {
        // family === 'chat' comes from the provider's reported capabilities, not
        // the name. Preselect the user's saved pick if it is still installed.
        const installed = await validateProvider(defaultModel.provider, {})
          .then(r => (r.ok ? r.models : []))
          .catch(() => [])
          .then(ms => ms.filter(m => m.family === 'chat').map(m => m.id));
        if (installed.length === 0) {
          setLocalModels(null);
          throw new Error('No local models found. Install one in Ollama or LM Studio, then try again.');
        }
        const saved = currentPrefs[`${defaultModel.provider}_model`];
        setLocalChoice(installed.includes(saved) ? saved : installed[0]);
        setLocalModels(installed);
        return;
      }

      // 1. Update routing fallback chain
      await setProviderSettings({ provider: defaultModel.provider, fallback_chain: fallback });

      // 2. Set default model
      await setLLMPreferences({
        ...currentPrefs,
        provider: defaultModel.provider as any,
        [`${defaultModel.provider}_model`]: model,
      });

      invalidateCache(CACHE_KEYS.providerSettings);
      invalidateCache(CACHE_KEYS.llmPreferences);
      invalidateCache(CACHE_KEYS.currentProvider);
      setLocalModels(null);
      onRecipeApplied();
    } catch (e: any) {
      setApplyError(e.message || 'Failed to apply recipe');
    } finally {
      setApplying(null);
    }
  };

  const dismiss = () => {
    setIsDismissed(true);
    localStorage.setItem('pma_recipes_dismissed', 'true');
  };

  /**
   * Three options told apart by their words. The per-recipe icon and tone went
   * with Safelight: it has no icon set, success and info are both ink2, and
   * "Maximum Quality" in fog would have read as a fault.
   */
  const recipes = [
    {
      id: 'local',
      title: 'Free & Local',
      desc: '100% private. Runs entirely on your machine.',
      fallback: ['ollama', 'lm_studio'],
      defaultModel: { provider: 'ollama', model: null }
    },
    {
      id: 'quality',
      title: 'Maximum Quality',
      desc: 'Best available reasoning. Costs money.',
      fallback: ['anthropic', 'openai', 'gemini'],
      defaultModel: { provider: 'anthropic', model: 'claude-sonnet-5-5' }
    },
    {
      id: 'fast',
      title: 'Fast & Cheap',
      desc: 'Optimized for speed and minimal cost.',
      fallback: ['groq', 'gemini', 'openrouter'],
      defaultModel: { provider: 'groq', model: 'llama3-8b-8192' }
    }
  ];

  return (
    // Was `glass rounded-3xl border-primary/10` — the retired bridge class, a
    // radius that renders 10px anyway, and a 10%-alpha brass border that is
    // effectively invisible. `Panel` is exactly this shape.
    <Panel className="p-5 mb-2 relative">
      <button
        onClick={dismiss}
        aria-label="Dismiss quick start recipes"
        className="absolute top-4 right-4 p-1.5 hover:bg-raised rounded-sm transition-colors"
      >
        <span aria-hidden className="text-text-secondary">✕</span>
      </button>

      <div className="flex items-center gap-2 mb-4">
        <h3 className="font-bold [font-stretch:80%] text-lg m-0">Quick Start Recipes</h3>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {recipes.map(r => {
          const isApplying = applying === r.id;
          return (
            <button
              key={r.id}
              disabled={applying !== null}
              aria-busy={isApplying || undefined}
              onClick={() => handleApply(r.id, r.fallback, r.defaultModel)}
              // `--edge` rather than a brass tint: this card IS the control, and
              // WCAG 1.4.11 wants 3:1 on a boundary that identifies one.
              className="relative flex flex-col items-start text-left p-4 rounded-md border border-rule hover:border-edge hover:bg-raised transition-colors group disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <h4 className="font-medium text-sm group-hover:text-primary transition-colors">{r.title}</h4>
              <p className="text-xs text-text-secondary mt-1">{r.desc}</p>

              {isApplying && (
                // No backdrop-blur here: it sat on an opaque `bg-surface`, so it
                // blurred nothing and cost a compositor layer. Same defect the
                // raw-palette pass removed from TourOverlay.
                <div className="absolute inset-0 bg-surface rounded-md flex items-center justify-center">
                  <span className="edge-type text-text-tertiary">Applying…</span>
                </div>
              )}
            </button>
          )
        })}
      </div>
      {localModels && (
        // Same native select as Settings > Model Selection.
        <div className="mt-3 flex flex-col sm:flex-row gap-3 sm:items-end">
          <label className="text-sm text-text-secondary flex flex-col gap-1 w-full sm:w-64">
            Local model
            <select
              value={localChoice}
              onChange={e => setLocalChoice(e.target.value)}
              className="bg-raised border border-rule rounded-lg px-3 py-2 text-text-primary"
            >
              {localModels.map(m => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </label>
          <button
            disabled={applying !== null}
            onClick={() => {
              const local = recipes.find(r => r.id === 'local')!;
              handleApply(local.id, local.fallback, local.defaultModel, localChoice);
            }}
            className="glass-button !bg-plate !text-on-plate hover:!bg-plate !py-2 !px-4 disabled:opacity-50"
          >
            Use this model
          </button>
        </div>
      )}
      {applyError && (
        // `danger` aliases `--pma-error`, so the colour was right; `error` is the
        // canonical name. The 5%-alpha fill is gone — invisible tints are what
        // `border-rule` and a real edge replaced everywhere else.
        <div
          role="alert"
          className="mt-3 text-xs font-medium text-error bg-surface border border-error px-3 py-2 rounded-sm text-center animate-fade-in"
        >
          {applyError}
        </div>
      )}
    </Panel>
  );
}
