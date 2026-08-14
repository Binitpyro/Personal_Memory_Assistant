import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Settings, CheckCircle2, AlertCircle, Cpu, HardDrive, RefreshCcw, Trash2, AlertTriangle, DatabaseZap, Play, Download, Loader2, ScanText } from 'lucide-react'
import { useApi, invalidateCache } from '../useApi'
import {
  getLocalModels,
  getSystemInfo,
  getLLMPreferences,
  setLLMPreferences,
  setProviderDefaultModel,
  clearIndex,
  getDriveInfo,
  purgeHostCache,
  getProviders,
  getProviderLaunchStatus,
  launchProvider,
  getOcrStatus,
  getOcrTiers,
  getVlmModels,
  getVlmSelection,
  selectVlmModel,
  getOcrInstallState,
  getOcrQueue,
  installOcrTier,
  uninstallOcrTier,
  cancelOcrInstall,
  resumeOcr,
  setOcrEnabled,
  retryOcr,
  clearOcrCache,
  type ProviderStatus,
  type LLMPreferences,
  type LocalModelDetection,
  type SystemInfo,
  type DriveInfo,
  type OcrInstallState,
  type OcrQueueItem
} from '../api'

// STATIC_FALLBACK_MODELS removed in favor of dynamic backend discovery and persistent model heaps.

// ── Sub-components for lower cognitive complexity ───────────────────


/**
 * Start button for a provider PMA can launch itself (Ollama / LM Studio).
 * Falls back to the plain "make sure it's running" hint whenever the backend can't
 * tell us anything useful, so the card is never empty.
 */
function StartLocalProviderButton({ providerId, displayName, offlineHint, onStarted }: Readonly<{
  providerId: string
  displayName: string
  offlineHint: string
  onStarted: () => void
}>) {
  const cacheKey = `launch-status-${providerId}`
  const { data: status, refetch: refetchStatus } = useApi(
    () => getProviderLaunchStatus(providerId),
    { cacheKey }
  )
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleStart = async () => {
    setStarting(true)
    setError(null)
    try {
      const res = await launchProvider(providerId)
      if (res.ok) {
        invalidateCache('local-models')
        invalidateCache('providers-list')
        onStarted()
      } else {
        setError(res.message)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : `Could not start ${displayName}.`)
    } finally {
      setStarting(false)
      invalidateCache(cacheKey)
      refetchStatus()
    }
  }

  if (!status?.supported) {
    return <p className="text-sm text-text-secondary">{offlineHint}</p>
  }

  if (!status.installed) {
    return (
      <div className="flex flex-col gap-1.5">
        <p className="text-sm text-text-secondary">{displayName} isn't installed on this machine.</p>
        {status.install_url && (
          <a
            href={status.install_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-xs font-semibold text-primary hover:underline"
          >
            <Download className="w-3.5 h-3.5" /> Install {displayName}
          </a>
        )}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <button
        onClick={handleStart}
        disabled={starting}
        className="glass-button !bg-primary/10 border border-primary/20 text-primary hover:!bg-primary/20 px-3 py-2 rounded-xl text-xs font-semibold flex items-center justify-center gap-1.5 disabled:opacity-60 w-fit"
      >
        {starting ? (
          <RefreshCcw className="w-3.5 h-3.5 animate-spin" />
        ) : (
          <Play className="w-3.5 h-3.5" />
        )}
        {starting ? 'Starting…' : `Start ${displayName}`}
      </button>
      {starting && (
        <span className="text-xs text-text-secondary">This can take up to a minute.</span>
      )}
      {error && !starting && (
        <span className="text-xs text-danger font-medium">{error}</span>
      )}
    </div>
  )
}

function LocalProviderCard({ providerId, displayName, emoji, detection, emptyHint, offlineHint, onStarted }: Readonly<{
  providerId: string
  displayName: string
  emoji: string
  detection?: { detected: boolean; models: string[] }
  emptyHint: string
  offlineHint: string
  onStarted: () => void
}>) {
  const detected = detection?.detected ?? false

  return (
    <div className="p-4 rounded-xl border border-primary/5 bg-white/50 backdrop-blur-md">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-text-primary flex items-center gap-2">
          <span className="text-xl">{emoji}</span> {displayName}
        </h3>
        {detected ? (
          <span className="inline-flex items-center justify-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-success/15 text-success">
            <span className="w-1.5 h-1.5 rounded-full bg-success"></span>
            Detected
          </span>
        ) : (
          <span className="inline-flex items-center justify-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-text-secondary/15 text-text-secondary">
            Offline
          </span>
        )}
      </div>
      {detected ? (
        <div className="flex flex-wrap gap-2 mt-2">
          {detection?.models.map(m => (
            <span key={m} className="px-2 py-1 text-xs font-medium bg-primary/10 text-primary rounded-md border border-primary/20">
              {m}
            </span>
          ))}
          {detection?.models.length === 0 && (
            <span className="text-sm text-text-secondary">{emptyHint}</span>
          )}
        </div>
      ) : (
        <StartLocalProviderButton
          providerId={providerId}
          displayName={displayName}
          offlineHint={offlineHint}
          onStarted={onStarted}
        />
      )}
    </div>
  )
}

function LocalModelsSection({ localModels, onStarted }: Readonly<{
  localModels?: LocalModelDetection
  onStarted: () => void
}>) {
  return (
    <div className="glass p-6 rounded-2xl border border-primary/10">
      <div className="flex items-start gap-4 mb-6">
        <div className="p-3 bg-primary/10 rounded-xl">
          <Cpu className="w-6 h-6 text-primary" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-text-primary">Local LLM Auto-Detection</h2>
          <p className="text-sm text-text-secondary mt-1 max-w-lg">
            Offline models running on your machine are detected automatically.
            The backend will cascade to these if Google Gemini is unavailable or not configured.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <LocalProviderCard
          providerId="ollama"
          displayName="Ollama"
          emoji="🦙"
          detection={localModels?.ollama}
          emptyHint="Running, but no models installed."
          offlineHint="Ensure Ollama is running on localhost:11434."
          onStarted={onStarted}
        />
        <LocalProviderCard
          providerId="lm_studio"
          displayName="LM Studio"
          emoji="🖥️"
          detection={localModels?.lm_studio}
          emptyHint="Running, but no model loaded."
          offlineHint="Ensure LM Studio's Local Server is running on localhost:1234."
          onStarted={onStarted}
        />
      </div>
    </div>
  )
}

function LLMPreferencesSection({
  onSave,
  saving
}: Readonly<{
  onSave: (prefs: LLMPreferences) => void
  saving: boolean
}>) {
  const navigate = useNavigate()
  const { data: llmPrefs } = useApi(getLLMPreferences, { cacheKey: 'llm-prefs' })
  const { data: providers } = useApi(getProviders, { cacheKey: 'providers-list' })
  
  const [provider, setProvider] = useState<string>('auto')
  const [selectedModels, setSelectedModels] = useState<Record<string, string>>({})

  useEffect(() => {
    if (llmPrefs) {
      setProvider(llmPrefs.provider || 'auto')
      const initialModels: Record<string, string> = {}
      if (llmPrefs.gemini_model) initialModels.gemini = llmPrefs.gemini_model
      if (llmPrefs.ollama_model) initialModels.ollama = llmPrefs.ollama_model
      if (llmPrefs.lm_studio_model) initialModels.lm_studio = llmPrefs.lm_studio_model
      setSelectedModels(initialModels)
    }
  }, [llmPrefs])

  const activeProviders = (providers || []).filter(
    (p: ProviderStatus) => p.is_set || p.spec.id === 'ollama' || p.spec.id === 'lm_studio'
  )
  
  const activeProviderSpec = activeProviders.find(p => p.spec.id === provider)
  
  const availableModels = activeProviderSpec 
    ? (activeProviderSpec.last_validation?.models?.map(m => m.id) || [])
    : []

  const currentModel = provider !== 'auto' ? (selectedModels[provider] || '') : ''

  const handleProviderChange = (newProvider: string) => {
    setProvider(newProvider)
    if (newProvider !== 'auto') {
      const pSpec = activeProviders.find(p => p.spec.id === newProvider)
      const pModels = pSpec?.last_validation?.models?.map(m => m.id) || []
      if (pModels.length > 0 && !selectedModels[newProvider]) {
        setSelectedModels(prev => ({ ...prev, [newProvider]: pModels[0] }))
      }
    }
  }

  const handleModelChange = (newModel: string) => {
    if (provider !== 'auto') {
      setSelectedModels(prev => ({ ...prev, [provider]: newModel }))
    }
  }

  const submitSave = () => {
    if (!llmPrefs) return
    const newPrefs: LLMPreferences = { ...llmPrefs, provider }
    Object.entries(selectedModels).forEach(([pId, mId]) => {
      newPrefs[`${pId}_model`] = mId
    })
    onSave(newPrefs)
  }

  return (
    <div className="glass p-6 rounded-2xl border border-primary/10">
      <div className="flex items-start gap-4 mb-6">
        <div className="p-3 bg-primary/10 rounded-xl">
          <Cpu className="w-6 h-6 text-primary" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-text-primary">Model Selection</h2>
          <p className="text-sm text-text-secondary mt-1">
            Choose your preferred intelligence provider and model. For detailed API key configurations, use the advanced view.
          </p>
        </div>
      </div>

      <div className="flex flex-col md:flex-row gap-4 items-end">
        <label className="text-sm text-text-secondary flex flex-col gap-1 w-full md:w-64">
          Provider
          <select 
            value={provider} 
            onChange={(e) => handleProviderChange(e.target.value)} 
            className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-text-primary"
          >
            <option value="auto">Auto (recommended)</option>
            {activeProviders.map(p => (
              <option key={p.spec.id} value={p.spec.id}>{p.spec.display_name}</option>
            ))}
          </select>
        </label>

        {provider !== 'auto' && availableModels.length > 0 && (
          <label className="text-sm text-text-secondary flex flex-col gap-1 w-full md:w-64 animate-fade-in">
            Model
            <select 
              value={currentModel} 
              onChange={(e) => handleModelChange(e.target.value)} 
              className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-text-primary"
            >
              <option value="" disabled>Select a model...</option>
              {availableModels.map(mId => (
                <option key={mId} value={mId}>{mId}</option>
              ))}
            </select>
          </label>
        )}
      </div>

      <div className="mt-6 flex items-center gap-3">
        <button
          onClick={submitSave}
          disabled={saving || !llmPrefs}
          className="glass-button !bg-primary !text-white hover:!bg-primary-h !py-2 !px-4"
        >
          {saving ? 'Saving...' : 'Save Preference'}
        </button>
        <button
          onClick={() => navigate('/settings/providers')}
          className="glass-button !bg-primary/10 border border-primary/20 text-primary hover:!bg-primary/20 !py-2 !px-4"
        >
          Manage Providers (Advanced)
        </button>
      </div>
    </div>
  )
}

/** Plain-language copy per tier. "DirectML" and "execution provider" are not
 *  user vocabulary; size and speed are what the choice actually turns on. */
const TIER_COPY: Record<string, { short: string; title: string; size: string; blurb: string }> = {
  cpu: {
    short: 'Standard',
    title: 'Standard — runs on the processor',
    size: '~230 MB',
    blurb:
      'About 230 MB, models included. Around one page per second on a typical CPU — ' +
      'the first page of a run is slower while the model warms up.',
  },
  gpu: {
    short: 'High accuracy',
    title: 'High accuracy — uses your graphics card',
    size: '~430 MB',
    blurb:
      'Downloads larger, more accurate models (about 194 MB on top of the engine) and ' +
      'runs them on your graphics card. Windows only. Needs roughly 6 GB of free graphics ' +
      'memory — measured at 5.2 GB in use — so a 4 GB card is not enough and will fall ' +
      'back to the processor, which is slower than Standard. The card actually in use is ' +
      'shown once installed.',
  },
  vlm: {
    short: 'Your own AI model',
    title: 'Your own AI model — uses Ollama or LM Studio',
    size: 'nothing to install',
    blurb:
      'Sends each page to a vision model you already run. Nothing is downloaded here — ' +
      'you pull the model yourself. Handles messy handwriting and unusual layouts better ' +
      'than the other options, but takes minutes per page rather than seconds, so it suits ' +
      'a few important documents rather than a whole library.',
  },
}

/** Picks the vision model for OCR Tier 3.
 *
 *  PMA never downloads these - the user pulls them in Ollama or LM Studio - so
 *  this shows what is actually installed and, when nothing is, names models
 *  worth pulling rather than leaving an empty dropdown with no explanation.
 */
function VlmPicker({
  onChanged,
  setNote,
}: Readonly<{ onChanged: () => void; setNote: (s: string) => void }>) {
  const { data, loading, refetch } = useApi(getVlmModels, { cacheKey: 'ocr-vlm-models' })
  const { data: current, refetch: refetchSelection } = useApi(getVlmSelection, {
    cacheKey: 'ocr-vlm-selection',
  })
  const [saving, setSaving] = useState('')

  const choose = async (provider: string, model: string) => {
    setSaving(model)
    try {
      const res = await selectVlmModel(provider, model)
      if (!res.ok) {
        setNote(`Could not use that model: ${res.error_code}`)
        return
      }
      setNote(`OCR will use ${model}.`)
      refetchSelection()
      onChanged()
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'Could not select that model.')
    } finally {
      setSaving('')
    }
  }

  if (loading && !data) {
    return (
      <div className="flex items-center gap-2 text-sm text-text-secondary mb-3">
        <Loader2 className="w-4 h-4 animate-spin" /> Looking for models you already have…
      </div>
    )
  }

  const reachable = (data?.providers ?? []).filter((p) => p.reachable)

  if (reachable.length === 0) {
    return (
      <div className="mb-3 p-3 rounded-xl bg-warning/10 border border-warning/20">
        <p className="text-xs text-warning">
          Neither Ollama nor LM Studio is running. Start one, then{' '}
          <button onClick={() => refetch()} className="underline font-bold">
            check again
          </button>
          .
        </p>
      </div>
    )
  }

  return (
    <div className="mb-3 space-y-3">
      {reachable.map((p) => {
        const vision = p.models.filter((m) => m.vision)
        return (
          <div key={p.provider} className="p-3 rounded-xl border border-black/5 bg-white/40">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-bold text-text-primary">{p.display_name}</span>
              {/* Page images are the most sensitive thing PMA sends anywhere.
                  The dispatch path refuses without consent, but the user should
                  see it here rather than discover it at a prompt. */}
              {!p.is_local && (
                <span className="text-[10px] font-bold text-warning uppercase">
                  not on this machine
                </span>
              )}
            </div>

            {vision.length === 0 ? (
              <p className="text-xs text-text-secondary">
                No vision model found here. Pull one, then check again — these read documents
                well:{' '}
                <span className="font-mono">{(data?.suggestions ?? []).join(', ')}</span>
              </p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {vision.map((m) => {
                  const active =
                    current?.selection?.provider === p.provider &&
                    current?.selection?.model === m.id
                  return (
                    <button
                      key={m.id}
                      onClick={() => choose(p.provider, m.id)}
                      disabled={!!saving}
                      className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${
                        active
                          ? 'bg-primary text-white shadow'
                          : 'bg-black/5 text-text-secondary hover:bg-black/10'
                      } ${saving ? 'opacity-50' : ''}`}
                    >
                      {saving === m.id ? 'Selecting…' : m.id}
                      {active && ' ✓'}
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function OcrSection() {
  const { data: ocr, refetch } = useApi(getOcrStatus, {
    cacheKey: 'ocr-status-settings',
    refetchInterval: 10_000,
  })
  const { data: tierData } = useApi(getOcrTiers, { cacheKey: 'ocr-tiers' })
  const tiers = tierData?.tiers ?? []
  const [selectedTier, setSelectedTier] = useState<string>('cpu')
  const selectedTierInfo = tiers.find((t) => t.id === selectedTier)
  // Show what is actually installed rather than always opening on "cpu".
  useEffect(() => {
    if (tierData?.installed) setSelectedTier(tierData.installed)
  }, [tierData?.installed])
  const [install, setInstall] = useState<OcrInstallState | null>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<OcrQueueItem[]>([])
  const [note, setNote] = useState('')

  const installing = install?.status === 'running'

  // Only poll while an install is actually in flight.
  useEffect(() => {
    if (!installing) return
    const id = setInterval(async () => {
      try {
        const state = await getOcrInstallState()
        setInstall(state)
        if (state.status !== 'running') {
          refetch()
          setBusy(false)
        }
      } catch { /* transient */ }
    }, 1500)
    return () => clearInterval(id)
  }, [installing, refetch])

  const loadFailed = useCallback(async () => {
    try {
      const res = await getOcrQueue('failed', 20)
      setFailed(res.items)
    } catch { setFailed([]) }
  }, [])

  useEffect(() => { if (ocr?.installed) loadFailed() }, [ocr?.installed, ocr?.queue?.failed, loadFailed])

  const handleInstall = async () => {
    setBusy(true)
    setNote('')
    try {
      const state = await installOcrTier(selectedTier)
      setInstall(state)
      // The poller clears `busy`, but it only runs while status is 'running'.
      // Any other status means no poll is coming and nothing else would ever
      // re-enable the button.
      if (state.status !== 'running') setBusy(false)
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'Install failed.')
      setBusy(false)
    }
  }

  const handleUninstall = async () => {
    setBusy(true)
    try { await uninstallOcrTier(); setInstall(null); refetch() }
    catch (e) { setNote(e instanceof Error ? e.message : 'Uninstall failed.') }
    finally { setBusy(false) }
  }

  const handleToggle = async (enabled: boolean) => {
    try {
      const res = await setOcrEnabled(enabled)
      if (!res.ok) setNote(`Cannot enable: ${res.error_code}`)
      refetch()
    } catch (e) { setNote(e instanceof Error ? e.message : 'Could not change OCR state.') }
  }

  const handleResume = async () => {
    try {
      await resumeOcr()
      setNote('OCR resumed.')
      refetch()
    } catch (e) { setNote(e instanceof Error ? e.message : 'Could not resume OCR.') }
  }

  const handleClearCache = async () => {
    try {
      const res = await clearOcrCache()
      setNote(`Cleared ${res.removed} cached page(s).`)
      refetch()
    } catch (e) { setNote(e instanceof Error ? e.message : 'Could not clear cache.') }
  }

  return (
    <div className="glass p-6 rounded-2xl border border-primary/10">
      <div className="flex items-start gap-4 mb-6">
        <div className="p-3 bg-primary/10 rounded-xl">
          <ScanText className="w-6 h-6 text-primary" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-text-primary">OCR for Scanned PDFs</h2>
          <p className="text-sm text-text-secondary mt-1">
            Reads text out of scanned pages so they become searchable. Runs on the CPU in
            its own isolated environment — nothing is added to the main install.
          </p>
        </div>
      </div>

      <div className="p-4 rounded-xl border border-primary/5 bg-white/50 backdrop-blur-md">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-text-primary">{TIER_COPY[selectedTier].title}</h3>
          {ocr?.installed && ocr?.tier === selectedTier ? (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-success/15 text-success">
              <span className="w-1.5 h-1.5 rounded-full bg-success"></span> Installed
            </span>
          ) : (
            <span className="px-2.5 py-1 rounded-full text-xs font-semibold bg-text-secondary/15 text-text-secondary">
              Not installed
            </span>
          )}
        </div>

        {/* Only one tier can be provisioned at a time - the two ONNX Runtime
            builds cannot share an interpreter - so this is a choice, not a
            stack. Switching re-provisions from scratch. */}
        <div className="flex gap-2 mb-3">
          {tiers.map((t) => {
            const copy = TIER_COPY[t.id]
            if (!copy) return null
            const blocked = !!t.unavailable_reason
            return (
              <button
                key={t.id}
                onClick={() => setSelectedTier(t.id)}
                disabled={blocked}
                title={t.unavailable_reason || copy.blurb}
                className={`flex-1 text-left px-3 py-2 rounded-xl border text-xs transition-all ${
                  selectedTier === t.id
                    ? 'border-primary bg-primary/10 text-text-primary'
                    : 'border-black/5 text-text-secondary hover:bg-black/5'
                } ${blocked ? 'opacity-40 cursor-not-allowed' : ''}`}
              >
                <span className="block font-bold">{copy.short}</span>
                <span className="block mt-0.5">{copy.size}</span>
                {t.installed && <span className="block mt-0.5 text-success font-bold">active</span>}
              </button>
            )
          })}
        </div>

        <p className="text-sm text-text-secondary mb-3">{TIER_COPY[selectedTier]?.blurb}</p>

        {selectedTier === 'vlm' && <VlmPicker onChanged={refetch} setNote={setNote} />}

        {selectedTierInfo?.unavailable_reason && (
          <p className="text-xs text-warning mb-3">{selectedTierInfo.unavailable_reason}</p>
        )}

        {!ocr?.uv_available && !ocr?.installed && (
          <p className="text-xs text-warning mb-3">
            Requires <code className="font-mono">uv</code>, which wasn&apos;t found on this machine.
            Install it from docs.astral.sh/uv, then reopen this page.
          </p>
        )}

        {installing && (
          <div className="mb-3">
            <div className="flex justify-between text-xs text-text-secondary mb-1.5">
              <span>{install?.message}</span>
              <span>{install?.pct}%</span>
            </div>
            <div className="h-2 bg-white/40 rounded-full overflow-hidden">
              <div className="h-full bg-primary transition-all duration-300" style={{ width: `${install?.pct ?? 0}%` }} />
            </div>
          </div>
        )}

        {install?.status === 'failed' && (
          <p className="text-xs text-danger mb-3">
            {install.error_code}: {install.message}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-3">
          {!ocr?.installed ? (
            <button
              onClick={handleInstall}
              disabled={busy || !ocr?.uv_available || !!selectedTierInfo?.unavailable_reason}
              className="glass-button flex items-center gap-2 disabled:opacity-50"
            >
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
              {busy ? 'Installing…' : `Install ${TIER_COPY[selectedTier]?.short ?? ''}`}
            </button>
          ) : (
            <>
              <label className="flex items-center gap-2 text-sm font-medium text-text-primary cursor-pointer">
                <input
                  type="checkbox"
                  checked={!!ocr?.enabled}
                  onChange={(e) => handleToggle(e.target.checked)}
                  className="w-4 h-4 accent-primary"
                />
                Enabled
              </label>
              {/* The backend computed and stamped this all along but never
                  exposed it, so a GPU tier that quietly fell back to the
                  processor was indistinguishable from one that had not. */}
              {ocr?.ep && (
                <span className="text-xs text-text-secondary">
                  Running on:{' '}
                  <strong className="text-text-primary">
                    {ocr.ep === 'DmlExecutionProvider' ? 'your graphics card' : 'the processor'}
                  </strong>
                </span>
              )}
              <button onClick={handleClearCache} className="glass-button text-sm">
                Clear OCR cache ({ocr?.cache_mb ?? 0} MB)
              </button>
              <button onClick={handleUninstall} disabled={busy} className="glass-button text-sm disabled:opacity-50">
                Uninstall
              </button>
            </>
          )}
          {installing && (
            <button onClick={() => cancelOcrInstall()} className="glass-button text-sm">Cancel</button>
          )}
        </div>

        {ocr?.installed && (
          <div className="mt-4 grid grid-cols-3 gap-3 text-center">
            {[
              { label: 'Pending pages', value: ocr.pages_pending ?? 0 },
              { label: 'Done', value: ocr.queue?.done ?? 0 },
              { label: 'Failed', value: ocr.queue?.failed ?? 0 },
            ].map(({ label, value }) => (
              <div key={label} className="p-2 rounded-lg bg-white/40">
                <div className="text-lg font-bold text-text-primary">{value}</div>
                <div className="text-[10px] uppercase tracking-wider text-text-secondary">{label}</div>
              </div>
            ))}
          </div>
        )}

        {ocr?.unhealthy && (
          <div className="mt-3 flex items-center justify-between gap-3">
            <p className="text-xs text-danger">
              OCR stopped: {ocr.fatal}. Resume to try again; if it stops again, reinstall the tier.
            </p>
            {/* Unconditional: a fatal raised during the worker handshake leaves
                no failed rows, so the per-file Retry list below does not render
                and this is the only way out of the stopped state. */}
            <button
              onClick={handleResume}
              className="shrink-0 text-xs font-bold px-3 py-1.5 rounded-lg bg-primary/10 text-primary hover:bg-primary/20 transition-all"
            >
              Resume OCR
            </button>
          </div>
        )}

        {ocr?.engine_mismatch && (
          <p className="mt-3 text-xs text-warning">
            OCR is not running the engine it was installed with ({ocr.engine_mismatch}).
            Text is still cached under what actually ran, but accuracy will not match the
            installed tier.
          </p>
        )}

        {failed.length > 0 && (
          <div className="mt-4">
            <h4 className="text-xs font-bold uppercase tracking-wider text-text-secondary mb-2">
              Failed files
            </h4>
            <div className="flex flex-col gap-2">
              {failed.map(item => (
                <div key={item.file_path} className="flex items-center justify-between gap-3 p-2 rounded-lg bg-white/40">
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-text-primary truncate">{item.file_name}</div>
                    <div className="text-[10px] text-danger truncate">{item.last_error}</div>
                  </div>
                  <button
                    onClick={async () => { await retryOcr(item.file_path); loadFailed(); refetch() }}
                    className="glass-button text-xs shrink-0"
                  >
                    Retry
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {note && <p className="mt-3 text-xs text-text-secondary">{note}</p>}
      </div>
    </div>
  )
}

function StorageSection({ sysInfo }: Readonly<{ sysInfo?: SystemInfo }>) {
  const getProgressColor = (pct: number) => {
    if (pct > 90) return 'bg-error'
    if (pct > 75) return 'bg-warning'
    return 'bg-primary'
  }

  if (!sysInfo?.volumes || sysInfo.volumes.length === 0) return null

  return (
    <div className="glass p-6 rounded-2xl border border-primary/10">
      <div className="flex items-start gap-4 mb-6">
        <div className="p-3 bg-primary/10 rounded-xl">
          <HardDrive className="w-6 h-6 text-primary" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-text-primary">Storage</h2>
          <p className="text-sm text-text-secondary mt-1">Disk usage on indexed volumes.</p>
        </div>
      </div>
      <div className="flex flex-col gap-4">
        {sysInfo.volumes.map(v => {
          const pct = Math.round((v.used_gb / v.total_gb) * 100)
          return (
            <div key={v.letter}>
              <div className="flex justify-between text-xs font-medium text-text-secondary mb-1.5">
                <span className="font-bold text-text-primary">{v.letter}:</span>
                <span>{v.used_gb.toFixed(1)} GB used of {v.total_gb.toFixed(1)} GB</span>
              </div>
              <div className="h-2 bg-white/10 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-700 ${getProgressColor(pct)}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <div className="text-right text-[10px] text-text-secondary mt-1">{pct}% used · {v.free_gb.toFixed(1)} GB free</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function SplitBrainSection({
  driveInfo,
  onPurge,
  purging
}: Readonly<{
  driveInfo?: DriveInfo;
  onPurge: () => void;
  purging: boolean;
}>) {
  if (!driveInfo) return null

  const isAtRisk = driveInfo.is_portable_fs && driveInfo.lancedb_mode !== 'split_brain'
  const cardClasses = `glass p-6 rounded-2xl border ${isAtRisk ? 'border-warning/30 bg-warning/5' : 'border-primary/10'}`
  const iconBgClasses = `p-3 rounded-xl ${isAtRisk ? 'bg-warning/10' : 'bg-primary/10'}`
  const iconClasses = `w-6 h-6 ${isAtRisk ? 'text-warning' : 'text-primary'}`

  return (
    <div className={cardClasses}>
      <div className="flex items-start gap-4 mb-5">
        <div className={iconBgClasses}>
          <DatabaseZap className={iconClasses} />
        </div>
        <div>
          <h2 className="text-lg font-bold text-text-primary">Vector Cache &amp; Portability</h2>
          <p className="text-sm text-text-secondary mt-1">
            Manage the local LanceDB host cache used in Split-Brain mode.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 mb-5 text-sm">
        <div className="p-3 rounded-xl bg-white/5 border border-white/10">
          <span className="text-text-secondary block mb-1">Drive</span>
          <span className="font-semibold text-text-primary flex items-center gap-1.5">
            <HardDrive className="w-3.5 h-3.5 text-primary" />
            {driveInfo.drive || '–'}
          </span>
        </div>
        <div className="p-3 rounded-xl bg-white/5 border border-white/10">
          <span className="text-text-secondary block mb-1">Filesystem</span>
          <span className={`font-semibold ${driveInfo.is_portable_fs ? 'text-warning' : 'text-success'}`}>
            {driveInfo.fs_type}
          </span>
        </div>
        <div className="p-3 rounded-xl bg-white/5 border border-white/10">
          <span className="text-text-secondary block mb-1">LanceDB Mode</span>
          <span className={`font-semibold ${driveInfo.lancedb_mode === 'split_brain' ? 'text-success' : 'text-text-primary'}`}>
            {driveInfo.lancedb_mode === 'split_brain' ? 'Split-Brain ✓' : 'Portable'}
          </span>
        </div>
        <div className="p-3 rounded-xl bg-white/5 border border-white/10">
          <span className="text-text-secondary block mb-1">Index Safety</span>
          {isAtRisk ? (
            <span className="font-semibold text-warning flex items-center gap-1">
              <AlertTriangle className="w-3.5 h-3.5" /> At Risk
            </span>
          ) : (
            <span className="font-semibold text-success flex items-center gap-1">
              <CheckCircle2 className="w-3.5 h-3.5" /> Safe
            </span>
          )}
        </div>
      </div>

      {driveInfo.lancedb_mode === 'split_brain' && (
        <div className="flex items-center gap-3">
          <button
            onClick={onPurge}
            disabled={purging}
            className="glass-button text-warning hover:bg-warning/10 !py-2 !px-4 gap-2 border border-warning/20 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Trash2 className="w-4 h-4" />
            {purging ? 'Purging…' : 'Purge Host Cache'}
          </button>
          <p className="text-xs text-text-secondary">
            Deletes the local vector index. PMA rebuilds from the portable SQLite database on next restart.
          </p>
        </div>
      )}
    </div>
  )
}

function ResetSection({ onRestartOnboarding, onFullReset }: Readonly<{ onRestartOnboarding: () => void; onFullReset: () => void }>) {
  return (
    <div className="glass p-6 rounded-2xl border border-error/10 bg-error/5">
      <div className="flex items-start gap-4 mb-6">
        <div className="p-3 bg-error/10 rounded-xl">
          <RefreshCcw className="w-6 h-6 text-error" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-text-primary">Showcase & Reset</h2>
          <p className="text-sm text-text-secondary mt-1">
            Use these options to prepare the app for a demonstration or fresh start.
          </p>
        </div>
      </div>

      <div className="flex flex-wrap gap-4">
        <button
          onClick={onRestartOnboarding}
          className="glass-button !text-text-primary hover:bg-white/10 !py-2 !px-4 gap-2 border border-white/10"
        >
          <RefreshCcw className="w-4 h-4" />
          Restart Onboarding
        </button>
        <button
          onClick={onFullReset}
          className="glass-button !text-error hover:bg-error/10 !py-2 !px-4 gap-2 border border-error/20"
        >
          <Trash2 className="w-4 h-4" />
          Full Application Reset
        </button>
      </div>
    </div>
  )
}

// ── Main Page Component ──────────────────────────────────────────────

export function SettingsPage() {
  const { data: localModels, refetch: refetchLocalModels } = useApi(getLocalModels, { cacheKey: 'local-models' })
  const { data: sysInfo } = useApi(getSystemInfo, { cacheKey: 'system-info' })
  const { refetch: refetchPrefs } = useApi(getLLMPreferences, { cacheKey: 'llm-prefs' })
  const { data: driveInfo } = useApi(getDriveInfo, { cacheKey: 'drive-info' })

  const [message, setMessage] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [savingPrefs, setSavingPrefs] = useState(false)
  const [purgingCache, setPurgingCache] = useState(false)



  const handleSavePrefs = async (prefs: LLMPreferences) => {
    setSavingPrefs(true)
    try {
      await setLLMPreferences(prefs)
      for (const [key, val] of Object.entries(prefs)) {
        if (key.endsWith('_model') && typeof val === 'string' && val) {
          const providerId = key.replace('_model', '')
          await setProviderDefaultModel(providerId, val).catch(() => {})
        }
      }
      invalidateCache('llm-prefs')
      invalidateCache('providers-list')
      refetchPrefs()
      setMessage({ type: 'ok', text: 'LLM preferences saved.' })
    } catch (e) {
      setMessage({ type: 'err', text: e instanceof Error ? e.message : 'Failed to save preferences' })
    } finally {
      setSavingPrefs(false)
    }
  }

  const handleResetOnboarding = () => {
    if (!confirm('This will restart the onboarding flow. You can use it for your showcase. Proceed?')) return
    localStorage.removeItem('pma_setup_complete')
    globalThis.location.href = '/setup'
  }

  const handleFullReset = async () => {
    if (!confirm('CAUTION: This will clear your entire index AND reset onboarding. This cannot be undone. Proceed?')) return
    try {
      await clearIndex()
      localStorage.removeItem('pma_setup_complete')
      globalThis.location.href = '/setup'
    } catch (e) {
      setMessage({ type: 'err', text: e instanceof Error ? e.message : 'Reset failed' })
    }
  }

  const handlePurgeCache = async () => {
    if (!confirm('This will delete the local LanceDB vector cache. The backend will rebuild it from SQLite on next restart. Proceed?')) return
    setPurgingCache(true)
    try {
      const res = await purgeHostCache()
      setMessage({ type: 'ok', text: res.message })
    } catch (e) {
      setMessage({ type: 'err', text: e instanceof Error ? e.message : 'Purge failed' })
    } finally {
      setPurgingCache(false)
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6 animate-fade-in-up custom-scrollbar">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-3">
            <Settings className="w-7 h-7 text-primary" />
            Settings
          </h1>
          <p className="text-text-secondary mt-1 text-sm">
            Configure integrations and application preferences
          </p>
        </div>
      </div>

      {/* Message banner */}
      {message && (
        <div className={`flex items-center gap-2 px-4 py-3 rounded-xl text-sm transition-all duration-300 ${message.type === 'ok' ? 'bg-success/20 text-success' : 'bg-error/20 text-error'}`}>
          {message.type === 'ok' ? <CheckCircle2 className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
          {message.text}
        </div>
      )}



      <LocalModelsSection localModels={localModels} onStarted={refetchLocalModels} />

      <LLMPreferencesSection
        onSave={handleSavePrefs}
        saving={savingPrefs}
      />

      <OcrSection />

      <StorageSection sysInfo={sysInfo} />

      <SplitBrainSection
        driveInfo={driveInfo}
        onPurge={handlePurgeCache}
        purging={purgingCache}
      />

      <ResetSection
        onRestartOnboarding={handleResetOnboarding}
        onFullReset={handleFullReset}
      />
    </div>
  )
}
