import { useState, useEffect, useRef } from 'react';
import { useApi } from '../../useApi';
import { getProviders } from '../../api';
import type { ProviderStatus } from '../../api';
import { useSessionProvider } from '../../context/SessionProviderContext';
import { Sparkles, Search, X, Check } from 'lucide-react';

const STATIC_FALLBACK_MODELS: Record<string, string[]> = {
  gemini: ['gemini-2.5-flash-lite', 'gemini-2.5-flash', 'gemini-2.5-pro', 'gemini-1.5-flash', 'gemini-1.5-pro'],
  openai: ['gpt-4o-mini', 'gpt-4o', 'o1-mini', 'o3-mini'],
  anthropic: ['claude-3-5-sonnet-20241022', 'claude-3-5-haiku-20241022', 'claude-3-opus-20240229'],
  groq: ['llama-3.3-70b-versatile', 'gemma2-9b-it', 'mixtral-8x7b-32768'],
  nvidia_nim: ['meta/llama-3.3-70b-instruct', 'nvidia/llama-3.1-nemotron-70b-instruct'],
  ollama: ['llama3', 'mistral', 'gemma2', 'phi3'],
  lm_studio: ['phi3', 'llama3'],
};

export function ModelPicker() {
  const { data: providers } = useApi(getProviders, { cacheKey: 'providers-list' });
  const { sessionModelOverride, setSessionModelOverride } = useSessionProvider();
  
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);

  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Keyboard shortcut Ctrl+K / Cmd+K
  useEffect(() => {
    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setIsOpen((prev) => !prev);
      }
    };
    window.addEventListener('keydown', handleGlobalKeyDown);
    return () => window.removeEventListener('keydown', handleGlobalKeyDown);
  }, []);

  useEffect(() => {
    if (isOpen) {
      setSearch('');
      setSelectedIndex(0);
      setTimeout(() => {
        inputRef.current?.focus();
      }, 50);
    }
  }, [isOpen]);

  // Resolve configured providers and models
  const activeProviders = (providers || []).filter(
    (p: ProviderStatus) => p.is_set || p.spec.id === 'ollama' || p.spec.id === 'lm_studio'
  );

  interface FlatModelItem {
    providerId: string;
    providerName: string;
    modelId: string;
  }

  const flatModels: FlatModelItem[] = [];
  activeProviders.forEach((p: ProviderStatus) => {
    const pModels = p.last_validation?.models?.map((m: { id: string }) => m.id) || STATIC_FALLBACK_MODELS[p.spec.id] || [];
    pModels.forEach((mId: string) => {
      flatModels.push({

        providerId: p.spec.id,
        providerName: p.spec.display_name,
        modelId: mId,
      });
    });
  });

  // Filter models based on search
  const filteredModels = flatModels.filter(
    (item) =>
      item.modelId.toLowerCase().includes(search.toLowerCase()) ||
      item.providerName.toLowerCase().includes(search.toLowerCase())
  );

  // Virtualization limit
  const MAX_DISPLAY = 30;
  const displayedModels = filteredModels.slice(0, MAX_DISPLAY);

  const handleSelectModel = (item: FlatModelItem) => {
    setSessionModelOverride({ provider: item.providerId, model: item.modelId });
    setIsOpen(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex((prev) => Math.min(prev + 1, displayedModels.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex((prev) => Math.max(prev - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (displayedModels[selectedIndex]) {
        handleSelectModel(displayedModels[selectedIndex]);
      }
    } else if (e.key === 'Escape') {
      setIsOpen(false);
    }
  };

  // Find active display model name
  const currentModelDisplay = sessionModelOverride
    ? sessionModelOverride.model
    : (providers?.find((p) => p.is_set)?.default_model || 'Gemini');

  return (
    <>
      {/* Trigger Button */}
      <button
        onClick={() => setIsOpen(true)}
        className="flex items-center gap-1.5 px-2.5 py-1 text-[10px] font-bold text-text-secondary hover:text-text-primary hover:bg-white/5 rounded-md transition-all uppercase tracking-wider cursor-pointer border border-white/5 bg-white/[0.02]"
        title="Change active session model (Cmd+K)"
      >
        <Sparkles className="w-3 h-3 text-primary animate-pulse" />
        <span>{currentModelDisplay}</span>
      </button>

      {/* Modal Dialog Overlay */}
      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="absolute inset-0" onClick={() => setIsOpen(false)} />
          
          <div className="relative w-full max-w-lg glass rounded-2xl shadow-2xl border border-white/10 overflow-hidden flex flex-col max-h-[75vh] animate-in fade-in zoom-in duration-200">
            {/* Header */}
            <div className="flex items-center gap-3 px-4 py-3.5 border-b border-white/5 bg-white/[0.02]">
              <Search className="w-4 h-4 text-text-secondary" />
              <input
                ref={inputRef}
                type="text"
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  setSelectedIndex(0);
                }}
                onKeyDown={handleKeyDown}
                placeholder="Search models or providers..."
                className="flex-1 bg-transparent text-sm text-text-primary focus:outline-none placeholder:text-text-secondary/50"
              />
              <button
                onClick={() => setIsOpen(false)}
                className="p-1 hover:bg-white/5 rounded-md text-text-secondary hover:text-text-primary transition-all"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* List */}
            <div
              ref={listRef}
              className="flex-1 overflow-y-auto p-2 space-y-1 divide-y divide-white/[0.02]"
            >
              {displayedModels.length > 0 ? (
                displayedModels.map((item, index) => {
                  const isSelected = selectedIndex === index;
                  const isActiveOverride =
                    sessionModelOverride?.provider === item.providerId &&
                    sessionModelOverride?.model === item.modelId;
                  
                  return (
                    <div
                      key={`${item.providerId}-${item.modelId}`}
                      onClick={() => handleSelectModel(item)}
                      onMouseEnter={() => setSelectedIndex(index)}
                      className={`flex items-center justify-between px-3 py-2.5 rounded-lg cursor-pointer transition-all ${
                        isSelected ? 'bg-primary text-white shadow-md' : 'hover:bg-white/5 text-text-secondary hover:text-text-primary'
                      }`}
                    >
                      <div className="flex flex-col items-start text-left min-w-0">
                        <span className={`text-xs font-semibold truncate w-full ${isSelected ? 'text-white' : 'text-text-primary'}`}>
                          {item.modelId}
                        </span>
                        <span className={`text-[10px] uppercase tracking-wider ${isSelected ? 'text-white/80' : 'text-text-secondary/80'}`}>
                          {item.providerName}
                        </span>
                      </div>
                      {(isActiveOverride || (!sessionModelOverride && item.modelId === currentModelDisplay)) && (
                        <Check className={`w-4 h-4 flex-shrink-0 ${isSelected ? 'text-white' : 'text-primary'}`} />
                      )}
                    </div>
                  );
                })
              ) : (
                <div className="py-8 text-center text-xs text-text-secondary/60">
                  No active provider or model matching "{search}"
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="px-4 py-2 border-t border-white/5 bg-white/[0.01] flex items-center justify-between text-[10px] text-text-secondary">
              <span>
                {filteredModels.length > MAX_DISPLAY
                  ? `Showing 30 of ${filteredModels.length} models. Type to narrow search.`
                  : `${filteredModels.length} models available`}
              </span>
              <span className="flex items-center gap-1.5">
                <kbd className="px-1.5 py-0.5 bg-white/5 border border-white/10 rounded text-[9px]">↑↓</kbd> navigate
                <kbd className="px-1.5 py-0.5 bg-white/5 border border-white/10 rounded text-[9px]">Enter</kbd> select
              </span>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
