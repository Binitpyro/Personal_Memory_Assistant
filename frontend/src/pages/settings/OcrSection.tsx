/**
 * Extracted from SettingsPage.tsx, which had grown to 1341 lines holding ten
 * unrelated section components in one unbroken scroll. Behaviour is unchanged;
 * only the file boundary moved.
 */
import { useState, useEffect, useCallback } from 'react'
import { CheckCircle2, RefreshCcw, Play, Download, Loader2, ScanText } from 'lucide-react'
import { useApi, invalidateCache } from '../../useApi'
import { launchProvider, getOcrStatus, type OcrStatus, getOcrTiers, selectOcrTier, getVlmModels, getVlmSelection, selectVlmModel, getOcrInstallState, getOcrQueue, installOcrTier, uninstallOcrTier, cancelOcrInstall, resumeOcr, setOcrEnabled, retryOcr, clearOcrCache, type OcrInstallState, type OcrQueueItem } from '../../api'
import { CACHE_KEYS } from '../../cacheKeys'
import { useOptimisticMutation } from '../../useOptimisticMutation'
import { confirmThen } from './confirmThen'

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
export function VlmPicker({
  onChanged,
  setNote,
}: Readonly<{ onChanged: () => void; setNote: (s: string) => void }>) {
  const { data, loading, refetch } = useApi(getVlmModels, { cacheKey: CACHE_KEYS.ocrVlmModels })
  const { data: current, refetch: refetchSelection } = useApi(getVlmSelection, {
    cacheKey: CACHE_KEYS.ocrVlmSelection,
  })
  const [saving, setSaving] = useState('')
  const [isChecking, setIsChecking] = useState(false)
  const [startingProvider, setStartingProvider] = useState<string | null>(null)

  const choose = async (provider: string, model: string) => {
    setSaving(model)
    try {
      const res = await selectVlmModel(provider, model)
      if (!res.ok) {
        setNote(`Could not use that model: ${res.error_code}`)
        return
      }
      setNote(`OCR will use ${model}.`)
      invalidateCache(CACHE_KEYS.ocrTiers)
      invalidateCache(CACHE_KEYS.ocrStatus)
      refetchSelection()
      onChanged()
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'Could not select that model.')
    } finally {
      setSaving('')
    }
  }

  const handleCheckAgain = async () => {
    setIsChecking(true)
    invalidateCache(CACHE_KEYS.ocrVlmModels)
    try {
      await refetch()
    } finally {
      setIsChecking(false)
    }
  }

  const handleStartProvider = async (providerId: string, displayName: string) => {
    setStartingProvider(providerId)
    setNote(`Starting ${displayName}...`)
    try {
      const res = await launchProvider(providerId)
      if (res.ok) {
        setNote(`${displayName} started successfully.`)
        invalidateCache(CACHE_KEYS.ocrVlmModels)
        invalidateCache(CACHE_KEYS.localModels)
        await refetch()
      } else {
        setNote(res.message || `Could not start ${displayName}.`)
      }
    } catch (e) {
      setNote(e instanceof Error ? e.message : `Could not start ${displayName}.`)
    } finally {
      setStartingProvider(null)
    }
  }

  if (loading && !data && !isChecking) {
    return (
      <div className="flex items-center gap-2 text-sm text-text-secondary mb-3">
        <Loader2 className="w-4 h-4 animate-spin" /> Looking for models you already have…
      </div>
    )
  }

  const reachable = (data?.providers ?? []).filter((p) => p.reachable)

  if (reachable.length === 0) {
    return (
      <div className="mb-3 p-4 rounded-xl bg-warning/10 border border-warning/20">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold text-warning">
              Neither Ollama nor LM Studio is currently reachable.
            </p>
            <p className="text-[11px] text-text-secondary mt-0.5">
              Start your local model server, then check again to detect installed vision models.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={() => handleStartProvider('ollama', 'Ollama')}
              disabled={!!startingProvider || isChecking}
              className="glass-button px-2.5 py-1.5 text-xs flex items-center gap-1.5 disabled:opacity-50"
              title="Start Ollama server"
            >
              {startingProvider === 'ollama' ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Play className="w-3.5 h-3.5 text-success" />
              )}
              Start Ollama
            </button>
            <button
              type="button"
              onClick={() => handleStartProvider('lm_studio', 'LM Studio')}
              disabled={!!startingProvider || isChecking}
              className="glass-button px-2.5 py-1.5 text-xs flex items-center gap-1.5 disabled:opacity-50"
              title="Start LM Studio server"
            >
              {startingProvider === 'lm_studio' ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Play className="w-3.5 h-3.5 text-success" />
              )}
              Start LM Studio
            </button>
            <button
              type="button"
              onClick={handleCheckAgain}
              disabled={isChecking || !!startingProvider}
              className="glass-button px-3 py-1.5 text-xs font-bold flex items-center gap-1.5 text-text-primary hover:bg-raised disabled:opacity-50"
            >
              <RefreshCcw className={`w-3.5 h-3.5 ${isChecking || loading ? 'animate-spin' : ''}`} />
              {isChecking || loading ? 'Checking…' : 'Check again'}
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="mb-3 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-text-secondary">Detected Local Providers</span>
        <button
          type="button"
          onClick={handleCheckAgain}
          disabled={isChecking || loading}
          className="text-xs text-text-secondary hover:text-text-primary flex items-center gap-1 transition-colors"
        >
          <RefreshCcw className={`w-3 h-3 ${isChecking || loading ? 'animate-spin' : ''}`} />
          Refresh models
        </button>
      </div>
      {reachable.map((p) => {
        const vision = p.models.filter((m) => m.vision)
        return (
          <div key={p.provider} className="p-3 rounded-xl border border-rule bg-surface">
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
                      type="button"
                      onClick={() => choose(p.provider, m.id)}
                      disabled={!!saving}
                      className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${
                        active
                          ? 'bg-plate text-on-plate shadow'
                          : 'bg-raised text-text-secondary hover:bg-raised'
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

export function OcrSection() {
  const { data: ocr, refetch } = useApi(getOcrStatus, {
    cacheKey: CACHE_KEYS.ocrStatus,
    refetchInterval: 10_000,
  })
  const { data: tierData, refetch: refetchTiers } = useApi(getOcrTiers, { cacheKey: CACHE_KEYS.ocrTiers })
  const tiers = tierData?.tiers ?? []
  const [selectedTier, setSelectedTier] = useState<string>('cpu')
  const selectedTierInfo = tiers.find((t) => t.id === selectedTier)

  // Show what is actually installed / active on first load
  const [initialised, setInitialised] = useState(false)
  useEffect(() => {
    if (!initialised && tierData?.installed && tierData.installed !== 'none') {
      setSelectedTier(tierData.installed)
      setInitialised(true)
    }
  }, [tierData?.installed, initialised])

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
          invalidateCache(CACHE_KEYS.ocrTiers)
          invalidateCache(CACHE_KEYS.ocrStatus)
          refetch()
          refetchTiers()
          setBusy(false)
        }
      } catch { /* transient */ }
    }, 1500)
    return () => clearInterval(id)
  }, [installing, refetch, refetchTiers])

  const loadFailed = useCallback(async () => {
    try {
      const res = await getOcrQueue('failed', 20)
      setFailed(res.items)
    } catch { setFailed([]) }
  }, [])

  useEffect(() => { if (ocr?.installed) loadFailed() }, [ocr?.installed, ocr?.queue?.failed, loadFailed])

  const handleInstall = () => confirmThen(
    `Install the ${TIER_COPY[selectedTier]?.short ?? selectedTier} tier?`,
    'This downloads the engine and its model weights, which can be several hundred megabytes.',
    'Install',
    doInstall,
  )

  const doInstall = async () => {
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

  const handleSelectActiveTier = async (tier: string) => {
    setBusy(true)
    setNote('')
    try {
      const res = await selectOcrTier(tier)
      if (!res.ok) {
        setNote(`Could not switch tier: ${res.error_code}`)
        return
      }
      setNote(`OCR switched to ${TIER_COPY[tier]?.short ?? tier}.`)
      invalidateCache(CACHE_KEYS.ocrTiers)
      invalidateCache(CACHE_KEYS.ocrStatus)
      refetch()
      refetchTiers()
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'Could not switch tier.')
    } finally {
      setBusy(false)
    }
  }

  const handleUninstall = (tierToUninstall?: string) => {
    const target = tierToUninstall || selectedTier
    confirmThen(
      `Uninstall the ${TIER_COPY[target]?.short ?? target} tier?`,
      'This deletes its virtual environment and downloaded model weights. Reinstalling downloads them again.',
      'Uninstall',
      () => doUninstall(target),
    )
  }

  const doUninstall = async (target: string) => {
    setBusy(true)
    try {
      await uninstallOcrTier(target)
      setInstall(null)
      setNote(`Uninstalled ${TIER_COPY[target]?.short ?? target} tier.`)
      invalidateCache(CACHE_KEYS.ocrTiers)
      invalidateCache(CACHE_KEYS.ocrStatus)
      refetch()
      refetchTiers()
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'Uninstall failed.')
    } finally {
      setBusy(false)
    }
  }

  // The checkbox reads `checked={!!ocr?.enabled}` straight off server data, so
  // before this it visibly snapped back to its old position and stayed there
  // until the refetch landed. The optimistic write moves it on click and the
  // rollback puts it back only if the server actually refuses.
  const toggleOcr = useOptimisticMutation<boolean, Awaited<ReturnType<typeof setOcrEnabled>>, OcrStatus>({
    mutationFn: setOcrEnabled,
    cacheKey: CACHE_KEYS.ocrStatus,
    invalidates: [CACHE_KEYS.ocrTiers],
    optimistic: (current, enabled) => (current ? { ...current, enabled } : current),
    onSuccess: (res) => {
      // A 200 that says "no" is not an error, so it does not roll back. The
      // onSettled invalidation refetches and the real value wins; this only
      // explains why.
      if (!res.ok) setNote(`Cannot enable: ${res.error_code}`)
    },
    onError: (e) => setNote(e instanceof Error ? e.message : 'Could not change OCR state.'),
  })

  const handleToggle = (enabled: boolean) => toggleOcr.mutate(enabled)

  const handleResume = async () => {
    try {
      await resumeOcr()
      setNote('OCR resumed.')
      refetch()
    } catch (e) { setNote(e instanceof Error ? e.message : 'Could not resume OCR.') }
  }

  const handleClearCache = () => confirmThen(
    'Clear the OCR page cache?',
    'Recognised text for scanned pages is discarded. Those pages are OCR-ed again next time they are indexed.',
    'Clear',
    doClearCache,
  )

  const doClearCache = async () => {
    try {
      const res = await clearOcrCache()
      setNote(`Cleared ${res.removed} cached page(s).`)
      refetch()
    } catch (e) { setNote(e instanceof Error ? e.message : 'Could not clear cache.') }
  }

  const isSelectedActive = ocr?.installed && ocr?.tier === selectedTier
  const isSelectedInstalled = !!selectedTierInfo?.installed

  return (
    <div className="glass p-6 rounded-2xl border border-primary/10">
      <div className="flex items-start gap-4 mb-6">
        <div className="p-3 bg-primary/10 rounded-xl">
          <ScanText className="w-6 h-6 text-primary" />
        </div>
        <div>
          <h2 className="font-serif text-lg font-medium text-text-primary">OCR for Scanned PDFs</h2>
          <p className="text-sm text-text-secondary mt-1">
            Reads text out of scanned pages so they become searchable. Runs in its own isolated
            environment — nothing is added to the main install.
          </p>
        </div>
      </div>

      <div className="p-4 rounded-xl border border-primary/5 bg-surface backdrop-blur-md">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-text-primary">{TIER_COPY[selectedTier]?.title}</h3>
          {isSelectedActive ? (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-success/15 text-success">
              <span className="w-1.5 h-1.5 rounded-full bg-success"></span> Active
            </span>
          ) : isSelectedInstalled ? (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-primary/15 text-primary">
              <span className="w-1.5 h-1.5 rounded-full bg-primary"></span> Ready
            </span>
          ) : (
            <span className="px-2.5 py-1 rounded-full text-xs font-semibold bg-text-secondary/15 text-text-secondary">
              Not installed
            </span>
          )}
        </div>

        {/* Tier selection tabs */}
        <div className="flex gap-2 mb-3">
          {tiers.map((t) => {
            const copy = TIER_COPY[t.id]
            if (!copy) return null
            const blocked = !!t.unavailable_reason
            const isThisActive = ocr?.installed && ocr?.tier === t.id
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setSelectedTier(t.id)}
                disabled={blocked}
                title={t.unavailable_reason || copy.blurb}
                className={`flex-1 text-left px-3 py-2 rounded-xl border text-xs transition-all ${
                  selectedTier === t.id
                    ? 'border-primary bg-primary/10 text-text-primary'
                    : 'border-rule text-text-secondary hover:bg-raised'
                } ${blocked ? 'opacity-40 cursor-not-allowed' : ''}`}
              >
                <span className="block font-bold">{copy.short}</span>
                <span className="block mt-0.5">{copy.size}</span>
                {isThisActive ? (
                  <span className="block mt-0.5 text-success font-bold">active</span>
                ) : t.installed ? (
                  <span className="block mt-0.5 text-primary font-medium">ready</span>
                ) : null}
              </button>
            )
          })}
        </div>

        <p className="text-sm text-text-secondary mb-3">{TIER_COPY[selectedTier]?.blurb}</p>

        {selectedTier === 'vlm' && <VlmPicker onChanged={refetch} setNote={setNote} />}

        {selectedTierInfo?.unavailable_reason && (
          <p className="text-xs text-warning mb-3">{selectedTierInfo.unavailable_reason}</p>
        )}

        {!ocr?.uv_available && !ocr?.installed && selectedTier !== 'vlm' && (
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
            <div className="h-2 bg-surface rounded-full overflow-hidden">
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
          {isSelectedActive ? (
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
              {ocr?.ep && selectedTier !== 'vlm' && (
                <span className="text-xs text-text-secondary">
                  Running on:{' '}
                  <strong className="text-text-primary">
                    {ocr.ep === 'DmlExecutionProvider' ? 'your graphics card' : 'the processor'}
                  </strong>
                </span>
              )}
              <button type="button" onClick={handleClearCache} className="glass-button text-sm">
                Clear OCR cache ({ocr?.cache_mb ?? 0} MB)
              </button>
              {selectedTier !== 'vlm' && (
                <button
                  type="button"
                  onClick={() => handleUninstall(selectedTier)}
                  disabled={busy}
                  className="glass-button text-sm disabled:opacity-50 text-danger hover:bg-danger/10"
                >
                  Uninstall
                </button>
              )}
            </>
          ) : isSelectedInstalled ? (
            <>
              <button
                type="button"
                onClick={() => handleSelectActiveTier(selectedTier)}
                disabled={busy}
                className="glass-button flex items-center gap-2 bg-primary/10 text-primary font-bold hover:bg-primary/20 disabled:opacity-50"
              >
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
                Switch to {TIER_COPY[selectedTier]?.short ?? ''}
              </button>
              {selectedTier !== 'vlm' && (
                <button
                  type="button"
                  onClick={() => handleUninstall(selectedTier)}
                  disabled={busy}
                  className="glass-button text-sm disabled:opacity-50 text-danger hover:bg-danger/10"
                >
                  Uninstall
                </button>
              )}
            </>
          ) : (
            selectedTier !== 'vlm' && (
              <button
                type="button"
                onClick={handleInstall}
                disabled={busy || !ocr?.uv_available || !!selectedTierInfo?.unavailable_reason}
                className="glass-button flex items-center gap-2 disabled:opacity-50"
              >
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
                {busy ? 'Installing…' : `Install ${TIER_COPY[selectedTier]?.short ?? ''}`}
              </button>
            )
          )}
          {installing && (
            <button type="button" onClick={() => cancelOcrInstall()} className="glass-button text-sm">Cancel</button>
          )}
        </div>

        {ocr?.installed && (
          <div className="mt-4 grid grid-cols-3 gap-3 text-center">
            {[
              { label: 'Pending pages', value: ocr.pages_pending ?? 0 },
              { label: 'Done', value: ocr.queue?.done ?? 0 },
              { label: 'Failed', value: ocr.queue?.failed ?? 0 },
            ].map(({ label, value }) => (
              <div key={label} className="p-2 rounded-lg bg-surface">
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
                <div key={item.file_path} className="flex items-center justify-between gap-3 p-2 rounded-lg bg-surface">
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
