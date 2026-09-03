import { useState, useEffect, useMemo, useId } from 'react';
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
import { Badge, Button, EmptyState, Panel, Skeleton } from '../components/ui';
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
  ChevronDown,
  ChevronUp,
  RefreshCw,
  Search,
  Star,
  Zap,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { CACHE_KEYS } from '../cacheKeys'
import { useOptimisticMutation } from '../useOptimisticMutation'
import { formatCurrency, formatDateTime } from '../utils/format'

export function ProvidersPage() {
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
  const baseUrlId = useId();
  const apiKeyId = useId();
  const apiKeyHelpId = useId();
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
      <div className="flex items-center justify-between px-8 py-4 border-b border-rule bg-surface relative z-10">
        <div className="flex items-center gap-3">
          {/* Icon-only, so it needs a name: it had none. */}
          <Link
            to="/settings"
            aria-label="Back to Settings"
            className="p-2 hover:bg-raised rounded-sm transition-colors"
          >
            <ArrowLeft className="w-5 h-5 text-text-secondary" aria-hidden />
          </Link>
          <div>
            <h1 className="font-serif text-xl font-normal tracking-tight">Model providers</h1>
            <p className="text-xs text-text-secondary">Configure cloud providers and local model backends</p>
          </div>
        </div>

        <Button
          variant="secondary"
          onClick={handleValidateAll}
          disabled={loading}
          loading={isValidatingAll}
          icon={<RefreshCw className="w-4 h-4" />}
        >
          Validate All
        </Button>
      </div>

      {/* Main Panel */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Side: Providers list */}
        <div id="tour-providers-list" className="w-80 border-r border-rule bg-raised flex flex-col overflow-y-auto p-4 gap-2">
          {routingSettings && routingSettings.fallback_chain && routingSettings.fallback_chain.length > 0 && (
            <Panel id="tour-fallback-router" className="p-3 flex flex-col gap-2.5 mb-2 shrink-0">
              {/* Was `text-[8px] text-text-secondary/60`: below even the mono
                  allowance, and the alpha dropped it under its measured ratio. */}
              <div className="font-mono text-[10px] tracking-[0.16em] uppercase text-text-tertiary flex items-center justify-between">
                <span>Backup cascade</span>
                <span className="tracking-normal normal-case">cascading retries</span>
              </div>
              <div className="flex flex-col gap-1.5">
                {routingSettings.fallback_chain.map((providerId, index) => {
                  const p = providers?.find(prov => prov.spec.id === providerId);
                  if (!p) return null;
                  return (
                    <div key={providerId} className="flex items-center justify-between px-2.5 py-1.5 bg-background border border-rule rounded-sm text-xs font-medium">
                      <span className="truncate">{p.spec.display_name}</span>
                      <div className="flex items-center gap-1">
                        <button
                          disabled={index === 0}
                          onClick={() => handleMoveFallback(index, -1)}
                          className="p-1 hover:bg-raised rounded-xs disabled:text-text-tertiary disabled:cursor-not-allowed cursor-pointer"
                          title="Move Up"
                          aria-label={`Move ${p.spec.display_name} earlier in the cascade`}
                        >
                          <ChevronUp className="w-3.5 h-3.5" />
                        </button>
                        <button
                          disabled={index === routingSettings.fallback_chain.length - 1}
                          onClick={() => handleMoveFallback(index, 1)}
                          className="p-1 hover:bg-raised rounded-xs disabled:text-text-tertiary disabled:cursor-not-allowed cursor-pointer"
                          title="Move Down"
                          aria-label={`Move ${p.spec.display_name} later in the cascade`}
                        >
                          <ChevronDown className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </Panel>
          )}

          {loading && !providers && (
            Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="w-full flex items-center gap-3 p-3.5 rounded-md border border-rule bg-surface">
                <Skeleton className="w-5 h-5" />
                <div className="flex flex-col gap-1.5 flex-1">
                  <Skeleton className="h-3.5 w-1/2" />
                  <Skeleton className="h-2.5 w-1/3" />
                </div>
                <Skeleton className="w-2.5 h-2.5" />
              </div>
            ))
          )}

          {providers && providers.length === 0 && (
            <EmptyState
              title="No providers found"
              body="The provider registry came back empty. Check that the backend is running, then try again."
            />
          )}

          {providers?.map(p => {
            const isSelected = p.spec.id === selectedId;
            const validation = validationStatuses[p.spec.id];
            
            // Connection dot calculation
            let dotColor = 'bg-text-tertiary';
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
                // No infinite pulse: "configured but unvalidated" is a
                // resting state, not an event, and a looping animation on
                // every row was motion with nothing to communicate.
                dotColor = 'bg-info';
                dotShape = 'pulse';
                dotLabel = 'Configured';
              }
            }

            return (
              <button
                key={p.spec.id}
                type="button"
                onClick={() => setSelectedId(p.spec.id)}
                aria-pressed={isSelected}
                className={`w-full flex items-center justify-between p-3.5 rounded-md border text-left transition-colors duration-150 ${
                  isSelected
                    ? 'bg-surface border-edge text-text-primary shadow-[inset_3px_0_0_var(--color-plate)]'
                    : 'bg-surface border-rule hover:border-edge text-text-primary'
                }`}
              >
                <div className="flex items-center gap-3">
                  <ProviderIcon
                    id={p.spec.id}
                    className={`w-5 h-5 ${isSelected ? 'text-primary' : 'text-text-secondary'}`}
                  />
                  <div>
                    <div className="font-medium text-sm flex items-center gap-2">
                      {p.spec.display_name}
                      {p.stored_in === 'env' && <Badge mono tone="accent">ENV</Badge>}
                    </div>
                    <span className="text-[11px] text-text-secondary capitalize">{p.spec.kind}</span>
                  </div>
                </div>

                {/* Connection indicator */}
                <div className="flex flex-col items-end">
                  <div className="flex items-center gap-1">
                    <div
                      title={dotLabel}
                      aria-hidden
                      className={`w-2.5 h-2.5 rounded-full ${dotColor} ${
                        dotShape === 'triangle' ? 'clip-triangle' : ''
                      }`}
                    />
                    {/* The dot is the only carrier of connection state, and a
                        `title` on a non-interactive div is announced by
                        essentially nothing. */}
                    <span className="sr-only">{dotLabel}</span>
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
                <div className="w-12 h-12 bg-raised border border-rule rounded-md flex items-center justify-center text-primary shrink-0">
                  <ProviderIcon id={selectedProvider.spec.id} className="w-6 h-6" />
                </div>
                <div>
                  <h2 className="font-serif text-xl font-normal">{selectedProvider.spec.display_name}</h2>
                  <p className="text-sm text-text-secondary mt-1">
                    Connect and configure default parameters for {selectedProvider.spec.display_name}.
                  </p>
                </div>
              </div>

              {/* URL and API Key Form */}
              <Panel id="tour-connection-details" className="p-6 flex flex-col gap-5">
                {/* Base URL (if editable or present) */}
                {(selectedProvider.spec.base_url_editable || selectedProvider.spec.default_base_url) && (
                  <div className="flex flex-col gap-1.5">
                    <label htmlFor={baseUrlId} className="font-mono text-[10px] tracking-[0.16em] uppercase text-text-tertiary flex items-center gap-1.5">
                      Base Endpoint URL
                      {!selectedProvider.spec.base_url_editable && <Lock className="w-3.5 h-3.5 text-text-tertiary" aria-hidden />}
                    </label>
                    <div className="relative flex items-center">
                      <Globe className="absolute left-3.5 w-4 h-4 text-text-tertiary z-10" aria-hidden />
                      <input
                        id={baseUrlId}
                        type="url"
                        inputMode="url"
                        name="base-url"
                        autoComplete="off"
                        spellCheck={false}
                        disabled={!selectedProvider.spec.base_url_editable || selectedProvider.stored_in === 'env'}
                        value={baseUrl}
                        onChange={e => setBaseUrlInput(e.target.value)}
                        className="glass-input pl-10 pr-4 py-2.5 text-sm rounded-sm"
                      />
                    </div>
                  </div>
                )}

                {(selectedProvider.spec.kind === 'cloud' || selectedProvider.spec.kind === 'aggregator') && (
                  <div id="cloud-consent" className="p-3 bg-surface border border-warning rounded-md text-xs text-text-primary flex flex-col gap-2.5 scroll-mt-6">
                    <div className="flex items-start gap-2">
                      <Zap className="w-4 h-4 text-warning shrink-0 mt-0.5" />
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
                        className="rounded-xs border-edge"
                      />
                      <span className="font-medium">I understand and consent to cloud data processing</span>
                    </label>
                  </div>
                )}

                {/* API Key */}
                {selectedProvider.spec.auth !== 'none' && (
                  <div className="flex flex-col gap-1.5">
                    {/* The "Get Key" link used to sit INSIDE the label, which
                        nests one interactive element in another: clicking label
                        text is meant to focus its input, and a link there does
                        something else entirely. */}
                    <div className="flex items-center justify-between">
                      <label htmlFor={apiKeyId} className="font-mono text-[10px] tracking-[0.16em] uppercase text-text-tertiary">
                        API Access Key
                      </label>
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
                    </div>
                    <div className="relative flex items-center">
                      <Key className="absolute left-3.5 w-4 h-4 text-text-tertiary z-10" aria-hidden />
                      <input
                        id={apiKeyId}
                        type="password"
                        name={`${selectedProvider.spec.id}-api-key`}
                        autoComplete="off"
                        spellCheck={false}
                        disabled={selectedProvider.stored_in === 'env'}
                        placeholder="••••••••••••••••••••"
                        value={apiKey}
                        onChange={e => setApiKeyInput(e.target.value)}
                        aria-invalid={!formatCheck.isValid}
                        aria-describedby={formatCheck.helperText ? apiKeyHelpId : undefined}
                        className={`glass-input pl-10 pr-4 py-2.5 text-sm rounded-sm ${
                          formatCheck.isValid ? '' : 'border-error'
                        }`}
                      />
                    </div>
                    <span id={apiKeyHelpId} className="text-xs text-error font-medium mt-1 empty:hidden" aria-live="polite">
                      {formatCheck.helperText}
                    </span>
                  </div>
                )}

                {/* Action buttons */}
                <div className="flex items-center justify-between mt-2 pt-4 border-t border-rule">
                  <div>
                    {selectedProvider.stored_in === 'env' && (
                      <span className="text-xs text-text-secondary font-medium flex items-center gap-1">
                        <Lock className="w-3.5 h-3.5" /> Managed via environment variables (.env)
                      </span>
                    )}
                  </div>

                  <div className="flex items-center gap-3">
                    {selectedProvider.is_set && selectedProvider.stored_in !== 'env' && (
                      <Button variant="danger" size="sm" onClick={handleDelete} disabled={isSaving}>
                        Remove Connection
                      </Button>
                    )}

                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={handleValidate}
                      disabled={selectedProvider.stored_in === 'env' || !formatCheck.isValid}
                      loading={isValidating}
                      icon={<Zap className="w-3.5 h-3.5" />}
                    >
                      Test &amp; Validate
                    </Button>

                    <Button
                      variant="plate"
                      size="sm"
                      onClick={handleSave}
                      disabled={selectedProvider.stored_in === 'env' || !formatCheck.isValid}
                      loading={isSaving}
                    >
                      Save Configuration
                    </Button>
                  </div>
                </div>
                <span className="sr-only" aria-live="polite">{saveError ?? ''}</span>
                {saveError && (
                  <div className="text-xs font-medium text-error mt-1">
                    {saveError}
                  </div>
                )}
              </Panel>

              {/* Connection validation metrics */}
              <span className="sr-only" aria-live="polite">
                {validationResult ? (validationResult.ok ? 'Connection verified' : `Connection failed: ${validationResult.error ?? ''}`) : ''}
              </span>
              {validationResult && (
                <div className={`bg-surface rounded-xl p-5 border flex flex-col gap-3 transition-colors duration-200 ${
                  validationResult.ok ? 'border-success' : 'border-error'
                }`}>
                  <div className="flex items-center gap-2">
                    {validationResult.ok ? (
                      <CheckCircle2 className="w-5 h-5 text-success" />
                    ) : (
                      <XCircle className="w-5 h-5 text-error" />
                    )}
                    <span className="font-medium text-sm">
                      {validationResult.ok ? 'Connection Verified' : 'Connection Failed'}
                    </span>
                  </div>

                  {validationResult.ok ? (
                    <div className="grid grid-cols-2 gap-4 text-xs font-medium text-text-secondary mt-1">
                      <div>Latency: <strong className="text-text-primary">{validationResult.latency_ms}ms</strong></div>
                      {validationResult.server_time && (
                        <div>Server Date: <strong className="text-text-primary">{formatDateTime(validationResult.server_time)}</strong></div>
                      )}
                    </div>
                  ) : (
                    <div className="flex flex-col gap-2 mt-1">
                      <p className="text-xs text-error font-medium m-0">{validationResult.error}</p>
                      {validationResult.error_code && (
                        <Badge mono tone="error" className="w-max">
                          {validationResult.error_code}
                        </Badge>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Models List Dropdown / Card */}
              {validationResult?.ok && (
                <Panel id="tour-model-selection" className="p-6 flex flex-col gap-4">
                  <div className="flex items-center justify-between border-b border-rule pb-2">
                    <h3 className="font-serif text-base font-medium text-text-primary m-0">Default target model</h3>
                    <span className="font-mono text-[11px] text-text-tertiary tabular-nums">
                      {validationResult.models.length} found
                    </span>
                  </div>

                  {/* Search Bar */}
                  <div className="relative flex items-center">
                    <Search className="absolute left-3.5 w-4 h-4 text-text-tertiary z-10" aria-hidden />
                    <input
                      type="search"
                      spellCheck={false}
                      autoComplete="off"
                      aria-label="Filter models by name"
                      placeholder="e.g. llama3…"
                      value={modelSearch}
                      onChange={e => setModelSearch(e.target.value)}
                      className="glass-input pl-10 pr-4 py-2.5 text-xs rounded-sm"
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
                          // A provider can return 300+ models into this
                          // scroller. `content-visibility` lets the browser skip
                          // layout and paint for offscreen rows without pulling
                          // in a virtualization dependency.
                          style={{ contentVisibility: 'auto', containIntrinsicSize: 'auto 72px' }}
                          className={`flex items-center justify-between p-3 rounded-md border transition-colors ${
                            isDefault
                              ? 'bg-surface border-edge shadow-[inset_3px_0_0_var(--color-plate)]'
                              : 'bg-surface border-rule hover:border-edge'
                          }`}
                        >
                          <div className="flex items-center gap-3">
                            <button
                              onClick={() => togglePin(model.id)}
                              aria-pressed={isPinned}
                              aria-label={`${isPinned ? 'Unpin' : 'Pin'} ${model.id}`}
                              className="text-text-tertiary hover:text-primary transition-colors"
                            >
                              <Star className={`w-4 h-4 ${isPinned ? 'fill-current text-primary' : ''}`} />
                            </button>

                            <div>
                              <div className="text-xs font-mono tracking-tight">{model.id}</div>
                              <div className="flex items-center gap-2 mt-1.5">
                                <Badge mono tone="accent">
                                  {model.context_length.toLocaleString()} ctx
                                </Badge>
                                {model.pricing_hint > 0 && (
                                  <Badge mono tone="success">
                                    {formatCurrency(model.pricing_hint)}/1M tok
                                  </Badge>
                                )}
                              </div>
                            </div>
                          </div>

                          <Button
                            variant={isDefault ? 'plate' : 'quiet'}
                            size="sm"
                            onClick={() => handleSetDefaultModel(model.id)}
                            disabled={isDefault}
                          >
                            {isDefault ? 'Default' : 'Select'}
                          </Button>
                        </div>
                      );
                    })}
                  </div>
                </Panel>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
