import { useState, useEffect, useRef } from 'react';
import { useApi } from '../../useApi';
import { getProviders } from '../../api';
import type { ProviderStatus } from '../../api';
import { useSessionProvider } from '../../context/SessionProviderContext';
import { Sparkles, Search, X, Check } from 'lucide-react';
import { CACHE_KEYS } from '../../cacheKeys'

// STATIC_FALLBACK_MODELS removed in favor of dynamic backend discovery and persistent model heaps.
// const STATIC_FALLBACK_MODELS: Record<string, string[]> = { ... };

export function ModelPicker() {
  const { data: providers, refetch: refreshProviders } = useApi(getProviders, { cacheKey: CACHE_KEYS.providersList });
  const { sessionModelOverride, setSessionModelOverride } = useSessionProvider();
  
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [customModel, setCustomModel] = useState('');
  const [customProvider, setCustomProvider] = useState('gemini');

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
      setCustomModel('');
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
    isOffline?: boolean;
  }

  const flatModels: FlatModelItem[] = [];
  activeProviders.forEach((p: ProviderStatus) => {
    const isOffline = !!p.last_validation?.cached_offline;
    const pModels = p.last_validation?.models?.map((m: { id: string }) => m.id) || [];
    pModels.forEach((mId: string) => {
      flatModels.push({
        providerId: p.spec.id,
        providerName: p.spec.display_name,
        modelId: mId,
        isOffline,
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

  const handleCustomSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (customModel.trim()) {
      setSessionModelOverride({ provider: customProvider, model: customModel.trim() });
      setIsOpen(false);
    }
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
          
          <div className="relative w-full max-w-lg glass rounded-2xl shadow-2xl border border-white/10 overflow-hidden flex flex-col max-h-[80vh] animate-in fade-in zoom-in duration-200">
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
                onClick={() => refreshProviders()}
                className="p-1 hover:bg-white/5 rounded-md text-text-secondary hover:text-text-primary text-[10px] transition-all"
                title="Refresh model list"
              >
                🔄 Refresh
              </button>
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
                        <div className="flex items-center gap-2">
                          <span className={`text-xs font-semibold truncate ${isSelected ? 'text-white' : 'text-text-primary'}`}>
                            {item.modelId}
                          </span>
                          {item.isOffline && (
                            <span className="text-[9px] px-1.5 py-0.5 rounded bg-yellow-500/20 text-yellow-300 font-medium">
                              Offline / Cached
                            </span>
                          )}
                        </div>
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
                <div className="py-6 text-center text-xs text-text-secondary/60">
                  No active model matching "{search}". Enter a custom model below.
                </div>
              )}
            </div>

            {/* Custom Model Input Form */}
            <form onSubmit={handleCustomSubmit} className="p-3 border-t border-white/5 bg-white/[0.02] flex items-center gap-2">
              <select
                value={customProvider}
                onChange={(e) => setCustomProvider(e.target.value)}
                className="bg-white/5 border border-white/10 rounded px-2 py-1 text-xs text-text-primary focus:outline-none"
              >
                {(providers || []).map((p: ProviderStatus) => (
                  <option key={p.spec.id} value={p.spec.id} className="bg-background text-text-primary">
                    {p.spec.display_name}
                  </option>
                ))}
              </select>
              <input
                type="text"
                placeholder="Or enter custom model ID..."
                value={customModel}
                onChange={(e) => setCustomModel(e.target.value)}
                className="flex-1 bg-white/5 border border-white/10 rounded px-2.5 py-1 text-xs text-text-primary focus:outline-none placeholder:text-text-secondary/40"
              />
              <button
                type="submit"
                disabled={!customModel.trim()}
                className="px-3 py-1 bg-primary hover:bg-primary/80 disabled:opacity-50 text-white rounded text-xs font-medium transition-all"
              >
                Use
              </button>
            </form>

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
