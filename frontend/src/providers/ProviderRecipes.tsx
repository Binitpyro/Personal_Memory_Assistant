import { Rocket, Zap, Shield, Star, X, Loader2 } from 'lucide-react';
import { useState } from 'react';
import { setProviderSettings, setLLMPreferences, getLLMPreferences } from '../api';
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

  if (isDismissed) return null;

  const handleApply = async (id: string, fallback: string[], defaultModel: { provider: string; model: string }) => {
    setApplying(id);
    setApplyError(null);
    try {
      // 1. Update routing fallback chain
      await setProviderSettings({ provider: defaultModel.provider, fallback_chain: fallback });

      // Fetch latest prefs
      const currentPrefs = await getLLMPreferences();
      // 2. Set default model
      await setLLMPreferences({
        ...currentPrefs,
        provider: defaultModel.provider as any,
        [`${defaultModel.provider}_model`]: defaultModel.model,
      });

      invalidateCache(CACHE_KEYS.providerSettings);
      invalidateCache(CACHE_KEYS.llmPreferences);
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
   * The three-way colour coding is kept on purpose: these are three options a
   * user picks between, and the tone carries which is which.
   *
   * What changed is that it is now expressed in tokens. `text-amber-500` /
   * `bg-amber-500/10` were raw palette values authored for the dark theme, and
   * this panel renders on the themed page rather than over the canvas — so on
   * Paper the chip washed out the way the consent banners did. `success`,
   * `warning` and `info` are the measured tokens and map onto the three
   * recipes without straining the meaning.
   */
  const recipes = [
    {
      id: 'local',
      title: 'Free & Local',
      desc: '100% private. Runs entirely on your machine.',
      icon: Shield,
      color: 'text-success',
      bg: 'bg-success/10',
      fallback: ['ollama', 'lmstudio'],
      defaultModel: { provider: 'ollama', model: 'llama3:8b' }
    },
    {
      id: 'quality',
      title: 'Maximum Quality',
      desc: 'Best available reasoning. Costs money.',
      icon: Star,
      color: 'text-warning',
      bg: 'bg-warning/10',
      fallback: ['anthropic', 'openai', 'gemini'],
      defaultModel: { provider: 'anthropic', model: 'claude-3-5-sonnet-20240620' }
    },
    {
      id: 'fast',
      title: 'Fast & Cheap',
      desc: 'Optimized for speed and minimal cost.',
      icon: Zap,
      // `accent-blue` is a real alias for `--pma-info`, so it emitted CSS and was
      // never broken. One name per token, though.
      color: 'text-info',
      bg: 'bg-info/10',
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
        <X className="w-4 h-4 text-text-secondary" aria-hidden />
      </button>

      <div className="flex items-center gap-2 mb-4">
        <Rocket className="w-5 h-5 text-primary" aria-hidden />
        <h3 className="font-serif text-base font-medium m-0">Quick Start Recipes</h3>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {recipes.map(r => {
          const Icon = r.icon;
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
              <div className={`p-2 rounded-sm ${r.bg} mb-3`}>
                <Icon className={`w-4 h-4 ${r.color}`} aria-hidden />
              </div>
              <h4 className="font-medium text-sm group-hover:text-primary transition-colors">{r.title}</h4>
              <p className="text-[11px] text-text-secondary mt-1">{r.desc}</p>

              {isApplying && (
                // No backdrop-blur here: it sat on an opaque `bg-surface`, so it
                // blurred nothing and cost a compositor layer. Same defect the
                // raw-palette pass removed from TourOverlay.
                <div className="absolute inset-0 bg-surface rounded-md flex items-center justify-center">
                  <Loader2 className="w-5 h-5 text-primary animate-spin" aria-hidden />
                </div>
              )}
            </button>
          )
        })}
      </div>
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
