import { useState, useCallback, useEffect } from 'react'
import { useMutation } from '@tanstack/react-query'
import { BookOpen, HardDrive, FolderPlus, RefreshCw, Loader2, CheckCircle2, AlertCircle, Play, Trash2, ScanText } from 'lucide-react'
import { useApi, invalidateCorpusCaches } from '../useApi'
import {
  getHealth,
  getIndexStatus,
  getSystemInfo,
  getAppConfig,
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

export function LibraryPage() {
  const [folderPath, setFolderPath] = useState('')
  const [indexing, setIndexing] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [liveProgress, setLiveProgress] = useState<(IndexStatus & { current_file: string }) | null>(null)
  const [message, setMessage] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)

  // Pause /index/status polling while local "indexing" is true; SSE drives live progress.
  const { data: status, refetch: refetchStatus } = useApi(getIndexStatus, {
    cacheKey: CACHE_KEYS.indexStatus,
    refetchInterval: indexing ? 0 : 10_000,
  })
  const { data: sysInfo } = useApi(getSystemInfo, { cacheKey: CACHE_KEYS.systemInfo })
  const { data: config } = useApi(getAppConfig, { cacheKey: CACHE_KEYS.appConfig })

  // OCR runs after the index run finishes, so this keeps polling regardless of
  // indexing state. Without it "indexing complete" is a lie for scanned PDFs.
  const { data: ocr } = useApi(getOcrStatus, {
    cacheKey: CACHE_KEYS.ocrStatus,
    refetchInterval: 10_000,
  })

  // Derive running state from BOTH local flag and polled backend status
  const isRunning = indexing || status?.status === 'running'

  // Pause background polling while SSE stream is active
  const { data: health, refetch: refetchHealth } = useApi(getHealth, {
    cacheKey: CACHE_KEYS.health,
    refetchInterval: isRunning ? 0 : 10_000
  })

  // Sync local indexing flag from backend status on page load/poll
  useEffect(() => {
    if (!status?.status) return
    setIndexing(status.status === 'running')
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
        setMessage({ type: 'ok', text: `Indexing complete — ${data.processed_files} files processed` })
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

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6 animate-fade-in-up custom-scrollbar">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-3">
            <BookOpen className="w-7 h-7 text-primary" />
            Library
          </h1>
          <p className="text-text-secondary mt-1 text-sm">
            Manage your indexed files and memory sources
          </p>
        </div>
        <div className="flex gap-3">
          <button onClick={handleRefresh} disabled={refreshMutation.isPending} className="glass-button !bg-primary !border-primary !text-white hover:!bg-primary-h hover:!text-white !py-2 gap-2 shadow-lg transition-all duration-200 disabled:opacity-60">
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
        </div>
      </div>

      {/* Message banner */}
      {message && (
        <div className={`flex items-center gap-2 px-4 py-3 rounded-xl text-sm transition-all duration-300 ${message.type === 'ok' ? 'bg-success/20 text-success' : 'bg-error/20 text-error'}`}>
          {message.type === 'ok' ? <CheckCircle2 className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
          {message.text}
        </div>
      )}

      {/* Hero Metrics */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {[
          { label: 'Total Files', value: filesIndexed.toLocaleString(), color: 'text-primary-light' },
          { label: 'Chunks Indexed', value: chunksIndexed.toLocaleString(), color: 'text-accent' },
          { label: 'Scan Status', value: scanStatus, color: scanStatus === 'Idle' ? 'text-success' : 'text-warning' },
          { label: 'Model', value: health?.model_ready ? (config?.gemini_model || 'Ready') : 'Loading…', color: health?.model_ready ? 'text-success' : 'text-warning' },
        ].map(({ label, value, color }) => (
          <div key={label} className="glass-card flex flex-col items-center justify-center py-6 px-4">
            <span className={`text-xl md:text-2xl lg:text-3xl font-bold ${color} text-center break-words w-full px-2`}>{value}</span>
            <span className="text-text-secondary text-xs mt-1 uppercase tracking-wider font-semibold text-center">{label}</span>
          </div>
        ))}
      </div>

      {/* Indexing progress bar */}
      {isRunning && (
        <div className="glass-card">
          <div className="flex items-center gap-3 mb-2">
            <Loader2 className="w-5 h-5 text-primary animate-spin" />
            <span className="text-sm text-text-secondary truncate">
              {liveProgress?.current_file || 'Processing…'} ({liveProgress?.processed_files ?? status?.processed_files ?? 0}/{liveProgress?.total_files ?? status?.total_files ?? 0})
            </span>
          </div>
          <div className="w-full bg-white/40 border border-white/60 rounded-full h-2.5 shadow-inner">
            <div
              className="bg-primary h-2.5 rounded-full transition-all duration-300"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      )}

      {/* OCR backlog. Deliberately outside the isRunning guard: OCR is drained
          after the index run ends, so this has to survive run completion. */}
      {!!ocr?.pages_pending && (
        <div className="glass-card">
          <div className="flex items-center gap-3">
            {ocr.worker_running
              ? <Loader2 className="w-5 h-5 text-primary animate-spin shrink-0" />
              : <ScanText className="w-5 h-5 text-primary shrink-0" />}
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
        className="glass-card transition-all duration-200 border-2 border-transparent hover:border-primary/30"
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
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2 text-text-primary">
          <FolderPlus className="w-5 h-5 text-primary" />
          Add to Memory
        </h2>
        <div className="flex gap-3">
          <input
            type="text"
            value={folderPath}
            onChange={(e) => setFolderPath(e.target.value)}
            placeholder="Select or drag a folder here..."
            className="flex-1 bg-white/40 border border-primary/20 rounded-xl px-4 py-3 text-text-primary placeholder:text-text-secondary/50 focus:outline-none focus:ring-2 focus:ring-primary/40 shadow-inner"
          />
          <button onClick={handleBrowse} className="glass-button flex items-center gap-2">
            <HardDrive className="w-4 h-4" /> Browse
          </button>
          <button
            onClick={handleIndex}
            disabled={!folderPath.trim() || isRunning}
            className={`flex items-center justify-center gap-2 bg-primary hover:brightness-110 text-white font-bold rounded-xl px-6 py-3 shadow-lg transition-all active:scale-95 disabled:opacity-40 ${isRunning ? 'hidden' : ''}`}
          >
            {isRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            Index
          </button>
          {isRunning && (
            <button
              onClick={handleCancel}
              disabled={cancelling}
              className="flex items-center justify-center gap-2 bg-error hover:brightness-110 text-white font-bold rounded-xl px-6 py-3 shadow-lg transition-all active:scale-95 disabled:opacity-40"
            >
              {cancelling ? <Loader2 className="w-4 h-4 animate-spin" /> : <AlertCircle className="w-4 h-4" />}
              {cancelling ? 'Cancelling...' : 'Cancel'}
            </button>
          )}
        </div>
      </div>

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
                <div className="bg-primary/10 p-3 rounded-2xl border border-primary/20">
                  <HardDrive className="w-8 h-8 text-primary shrink-0" />
                </div>
                <div className="flex-1 min-w-0">
                  <span className="text-lg font-bold text-text-primary">{vol.letter}</span>
                  <p className="text-text-secondary text-sm">
                    {vol.used_gb} / {vol.total_gb} GB used ({usedPct}%)
                  </p>
                  <div className="w-full bg-white/40 border border-white/60 rounded-full h-1.5 mt-2 shadow-inner">
                    <div
                      className={`h-1.5 rounded-full ${colorClass}`}
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
        <div className="flex gap-2 pl-4 ml-auto border-l border-white/20">
          <button
            onClick={handleDemo}
            disabled={isRunning}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 border border-emerald-500/20 transition-all font-black text-[10px] uppercase tracking-widest shadow-sm disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <Play className="w-3 h-3" />
            Seed Demo
          </button>
          <button
            onClick={handleClear}
            disabled={isRunning || clearIndexMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-600 border border-red-500/20 transition-all font-black text-[10px] uppercase tracking-widest shadow-sm disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {clearIndexMutation.isPending
              ? <Loader2 className="w-3 h-3 animate-spin" />
              : <Trash2 className="w-3 h-3" />}
            {clearIndexMutation.isPending ? 'Clearing…' : 'Clear Index'}
          </button>
        </div>
      </div>
    </div>
  )
}
