import { Rocket, Zap, Shield, Star, X, Loader2 } from 'lucide-react';
import { useState } from 'react';
import { setProviderSettings, setLLMPreferences, getLLMPreferences } from '../api';
import { invalidateCache } from '../useApi';

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

      invalidateCache('provider-settings');
      invalidateCache('llm-preferences');
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

  const recipes = [
    {
      id: 'local',
      title: 'Free & Local',
      desc: '100% private. Runs entirely on your machine.',
      icon: Shield,
      color: 'text-emerald-500',
      bg: 'bg-emerald-500/10',
      fallback: ['ollama', 'lmstudio'],
      defaultModel: { provider: 'ollama', model: 'llama3:8b' }
    },
    {
      id: 'quality',
      title: 'Maximum Quality',
      desc: 'Best available reasoning. Costs money.',
      icon: Star,
      color: 'text-amber-500',
      bg: 'bg-amber-500/10',
      fallback: ['anthropic', 'openai', 'gemini'],
      defaultModel: { provider: 'anthropic', model: 'claude-3-5-sonnet-20240620' }
    },
    {
      id: 'fast',
      title: 'Fast & Cheap',
      desc: 'Optimized for speed and minimal cost.',
      icon: Zap,
      color: 'text-accent-blue',
      bg: 'bg-accent-blue/10',
      fallback: ['groq', 'gemini', 'openrouter'],
      defaultModel: { provider: 'groq', model: 'llama3-8b-8192' }
    }
  ];

  return (
    <div className="glass rounded-3xl p-5 border border-primary/10 bg-white/40 mb-2 relative">
      <button 
        onClick={dismiss} 
        className="absolute top-4 right-4 p-1.5 hover:bg-black/5 rounded-full transition-colors"
      >
        <X className="w-4 h-4 text-text-secondary" />
      </button>
      
      <div className="flex items-center gap-2 mb-4">
        <Rocket className="w-5 h-5 text-primary" />
        <h3 className="font-bold text-sm">Quick Start Recipes</h3>
      </div>
      
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {recipes.map(r => {
          const Icon = r.icon;
          const isApplying = applying === r.id;
          return (
            <button
              key={r.id}
              disabled={applying !== null}
              onClick={() => handleApply(r.id, r.fallback, r.defaultModel)}
              className="relative flex flex-col items-start text-left p-4 rounded-2xl border border-primary/10 hover:border-primary/30 hover:bg-white/50 transition-all group disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <div className={`p-2 rounded-xl ${r.bg} mb-3`}>
                <Icon className={`w-4 h-4 ${r.color}`} />
              </div>
              <h4 className="font-bold text-sm group-hover:text-primary transition-colors">{r.title}</h4>
              <p className="text-[11px] text-text-secondary mt-1">{r.desc}</p>
              
              {isApplying && (
                <div className="absolute inset-0 bg-white/50 backdrop-blur-[1px] rounded-2xl flex items-center justify-center">
                  <Loader2 className="w-5 h-5 text-primary animate-spin" />
                </div>
              )}
            </button>
          )
        })}
      </div>
      {applyError && (
        <div className="mt-3 text-xs font-medium text-danger bg-danger/5 border border-danger/10 px-3 py-2 rounded-lg text-center animate-fade-in">
          {applyError}
        </div>
      )}
    </div>
  );
}
