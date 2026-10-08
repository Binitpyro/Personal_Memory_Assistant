import { useState, useCallback, useEffect } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useApi, invalidateCorpusCaches } from '../useApi'
import {
  getHealth,
  getFileTree,
  getIndexStatus,
  getSystemInfo,
  getCurrentProvider,
  getOcrStatus,
  pickFolder,
  startIndexing,
  clearIndex,
  cancelIndexing,
  seedDemo,
  subscribeProgress,
  clearBackendCaches,
  type IndexStatus,
} from '../api'
import { CACHE_KEYS } from '../cacheKeys'
import { useSessionProvider } from '../context/SessionProviderContext'
import { Button, CellProgress, FigureLine, FilmStrip, useWorking } from '../components/ui'

export function LibraryPage() {
  const [folderPath, setFolderPath] = useState('')
  const [indexing, setIndexing] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [liveProgress, setLiveProgress] = useState<(IndexStatus & { current_file: string }) | null>(null)
  const [message, setMessage] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)

  // Pause /index/status polling while local "indexing" is true; SSE drives live progress.
  const { data: status, error: statusError, refetch: refetchStatus } = useApi(getIndexStatus, {
    cacheKey: CACHE_KEYS.indexStatus,
    refetchInterval: indexing ? 0 : 10_000,
  })
  const { data: sysInfo } = useApi(getSystemInfo, { cacheKey: CACHE_KEYS.systemInfo })
  // The same tree Ask and Explorer read, for the film strip and the figures.
  const { data: fileTree } = useApi(getFileTree, { cacheKey: CACHE_KEYS.fileTree })
  // Which model actually answers. NOT AppConfig.gemini_model, which names
  // Gemini whoever is serving, and NOT health.model_ready, which is the ONNX
  // embedder's readiness rather than the chat LLM's.
  const { data: activeProvider } = useApi(getCurrentProvider, {
    cacheKey: CACHE_KEYS.currentProvider,
  })
  const { mostUsedModel } = useSessionProvider()

  // OCR runs after the index run finishes, so this keeps polling regardless of
  // indexing state. Without it "indexing complete" is a lie for scanned PDFs.
  const { data: ocr } = useApi(getOcrStatus, {
    cacheKey: CACHE_KEYS.ocrStatus,
    refetchInterval: 10_000,
  })

  // Derive running state from BOTH local flag and polled backend status
  const isRunning = indexing || status?.status === 'running'
  // Starts the window's grain the moment Index is pressed, before any poll sees the run.
  useWorking(isRunning)

  // Pause background polling while SSE stream is active
  const { data: health, refetch: refetchHealth } = useApi(getHealth, {
    cacheKey: CACHE_KEYS.health,
    refetchInterval: isRunning ? 0 : 10_000
  })

  // Sync local indexing flag from backend status on page load/poll
  // 'cancelling' is still a run: the files in flight finish first, and the SSE
  // stream is what reports the end. AppShell polls this same entry during a run.
  useEffect(() => {
    if (!status?.status) return
    setIndexing(status.status === 'running' || status.status === 'cancelling')
  }, [status?.status])

  // SSE progress stream while indexing (driven by isRunning, survives reload)
  useEffect(() => {
    if (!isRunning) return
    const unsub = subscribeProgress((data) => {
      setLiveProgress(data)
      if (data.status !== 'running' && data.status !== 'cancelling') {
        setIndexing(false)
        setCancelling(false)
        setLiveProgress(null)
        invalidateCorpusCaches()
        refetchHealth()
        refetchStatus()
        if (data.run_failed) {
          // The stream omits last_error (it is exempt from the token check); /index/status has it.
          setMessage({ type: 'err', text: 'Indexing failed — the run stopped before completing' })
          getIndexStatus()
            .then((s) => s.last_error && setMessage({ type: 'err', text: `Indexing failed — ${s.last_error}` }))
            .catch(() => {})
        } else {
          const failed = data.failed_files ? `, ${data.failed_files} failed` : ''
          setMessage({ type: 'ok', text: `Indexing complete — ${data.processed_files} files processed${failed}` })
        }
      }
    })
    return unsub
  }, [isRunning, refetchHealth, refetchStatus])

  const handleBrowse = useCallback(async () => {
    try {
      const { path, error } = await pickFolder()
      if (path) setFolderPath(path)
      else if (error) setMessage({ type: 'err', text: error })
    } catch {
      setMessage({ type: 'err', text: 'Could not open folder picker' })
    }
  }, [])

  const handleIndex = useCallback(async () => {
    if (!folderPath.trim()) return
    try {
      setMessage(null)
      await startIndexing([folderPath.trim()])
      setIndexing(true)
    } catch (e) {
      setMessage({ type: 'err', text: e instanceof Error ? e.message : 'Indexing failed' })
    }
  }, [folderPath])

  const handleCancel = useCallback(async () => {
    if (!isRunning || cancelling) return
    try {
      setCancelling(true)
      await cancelIndexing()
      setMessage({ type: 'ok', text: 'Cancelling... Please wait for current files to finish.' })
    } catch (e) {
      setMessage({ type: 'err', text: e instanceof Error ? e.message : 'Cancel failed' })
      setCancelling(false)
    }
  }, [isRunning, cancelling])

  // Neither of these has optimistic state worth showing - what they need is a
  // pending one. Both used to run with no indication at all, so the button
  // stayed live and a second click sent a second request.
  const clearIndexMutation = useMutation({
    mutationFn: clearIndex,
    onSuccess: () => {
      invalidateCorpusCaches()
      refetchHealth()
      refetchStatus()
      setMessage({ type: 'ok', text: 'All indexed data cleared' })
    },
    onError: (e) => {
      setMessage({ type: 'err', text: e instanceof Error ? e.message : 'Clear failed' })
    },
  })

  const handleClear = useCallback(() => {
    if (isRunning || clearIndexMutation.isPending) return
    if (!confirm('This will permanently delete ALL indexed data. Continue?')) return
    clearIndexMutation.mutate()
  }, [isRunning, clearIndexMutation])



  const handleDemo = useCallback(async () => {
    if (isRunning) return
    try {
      const res = await seedDemo()
      setIndexing(true)
      setMessage({ type: 'ok', text: res.message })
    } catch (e) {
      setMessage({ type: 'err', text: e instanceof Error ? e.message : 'Demo seed failed' })
    }
  }, [isRunning])

  const refreshMutation = useMutation({
    mutationFn: clearBackendCaches,
    // The local refresh happens either way: a backend cache that refuses to
    // clear is not a reason to leave the user looking at stale numbers.
    onSettled: (_data, error) => {
      if (error) console.error('Failed to clear backend caches:', error)
      invalidateCorpusCaches()
      refetchHealth()
      refetchStatus()
      setMessage({
        type: 'ok',
        text: error ? 'Local data refreshed' : 'Data refreshed successfully',
      })
    },
  })

  const handleRefresh = useCallback(() => {
    if (refreshMutation.isPending) return
    refreshMutation.mutate()
  }, [refreshMutation])

  const filesIndexed = status?.files_indexed ?? 0
  const chunksIndexed = status?.chunks_indexed ?? 0
  const scanStatus = isRunning ? 'Indexing…' : 'Idle'
  const progressPct = liveProgress?.progress_percent ?? status?.progress_percent ?? 0

  // The film strip: every indexed file as a frame, grouped by folder. Past
  // STRIP_MAX it samples every k-th file, so each folder keeps its share of the
  // roll and the captions still line up with the bars.
  // ponytail: sampled, not aggregated; bin by folder if a real library reads as noise.
  const STRIP_MAX = 480
  const folders = Object.entries(fileTree?.folders ?? {})
  const allFiles = folders.flatMap(([, files]) => files)
  const step = Math.max(1, Math.ceil(allFiles.length / STRIP_MAX))
  const strip = {
    files: allFiles.filter((_, i) => i % step === 0).map((f) => ({ size: f.size, opens: f.usage_count ?? 0 })),
    groups: folders.map(([name, files]) => ({
      name: name.split(/[\\/]/).filter(Boolean).pop() ?? name,
      count: files.length,
      detail: `${files.length} · ${(files.reduce((s, f) => s + f.size, 0) / (1024 * 1024)).toFixed(1)} MB`,
    })),
    note: step > 1 ? `1 in ${step} of ${allFiles.length.toLocaleString()} files shown` : undefined,
  }

  return (
    <div className="flex-1 overflow-y-auto px-8 py-6 space-y-8 custom-scrollbar">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-baseline gap-3">
            <span aria-hidden className="font-mono text-[10px] tracking-[.1em] text-text-tertiary">02</span>
            <h1 className="stock text-[28px] leading-none m-0">Library</h1>
          </div>
          <p className="text-text-secondary mt-2 text-sm">
            Manage your indexed files and memory sources
          </p>
        </div>
        <div className="flex gap-3">
          <Button onClick={handleRefresh} disabled={refreshMutation.isPending}>
            <span aria-hidden>↻</span> Refresh
          </Button>
        </div>
      </div>

      {/* Message banner: a square beside the words, fog for a fault. */}
      {message && (
        <div className={`flex items-center gap-2 px-4 py-3 text-sm bg-surface border border-rule ${message.type === 'ok' ? 'text-text-primary' : 'text-error'}`}>
          <span aria-hidden className="w-2 h-2 shrink-0 bg-current" />
          {message.text}
        </div>
      )}

      {/* A 401 or a dead backend otherwise reads as an empty, idle library, and
          "Files 0" invites a re-index or the thought that data was wiped. */}
      {statusError && (
        <div role="alert" className="flex items-center gap-2 px-4 py-3 text-sm bg-surface border border-rule text-error">
          <span aria-hidden className="w-2 h-2 shrink-0 bg-current" />
          Can't read the index status ({statusError}). The figures below may be out of date.
        </div>
      )}

      {/* Figures on one ruled line, counted up once. Safelight has no stat cards. */}
      <FigureLine
        items={[
          { label: 'Files', value: filesIndexed },
          { label: 'Chunks', value: chunksIndexed },
          ...(fileTree ? [
            { label: 'Folders', value: Object.keys(fileTree.folders).length },
            { label: 'On disk', value: Math.round(fileTree.total_size / (1024 * 1024)), unit: 'MB' },
          ] : []),
        ]}
      />

      {/* The standing facts, in edge type. Casing is load-bearing for
          LibraryPage.test.tsx; the caps are CSS. */}
      <dl className="flex flex-wrap gap-x-10 gap-y-2 m-0 font-mono text-[10.5px] tracking-[.12em] uppercase">
        {[
          { label: 'Scan Status', value: scanStatus },
          // Most-used once the user has actually run anything; until then there
          // is no usage to report, so fall back to whichever model would answer
          // right now. Both are real — neither is a hardcoded provider name.
          mostUsedModel
            ? { label: `Most used · ${mostUsedModel.provider}`, value: mostUsedModel.model }
            : { label: activeProvider?.provider ? `Model · ${activeProvider.provider}` : 'Model', value: activeProvider?.model || (activeProvider ? 'Not configured' : 'Loading…') },
        ].map(({ label, value }) => (
          <div key={label} className="flex gap-3">
            <dt className="text-text-tertiary">{label}</dt>
            <dd className="m-0 text-text-primary">{value}</dd>
          </div>
        ))}
      </dl>

      {/* Indexing: a word and a number, and the cells filling in. */}
      {isRunning && (
        // A screen reader previously got nothing at all from the longest
        // operation in the product. Polite, so it does not interrupt; the file
        // name changes far too often to announce, so only the summary is live.
        <div className="glass-card flex flex-col gap-3" role="group" aria-label="Indexing progress">
          <span className="text-sm text-text-secondary truncate">
            {liveProgress?.current_file || 'Processing…'} ({liveProgress?.processed_files ?? status?.processed_files ?? 0}/{liveProgress?.total_files ?? status?.total_files ?? 0})
          </span>
          <CellProgress
            label="Indexing…"
            done={liveProgress?.processed_files ?? status?.processed_files ?? 0}
            total={liveProgress?.total_files ?? status?.total_files ?? 0}
          />
          <span className="sr-only" aria-live="polite">
            {`${Math.round(progressPct)} percent, ${liveProgress?.processed_files ?? 0} of ${liveProgress?.total_files ?? 0} files indexed`}
          </span>
        </div>
      )}

      {/* OCR backlog. Deliberately outside the isRunning guard: OCR is drained
          after the index run ends, so this has to survive run completion. */}
      {!!ocr?.pages_pending && (
        <div className="glass-card">
          <div className="flex items-center gap-3">
            <div className="min-w-0">
              <div className="text-sm font-semibold text-text-primary">
                {ocr.pages_pending.toLocaleString()} page{ocr.pages_pending === 1 ? '' : 's'} pending OCR
              </div>
              <div className="text-xs text-text-secondary truncate">
                {ocr.unhealthy
                  ? `OCR stopped: ${ocr.fatal}`
                  : ocr.worker_running
                    ? `Reading ${ocr.current_file || 'scanned pages'}…`
                    : 'Scanned pages are queued and will be read in the background.'}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Add to Memory */}
      <div
        className="glass-card"
        onDragOver={(e) => { e.preventDefault(); e.stopPropagation() }}
        onDrop={(e) => {
          e.preventDefault()
          e.stopPropagation()
          const file = e.dataTransfer.files?.[0] as File & { path?: string }
          if (file?.path) {
            // Electron/Tauri exposes absolute paths via the non-standard .path property
            setFolderPath(file.path)
          } else {
            // Web fallback: alert user since browsers hide absolute paths for security
            alert("Drag-and-drop folder paths are only fully supported in the desktop app. Please use the 'Browse' button.")
          }
        }}
      >
        <h2 className="font-bold [font-stretch:80%] text-lg mb-4 text-text-primary">
          Add to Memory
        </h2>
        <div className="flex gap-3">
          {/* Bounded in ink3 like the ask field; the global ink ring shows focus. */}
          <input
            type="text"
            value={folderPath}
            onChange={(e) => setFolderPath(e.target.value)}
            placeholder="Select or drag a folder here..."
            className="flex-1 min-w-0 h-10 bg-background border border-edge px-4 text-text-primary placeholder:text-text-tertiary"
          />
          <Button onClick={handleBrowse}>
            Browse
          </Button>
          {/* The one plate on this screen. */}
          <Button
            variant="plate"
            onClick={handleIndex}
            disabled={!folderPath.trim() || isRunning}
            icon={<span aria-hidden>▶</span>}
            className={isRunning ? 'hidden' : ''}
          >
            Index
          </Button>
          {isRunning && (
            <Button variant="danger" onClick={handleCancel} disabled={cancelling}>
              {cancelling ? 'Cancelling…' : 'Cancel'}
            </Button>
          )}
        </div>
      </div>

      {/* The library as one roll of film: height is size, brightness is use. */}
      {strip.files.length > 0 && (
        <section className="flex flex-col gap-4" aria-labelledby="library-roll">
          <h2 id="library-roll" className="font-bold [font-stretch:80%] text-lg m-0 text-text-primary">Indexed files</h2>
          <FilmStrip files={strip.files} groups={strip.groups} note={strip.note} />
        </section>
      )}

      {/* System Drives */}
      {sysInfo?.volumes && sysInfo.volumes.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {sysInfo.volumes.map((vol) => {
            const usedPct = vol.total_gb > 0 ? Math.round((vol.used_gb / vol.total_gb) * 100) : 0
            let colorClass = 'bg-primary'
            if (usedPct > 90) colorClass = 'bg-error'
            else if (usedPct > 70) colorClass = 'bg-warning'

            return (
              <div key={vol.letter} className="glass-card flex items-center gap-4">
                <div className="flex-1 min-w-0">
                  <span className="stock text-[28px] leading-none text-text-primary">{vol.letter}</span>
                  <p className="text-text-secondary text-sm">
                    {vol.used_gb} / {vol.total_gb} GB used ({usedPct}%)
                  </p>
                  <div className="w-full bg-surface border border-edge h-1.5 mt-2">
                    <div
                      className={`h-1.5 ${colorClass}`}
                      style={{ width: `${usedPct}%` }}
                    />
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Server Info */}
      <div className="glass-card flex flex-wrap items-center justify-between p-4 mt-8">
        <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs">
          <span className="flex items-center gap-1.5">
            <span className="font-bold opacity-60 uppercase tracking-tighter text-primary-light">Version</span>
            <span className="font-mono font-bold text-text-primary">{health?.version ?? '—'}</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="font-bold opacity-60 uppercase tracking-tighter text-primary-light">DB</span>
            <span className="font-mono font-bold text-text-primary">{health?.db ?? '—'}</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="font-bold opacity-60 uppercase tracking-tighter text-primary-light">OS</span>
            <span className="font-mono font-bold text-text-primary">{sysInfo?.os ?? '—'}</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="font-bold opacity-60 uppercase tracking-tighter text-primary-light">Admin</span>
            <span className={`font-mono font-bold ${sysInfo?.is_admin ? 'text-success' : 'text-warning'}`}>{sysInfo?.is_admin ? 'Yes' : 'No'}</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="font-bold opacity-60 uppercase tracking-tighter text-primary-light">Scan</span>
            <span className="font-mono font-bold text-text-primary">{sysInfo?.scan_method ?? '—'}</span>
          </span>
        </div>
        <div className="flex gap-2 pl-4 ml-auto border-l border-rule">
          <Button size="sm" onClick={handleDemo} disabled={isRunning} icon={<span aria-hidden>▶</span>}>
            Seed Demo
          </Button>
          <Button size="sm" variant="danger" onClick={handleClear} disabled={isRunning || clearIndexMutation.isPending}>
            {clearIndexMutation.isPending ? 'Clearing…' : 'Clear Index'}
          </Button>
        </div>
      </div>
    </div>
  )
}
