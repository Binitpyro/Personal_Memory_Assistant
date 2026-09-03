import { useState, useEffect, useRef, useId } from 'react';
import { useApi } from '../../useApi';
import { getProviders } from '../../api';
import type { ProviderStatus } from '../../api';
import { useSessionProvider } from '../../context/SessionProviderContext';
import { Sparkles, Search, X, Check } from 'lucide-react';
import { CACHE_KEYS } from '../../cacheKeys'
import { Badge } from '../ui';

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
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchId = useId();
  const customModelId = useId();
  const customProviderId = useId();

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

  // `showModal()` rather than a hand-rolled overlay: the platform supplies the
  // focus trap, Escape, an inert background and the top layer. What replaced
  // was a fixed div plus a click-catching sibling div with no keyboard path,
  // and an Escape branch that only fired while the search input held focus.
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (isOpen) {
      setSearch('');
      setSelectedIndex(0);
      setCustomModel('');
      if (!dialog.open) dialog.showModal();
      inputRef.current?.focus();
    } else if (dialog.open) {
      dialog.close();
    }
  }, [isOpen]);

  // Escape closes the dialog through the user agent, not through setIsOpen, so
  // the DOM has to be mirrored back into state. Without this `isOpen` stays
  // true, the trigger's setIsOpen(true) is a no-op, the effect above never
  // re-runs, and the palette cannot be reopened without a reload.
  //
  // A native listener rather than React's `onClose`. React's synthetic handler
  // does work here — that was checked, and a test with `onClose` restored still
  // passes — but `close` is a non-bubbling event, which is the category React
  // has to special-case, and this does not depend on it continuing to.
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const onNativeClose = () => {
      setIsOpen(false);
      triggerRef.current?.focus();
    };
    dialog.addEventListener('close', onNativeClose);
    return () => dialog.removeEventListener('close', onNativeClose);
  }, []);

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
        ref={triggerRef}
        type="button"
        onClick={() => setIsOpen(true)}
        className="flex items-center gap-1.5 px-2.5 py-1 text-[10px] font-bold text-text-secondary hover:text-text-primary hover:bg-raised rounded-md transition-[color,background-color] uppercase tracking-wider cursor-pointer border border-rule bg-raised"
        title="Change active session model (Cmd+K)"
      >
        <Sparkles className="w-3 h-3 text-primary animate-pulse" aria-hidden />
        <span>{currentModelDisplay}</span>
      </button>

      {/* Modal Dialog. Always mounted so the element exists for showModal(). */}
      <dialog
        ref={dialogRef}
        aria-label="Change session model"
        className="w-full max-w-lg glass rounded-2xl shadow-2xl border border-rule overflow-hidden flex-col max-h-[80vh] p-0 bg-surface text-text-primary"
      >
        <div className="flex flex-col max-h-[80vh]">
            {/* Header */}
            <div className="flex items-center gap-3 px-4 py-3.5 border-b border-rule bg-raised">
              <label htmlFor={searchId} className="sr-only">
                Search models or providers
              </label>
              <Search className="w-4 h-4 text-text-secondary" aria-hidden />
              <input
                ref={inputRef}
                id={searchId}
                type="search"
                spellCheck={false}
                autoComplete="off"
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  setSelectedIndex(0);
                }}
                onKeyDown={handleKeyDown}
                placeholder="e.g. llama3 or Ollama…"
                className="flex-1 bg-transparent text-sm text-text-primary placeholder:text-text-secondary/50"
              />
              <button
                type="button"
                onClick={() => refreshProviders()}
                className="p-1 hover:bg-raised rounded-md text-text-secondary hover:text-text-primary text-[10px] transition-[color,background-color]"
                title="Refresh model list"
              >
                <span aria-hidden>🔄</span> Refresh
              </button>
              <button
                type="button"
                onClick={() => setIsOpen(false)}
                aria-label="Close model picker"
                className="p-1 hover:bg-raised rounded-md text-text-secondary hover:text-text-primary transition-[color,background-color]"
              >
                <X className="w-4 h-4" aria-hidden />
              </button>
            </div>

            {/* List */}
            <div
              ref={listRef}
              className="flex-1 overflow-y-auto overscroll-contain p-2 space-y-1 divide-y divide-rule"
            >
              {displayedModels.length > 0 ? (
                displayedModels.map((item, index) => {
                  const isSelected = selectedIndex === index;
                  const isActiveOverride =
                    sessionModelOverride?.provider === item.providerId &&
                    sessionModelOverride?.model === item.modelId;
                  
                  return (
                    <button
                      key={`${item.providerId}-${item.modelId}`}
                      type="button"
                      onClick={() => handleSelectModel(item)}
                      onMouseEnter={() => setSelectedIndex(index)}
                      aria-current={isActiveOverride || undefined}
                      className={`w-full text-left flex items-center justify-between px-3 py-2.5 rounded-lg cursor-pointer transition-[background-color,color,box-shadow] ${
                        isSelected ? 'bg-plate text-on-plate shadow-md' : 'hover:bg-raised text-text-secondary hover:text-text-primary'
                      }`}
                    >
                      <div className="flex flex-col items-start text-left min-w-0">
                        <div className="flex items-center gap-2">
                          <span className={`text-xs font-semibold truncate ${isSelected ? 'text-on-plate' : 'text-text-primary'}`}>
                            {item.modelId}
                          </span>
                          {/* Was `bg-yellow-500/20 text-yellow-300`: raw palette, and
                              #fde047 on Paper's #F1ECDF panel measures about 1.2. */}
                          {item.isOffline && <Badge tone="warning">Offline / Cached</Badge>}
                        </div>
                        <span className={`text-[10px] uppercase tracking-wider ${isSelected ? 'text-text-secondary' : 'text-text-secondary/80'}`}>
                          {item.providerName}
                        </span>
                      </div>
                      {(isActiveOverride || (!sessionModelOverride && item.modelId === currentModelDisplay)) && (
                        <Check
                          className={`w-4 h-4 flex-shrink-0 ${isSelected ? 'text-on-plate' : 'text-primary'}`}
                          aria-label="Active model"
                        />
                      )}
                    </button>
                  );
                })
              ) : (
                <div className="py-6 text-center text-xs text-text-secondary/60">
                  No active model matching “{search}”. Enter a custom model below.
                </div>
              )}
            </div>

            {/* Custom Model Input Form */}
            <form onSubmit={handleCustomSubmit} className="p-3 border-t border-rule bg-raised flex items-center gap-2">
              <label htmlFor={customProviderId} className="sr-only">
                Provider for the custom model
              </label>
              <select
                id={customProviderId}
                value={customProvider}
                onChange={(e) => setCustomProvider(e.target.value)}
                className="bg-raised border border-rule rounded px-2 py-1 text-xs text-text-primary"
              >
                {(providers || []).map((p: ProviderStatus) => (
                  <option key={p.spec.id} value={p.spec.id} className="bg-background text-text-primary">
                    {p.spec.display_name}
                  </option>
                ))}
              </select>
              <label htmlFor={customModelId} className="sr-only">
                Custom model ID
              </label>
              <input
                id={customModelId}
                type="text"
                spellCheck={false}
                autoComplete="off"
                placeholder="Or a model ID, e.g. llama3:8b…"
                value={customModel}
                onChange={(e) => setCustomModel(e.target.value)}
                className="flex-1 bg-raised border border-rule rounded px-2.5 py-1 text-xs text-text-primary placeholder:text-text-secondary/40"
              />
              <button
                type="submit"
                disabled={!customModel.trim()}
                className="px-3 py-1 bg-plate hover:brightness-110 disabled:opacity-50 text-on-plate rounded text-xs font-medium transition-[filter,opacity]"
              >
                Use
              </button>
            </form>

            {/* Footer */}
            <div className="px-4 py-2 border-t border-rule bg-raised flex items-center justify-between text-[10px] text-text-secondary">
              <span>
                {filteredModels.length > MAX_DISPLAY
                  ? `Showing 30 of ${filteredModels.length} models. Type to narrow search.`
                  : `${filteredModels.length} models available`}
              </span>
              <span className="flex items-center gap-1.5">
                <kbd className="px-1.5 py-0.5 bg-raised border border-rule rounded text-[9px]">↑↓</kbd> navigate
                <kbd className="px-1.5 py-0.5 bg-raised border border-rule rounded text-[9px]">Enter</kbd> select
              </span>
            </div>
        </div>
      </dialog>
    </>
  );
}
