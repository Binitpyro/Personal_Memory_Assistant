import { useState, useEffect, useMemo } from 'react';
import {
  getProviders,
  selfTestProvider,
  setLLMPreferences,
  getLLMPreferences,
  getProviderSettings,
  type ProviderRoutingSettings,
  setProviderSettings,
} from '../api';
import type {
  ValidationResponse,
} from '../api';
import { toast } from 'sonner';
import { useApi } from '../useApi';

import { ProviderIcon } from '../providers/icons';
import { useProviderValidation } from '../providers/useProviderValidation';
import { validateApiKeyFormat } from '../providers/keyValidators';
import { ProviderSparkline } from '../providers/ProviderSparkline';
import { ProviderRecipes } from '../providers/ProviderRecipes';
import { TourOverlay } from '../providers/TourOverlay';
import {
  CheckCircle2,
  XCircle,
  Lock,
  Globe,
  ArrowLeft,
  Key,
  RefreshCw,
  Search,
  Star,
  Zap,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { CACHE_KEYS } from '../cacheKeys'
import { useOptimisticMutation } from '../useOptimisticMutation'

export function ProvidersPage() {
  const navigate = useNavigate();
  const { data: providers, refetch: refetchProviders, loading } = useApi(getProviders, { cacheKey: CACHE_KEYS.providersList });
  const { data: prefs, refetch: refetchPrefs } = useApi(getLLMPreferences, { cacheKey: CACHE_KEYS.llmPreferences });
  const { data: routingSettings, refetch: refetchSettings } = useApi(getProviderSettings, { cacheKey: CACHE_KEYS.providerSettings });

  // Both of these write provider settings, so they share one cache entry and one
  // optimistic path: the list reorders and the checkbox moves on click instead
  // of waiting for the round trip and the refetch behind it.
  const updateRoutingSettings = useOptimisticMutation<
    Partial<ProviderRoutingSettings>,
    unknown,
    ProviderRoutingSettings
  >({
    mutationFn: setProviderSettings,
    cacheKey: CACHE_KEYS.providerSettings,
    optimistic: (current, patch) => (current ? { ...current, ...patch } : current),
  });

  const handleMoveFallback = (index: number, direction: number) => {
    if (!routingSettings) return;
    const newChain = [...routingSettings.fallback_chain];
    const targetIndex = index + direction;
    if (targetIndex < 0 || targetIndex >= newChain.length) return;

    // Swap
    const temp = newChain[index];
    newChain[index] = newChain[targetIndex];
    newChain[targetIndex] = temp;

    updateRoutingSettings.mutate({
      provider: routingSettings.provider,
      fallback_chain: newChain,
    });
  };

  const [selectedId, setSelectedId] = useState<string>('gemini');
  const selectedProvider = useMemo(() => {
    return providers?.find(p => p.spec.id === selectedId);
  }, [providers, selectedId]);

  const [validationStatuses, setValidationStatuses] = useState<Record<string, ValidationResponse>>({});
  const [isValidatingAll, setIsValidatingAll] = useState(false);

  // Detail pane state
  const [apiKey, setApiKeyInput] = useState('');
  const [baseUrl, setBaseUrlInput] = useState('');
  const [modelSearch, setModelSearch] = useState('');
  const [saveError, setSaveError] = useState<string | null>(null);
  const [pinnedModels, setPinnedModels] = useState<string[]>(() => {
    try {
      return JSON.parse(localStorage.getItem('pma_pinned_models') || '[]');
    } catch {
      return [];
    }
  });

  const {
    isValidating,
    isSaving,
    validationResult,
    validate,
    saveKey,
    deleteKey,
    saveDefaultModel,
    setValidationResult,
  } = useProviderValidation(selectedId);

  // Initialize fields when selected provider changes
  useEffect(() => {
    if (selectedProvider) {
      setApiKeyInput(selectedProvider.is_set ? '••••••••••••••••' : '');
      setBaseUrlInput(selectedProvider.base_url || selectedProvider.spec.default_base_url || '');
      setValidationResult(validationStatuses[selectedId] || null);
    }
  }, [selectedId, selectedProvider, validationStatuses]);

  // Sync validation result to statuses cache
  useEffect(() => {
    if (validationResult) {
      setValidationStatuses(prev => ({
        ...prev,
        [selectedId]: validationResult,
      }));
    }
  }, [validationResult, selectedId]);

  // Validate format check
  const formatCheck = useMemo(() => {
    if (!apiKey || apiKey === '••••••••••••••••') return { isValid: true };
    return validateApiKeyFormat(selectedId, apiKey);
  }, [selectedId, apiKey]);

  const handleValidate = async () => {
    const keyToSend = apiKey === '••••••••••••••••' ? null : apiKey;
    const urlToSend = baseUrl || null;
    await validate(keyToSend, urlToSend);
  };

  const handleSave = async () => {
    setSaveError(null);
    try {
      const keyChanged = !!apiKey && apiKey !== '••••••••••••••••';
      // The Base Endpoint URL was editable, was sent on Validate, and was
      // silently dropped on Save - handleSave only ever passed the API key, so
      // an endpoint change appeared to succeed and never persisted. Gated on
      // base_url_editable because the backend 400s otherwise.
      const urlChanged =
        !!selectedProvider?.spec.base_url_editable &&
        (baseUrl || null) !== (selectedProvider.base_url || null);

      if (keyChanged || urlChanged) {
        const ok = await saveKey(
          keyChanged ? apiKey : null,
          urlChanged ? (baseUrl || null) : undefined,
        );
        if (!ok) {
          setSaveError('Failed to verify and save the connection details.');
          return;
        }
      }
      if (selectedProvider) {
        await refetchProviders();
      }
    } catch (e: any) {
      setSaveError(e.message || 'An unexpected error occurred while saving.');
    }
  };

  const handleDelete = () => {
    // Removing a connection deletes the stored key from the OS keyring;
    // there is no undo and no copy of it left in the app.
    toast('Remove this connection?', {
      description: `The stored key for ${selectedProvider?.spec.display_name ?? 'this provider'} is deleted from your keyring.`,
      action: { label: 'Remove', onClick: () => void doDelete() },
      cancel: { label: 'Cancel', onClick: () => {} },
    });
  };

  const doDelete = async () => {
    setSaveError(null);
    try {
      const ok = await deleteKey();
      if (ok) {
        setApiKeyInput('');
        await refetchProviders();
      } else {
        setSaveError('Failed to remove the connection.');
      }
    } catch (e: any) {
      setSaveError(e.message || 'An unexpected error occurred while deleting.');
    }
  };

  const handleValidateAll = async () => {
    if (!providers) return;
    setIsValidatingAll(true);
    const results: Record<string, ValidationResponse> = {};
    for (const p of providers) {
      try {
        const res = await selfTestProvider(p.spec.id);
        results[p.spec.id] = res;
      } catch (err: any) {
        results[p.spec.id] = {
          ok: false,
          latency_ms: 0,
          models: [],
          error: err.message || 'Self-test failed',
          error_code: 'network',
          server_time: null,
        };
      }
    }
    setValidationStatuses(results);
    if (results[selectedId]) {
      setValidationResult(results[selectedId]);
    }
    setIsValidatingAll(false);
  };

  const togglePin = (modelId: string) => {
    const next = pinnedModels.includes(modelId)
      ? pinnedModels.filter(m => m !== modelId)
      : [...pinnedModels, modelId];
    setPinnedModels(next);
    localStorage.setItem('pma_pinned_models', JSON.stringify(next));
  };

  const handleSetDefaultModel = async (modelId: string) => {
    await saveDefaultModel(modelId);
    // Also update LLM Preferences
    if (prefs) {
      await setLLMPreferences({
        ...prefs,
        provider: selectedId as any,
        [`${selectedId}_model`]: modelId,
      });
    }
    await refetchProviders();
    await refetchPrefs();
  };

  // Filter & sort models
  const availableModels = useMemo(() => {
    const list = validationResult?.models || [];
    const filtered = list.filter(m => m.id.toLowerCase().includes(modelSearch.toLowerCase()));
    
    // Sort: pinned first, then alphabetical
    return [...filtered].sort((a, b) => {
      const aPinned = pinnedModels.includes(a.id);
      const bPinned = pinnedModels.includes(b.id);
      if (aPinned && !bPinned) return -1;
      if (!aPinned && bPinned) return 1;
      return a.id.localeCompare(b.id);
    });
  }, [validationResult, modelSearch, pinnedModels]);

  return (
    <div className="flex-1 flex flex-col h-full bg-background overflow-hidden relative">
      <TourOverlay />
      {/* Top Header */}
      <div className="flex items-center justify-between px-8 py-4 border-b border-primary/10 bg-surface backdrop-blur-md relative z-10">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate('/settings')} className="p-2 hover:bg-primary/5 rounded-lg transition-colors">
            <ArrowLeft className="w-5 h-5 text-text-secondary" />
          </button>
          <div>
            <h1 className="font-serif text-xl font-normal tracking-tight">Intelligence Engines</h1>
            <p className="text-xs text-text-secondary">Configure cloud providers and local model backends</p>
          </div>
        </div>

        <button
          onClick={handleValidateAll}
          disabled={isValidatingAll || loading}
          className="glass-button !bg-primary/5 hover:!bg-primary/10 gap-2 text-sm py-2 px-4 border border-primary/20 rounded-xl"
        >
          <RefreshCw className={`w-4 h-4 ${isValidatingAll ? 'animate-spin' : ''}`} />
          Validate All
        </button>
      </div>

      {/* Main Panel */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Side: Providers list */}
        <div id="tour-providers-list" className="w-80 border-r border-primary/10 bg-raised flex flex-col overflow-y-auto p-4 gap-2">
          {routingSettings && routingSettings.fallback_chain && routingSettings.fallback_chain.length > 0 && (
            <div id="tour-fallback-router" className="glass rounded-2xl p-3 border border-primary/10 bg-surface flex flex-col gap-2.5 mb-2 shrink-0">
              <div className="text-[10px] font-bold text-text-secondary uppercase tracking-wider flex items-center justify-between">
                <span>Backup Cascade</span>
                <span className="text-[8px] normal-case text-text-secondary/60">cascading retries</span>
              </div>
              <div className="flex flex-col gap-1.5">
                {routingSettings.fallback_chain.map((providerId, index) => {
                  const p = providers?.find(prov => prov.spec.id === providerId);
                  if (!p) return null;
                  return (
                    <div key={providerId} className="flex items-center justify-between px-2.5 py-1.5 bg-background/50 border border-rule rounded-xl text-xs font-semibold">
                      <span className="truncate">{p.spec.display_name}</span>
                      <div className="flex items-center gap-1">
                        <button
                          disabled={index === 0}
                          onClick={() => handleMoveFallback(index, -1)}
                          className="p-1 hover:bg-raised rounded disabled:opacity-30 cursor-pointer"
                          title="Move Up"
                        >
                          ▲
                        </button>
                        <button
                          disabled={index === routingSettings.fallback_chain.length - 1}
                          onClick={() => handleMoveFallback(index, 1)}
                          className="p-1 hover:bg-raised rounded disabled:opacity-30 cursor-pointer"
                          title="Move Down"
                        >
                          ▼
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {loading && !providers && (
            Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="w-full flex items-center gap-3 p-3.5 rounded-2xl border border-primary/5 bg-surface animate-pulse">
                <div className="w-5 h-5 rounded bg-primary/10" />
                <div className="flex flex-col gap-1.5 flex-1">
                  <div className="h-3.5 bg-primary/10 rounded w-1/2" />
                  <div className="h-2.5 bg-primary/10 rounded w-1/3" />
                </div>
                <div className="w-2.5 h-2.5 rounded-full bg-primary/10" />
              </div>
            ))
          )}

          {providers && providers.length === 0 && (
            <div className="flex flex-col items-center justify-center p-6 text-center h-48 border border-dashed border-primary/20 rounded-2xl">
              <Globe className="w-6 h-6 text-text-secondary mb-2 opacity-50" />
              <p className="text-sm font-semibold text-text-primary">No providers found</p>
              <p className="text-xs text-text-secondary mt-1">Unable to load intelligence engines.</p>
            </div>
          )}

          {providers?.map(p => {
            const isSelected = p.spec.id === selectedId;
            const validation = validationStatuses[p.spec.id];
            
            // Connection dot calculation
            let dotColor = 'bg-zinc-400';
            let dotShape = 'circle';
            let dotLabel = 'Not Connected';

            if (p.is_set || p.spec.kind === 'local') {
              if (validation) {
                if (validation.ok) {
                  dotColor = 'bg-success';
                  dotShape = 'check';
                  dotLabel = 'Connected';
                } else {
                  dotColor = 'bg-warning';
                  dotShape = 'triangle';
                  dotLabel = 'Error';
                }
              } else {
                dotColor = 'bg-accent-blue animate-pulse';
                dotShape = 'pulse';
                dotLabel = 'Configured';
              }
            }

            return (
              <button
                key={p.spec.id}
                onClick={() => setSelectedId(p.spec.id)}
                className={`w-full flex items-center justify-between p-3.5 rounded-2xl border text-left transition-all duration-200 ${
                  isSelected
                    ? 'bg-primary/10 border-primary text-primary shadow-sm'
                    : 'bg-surface border-primary/5 hover:border-primary/20 text-text-primary'
                }`}
              >
                <div className="flex items-center gap-3">
                  <ProviderIcon id={p.spec.id} className="w-5 h-5" />
                  <div>
                    <div className="font-semibold text-sm flex items-center gap-1.5">
                      {p.spec.display_name}
                      {p.stored_in === 'env' && (
                        <span className="text-[10px] font-mono font-bold bg-primary/10 text-primary px-1.5 py-0.5 rounded">
                          ENV
                        </span>
                      )}
                    </div>
                    <span className="text-[11px] text-text-secondary capitalize">{p.spec.kind}</span>
                  </div>
                </div>

                {/* Connection indicator */}
                <div className="flex flex-col items-end">
                  <div className="flex items-center gap-1">
                    <div
                      title={dotLabel}
                      className={`w-2.5 h-2.5 rounded-full ${dotColor} ${
                        dotShape === 'triangle' ? 'clip-triangle' : ''
                      }`}
                    />
                  </div>
                  <ProviderSparkline providerId={p.spec.id} />
                </div>
              </button>
            );
          })}
        </div>

        {/* Right Side: Selected provider configurations */}
        <div className="flex-1 overflow-y-auto p-8 flex flex-col gap-6">
          <ProviderRecipes onRecipeApplied={() => { refetchProviders(); refetchPrefs(); refetchSettings(); }} />
          
          {selectedProvider && (
            <div className="max-w-2xl flex flex-col gap-6">
              {/* Header */}
              <div className="flex items-start gap-4">
                <div className="w-12 h-12 bg-primary/10 rounded-2xl flex items-center justify-center">
                  <ProviderIcon id={selectedProvider.spec.id} className="w-7 h-7" />
                </div>
                <div>
                  <h2 className="text-xl font-bold">{selectedProvider.spec.display_name}</h2>
                  <p className="text-sm text-text-secondary mt-1">
                    Connect and configure default parameters for {selectedProvider.spec.display_name}.
                  </p>
                </div>
              </div>

              {/* URL and API Key Form */}
              <div id="tour-connection-details" className="glass rounded-3xl p-6 border border-primary/10 bg-surface flex flex-col gap-5">
                {/* Base URL (if editable or present) */}
                {(selectedProvider.spec.base_url_editable || selectedProvider.spec.default_base_url) && (
                  <div className="flex flex-col gap-1.5">
                    <label className="text-xs font-semibold text-text-secondary uppercase tracking-wider flex items-center gap-1.5">
                      Base Endpoint URL
                      {!selectedProvider.spec.base_url_editable && <Lock className="w-3.5 h-3.5 text-text-secondary/50" />}
                    </label>
                    <div className="relative flex items-center">
                      <Globe className="absolute left-3.5 w-4 h-4 text-text-secondary/50" />
                      <input
                        type="text"
                        disabled={!selectedProvider.spec.base_url_editable || selectedProvider.stored_in === 'env'}
                        value={baseUrl}
                        onChange={e => setBaseUrlInput(e.target.value)}
                        className="w-full bg-background/50 border border-primary/20 rounded-xl pl-10 pr-4 py-2.5 text-sm outline-none focus:border-primary/50 disabled:opacity-60 transition-colors"
                      />
                    </div>
                  </div>
                )}

                {(selectedProvider.spec.kind === 'cloud' || selectedProvider.spec.kind === 'aggregator') && (
                  <div id="cloud-consent" className="p-3 bg-amber-500/10 border border-amber-500/20 rounded-xl text-xs text-amber-800 flex flex-col gap-2.5 scroll-mt-6">
                    <div className="flex items-start gap-2">
                      <Zap className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
                      <span>
                        <strong>Privacy Notice:</strong>{' '}
                        {routingSettings?.cloud_privacy_notice ||
                          'Free-tier cloud dispatches may use data inputs for model training/improvement per provider terms and are restricted for EEA, Switzerland, and UK users.'}
                      </span>
                    </div>
                    <label className="flex items-center gap-2 pl-6 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={!!routingSettings?.cloud_privacy_consent}
                        disabled={updateRoutingSettings.isPending}
                        onChange={e => {
                          updateRoutingSettings.mutate({ cloud_privacy_consent: e.target.checked });
                        }}
                        className="rounded border-amber-500/40"
                      />
                      <span className="font-medium">I understand and consent to cloud data processing</span>
                    </label>
                  </div>
                )}

                {/* API Key */}
                {selectedProvider.spec.auth !== 'none' && (
                  <div className="flex flex-col gap-1.5">
                    <label className="text-xs font-semibold text-text-secondary uppercase tracking-wider flex items-center justify-between">
                      API Access Key
                      {selectedProvider.spec.api_key_docs_url && (
                        <a
                          href={selectedProvider.spec.api_key_docs_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-[11px] font-medium text-primary hover:underline"
                        >
                          Get Key
                        </a>
                      )}
                    </label>
                    <div className="relative flex items-center">
                      <Key className="absolute left-3.5 w-4 h-4 text-text-secondary/50" />
                      <input
                        type="password"
                        disabled={selectedProvider.stored_in === 'env'}
                        placeholder="••••••••••••••••••••"
                        value={apiKey}
                        onChange={e => setApiKeyInput(e.target.value)}
                        className={`w-full bg-background/50 border rounded-xl pl-10 pr-4 py-2.5 text-sm outline-none transition-colors ${
                          formatCheck.isValid ? 'border-primary/20 focus:border-primary/50' : 'border-danger focus:border-danger'
                        }`}
                      />
                    </div>
                    {formatCheck.helperText && (
                      <span className="text-xs text-danger font-medium mt-1">{formatCheck.helperText}</span>
                    )}
                  </div>
                )}

                {/* Action buttons */}
                <div className="flex items-center justify-between mt-2 pt-4 border-t border-primary/5">
                  <div>
                    {selectedProvider.stored_in === 'env' && (
                      <span className="text-xs text-text-secondary font-medium flex items-center gap-1">
                        <Lock className="w-3.5 h-3.5" /> Managed via environment variables (.env)
                      </span>
                    )}
                  </div>

                  <div className="flex items-center gap-3">
                    {selectedProvider.is_set && selectedProvider.stored_in !== 'env' && (
                      <button
                        onClick={handleDelete}
                        disabled={isSaving}
                        className="px-4 py-2 text-xs font-semibold text-danger hover:bg-danger/5 rounded-xl transition-all disabled:opacity-50"
                      >
                        Remove Connection
                      </button>
                    )}

                    <button
                      onClick={handleValidate}
                      disabled={isValidating || selectedProvider.stored_in === 'env' || !formatCheck.isValid}
                      className="glass-button !bg-primary/5 border border-primary/20 text-primary hover:!bg-primary/10 px-4 py-2 rounded-xl text-xs font-semibold flex items-center gap-1.5 disabled:opacity-50"
                    >
                      {isValidating ? (
                        <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Zap className="w-3.5 h-3.5" />
                      )}
                      Test & Validate
                    </button>

                    <button
                      onClick={handleSave}
                      disabled={isSaving || selectedProvider.stored_in === 'env' || !formatCheck.isValid}
                      className="glass-button !bg-plate !text-on-plate hover:opacity-90 px-5 py-2 rounded-xl text-xs font-semibold disabled:opacity-50 flex items-center gap-1.5"
                    >
                      {isSaving && <RefreshCw className="w-3.5 h-3.5 animate-spin" />}
                      Save Configuration
                    </button>
                  </div>
                </div>
                {saveError && (
                  <div className="text-xs font-semibold text-danger mt-1">
                    {saveError}
                  </div>
                )}
              </div>

              {/* Connection validation metrics */}
              {validationResult && (
                <div className={`glass rounded-3xl p-5 border flex flex-col gap-3 transition-all duration-300 ${
                  validationResult.ok ? 'border-success/30 bg-success/5' : 'border-danger/30 bg-danger/5'
                }`}>
                  <div className="flex items-center gap-2">
                    {validationResult.ok ? (
                      <CheckCircle2 className="w-5 h-5 text-success" />
                    ) : (
                      <XCircle className="w-5 h-5 text-danger" />
                    )}
                    <span className="font-bold text-sm">
                      {validationResult.ok ? 'Connection Verified' : 'Connection Failed'}
                    </span>
                  </div>

                  {validationResult.ok ? (
                    <div className="grid grid-cols-2 gap-4 text-xs font-medium text-text-secondary mt-1">
                      <div>Latency: <strong className="text-text-primary">{validationResult.latency_ms}ms</strong></div>
                      {validationResult.server_time && (
                        <div>Server Date: <strong className="text-text-primary">{validationResult.server_time}</strong></div>
                      )}
                    </div>
                  ) : (
                    <div className="flex flex-col gap-2 mt-1">
                      <p className="text-xs text-danger font-semibold">{validationResult.error}</p>
                      {validationResult.error_code && (
                        <span className="text-[10px] uppercase font-mono font-bold bg-danger/10 text-danger px-2 py-0.5 rounded w-max">
                          Error Code: {validationResult.error_code}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Models List Dropdown / Card */}
              {validationResult?.ok && (
                <div id="tour-model-selection" className="glass rounded-3xl p-6 border border-primary/10 bg-surface flex flex-col gap-4">
                  <div className="flex items-center justify-between">
                    <h3 className="font-bold text-sm uppercase tracking-wider text-text-secondary">Default Target Model</h3>
                    <span className="text-xs font-medium text-primary">
                      {validationResult.models.length} Models Found
                    </span>
                  </div>

                  {/* Search Bar */}
                  <div className="relative flex items-center">
                    <Search className="absolute left-3.5 w-4 h-4 text-text-secondary/50" />
                    <input
                      type="text"
                      placeholder="Filter models by name..."
                      value={modelSearch}
                      onChange={e => setModelSearch(e.target.value)}
                      className="w-full bg-background/50 border border-primary/20 rounded-xl pl-10 pr-4 py-2.5 text-xs outline-none focus:border-primary/50 transition-colors"
                    />
                  </div>

                  {/* Models list */}
                  <div className="flex flex-col gap-2 max-h-64 overflow-y-auto pr-1">
                    {availableModels.map(model => {
                      const isDefault = selectedProvider.default_model === model.id;
                      const isPinned = pinnedModels.includes(model.id);

                      return (
                        <div
                          key={model.id}
                          className={`flex items-center justify-between p-3 rounded-xl border transition-all ${
                            isDefault
                              ? 'bg-primary/10 border-primary/30 text-primary'
                              : 'bg-surface border-primary/5 hover:border-primary/10'
                          }`}
                        >
                          <div className="flex items-center gap-3">
                            <button
                              onClick={() => togglePin(model.id)}
                              className="text-text-secondary hover:text-amber-400 transition-colors"
                            >
                              <Star className={`w-4 h-4 ${isPinned ? 'fill-amber-400 text-amber-400' : ''}`} />
                            </button>

                            <div>
                              <div className="text-xs font-semibold font-mono tracking-tight">{model.id}</div>
                              <div className="flex items-center gap-2 mt-1.5">
                                <span className="text-[10px] font-semibold bg-primary/10 text-primary px-1.5 py-0.5 rounded">
                                  {model.context_length.toLocaleString()} ctx
                                </span>
                                {model.pricing_hint > 0 && (
                                  <span className="text-[10px] font-semibold bg-surface text-success px-1.5 py-0.5 rounded">
                                    ${model.pricing_hint.toFixed(2)}/1M tok
                                  </span>
                                )}
                              </div>
                            </div>
                          </div>

                          <button
                            onClick={() => handleSetDefaultModel(model.id)}
                            disabled={isDefault}
                            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                              isDefault
                                ? 'bg-plate text-on-plate pointer-events-none'
                                : 'bg-primary/5 text-primary hover:bg-primary/10'
                            }`}
                          >
                            {isDefault ? 'Default' : 'Select'}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
