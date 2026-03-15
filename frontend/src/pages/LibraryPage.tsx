import { useState, useCallback, useEffect } from 'react'
import { BookOpen, HardDrive, FolderPlus, RefreshCw, Loader2, CheckCircle2, AlertCircle, Play, Trash2 } from 'lucide-react'
import { useApi, invalidateCache } from '../useApi'
import {
  getHealth,
  getIndexStatus,
  getSystemInfo,
  pickFolder,
  startIndexing,
  clearIndex,
  seedDemo,
  subscribeProgress,
  clearBackendCaches,
  type IndexStatus,
} from '../api'

export function LibraryPage() {
  const { data: health, refetch: refetchHealth } = useApi(getHealth, { cacheKey: 'health', refetchInterval: 10_000 })
  const { data: status, refetch: refetchStatus } = useApi(getIndexStatus, { cacheKey: 'index-status' })
  const { data: sysInfo } = useApi(getSystemInfo, { cacheKey: 'system-info' })

  const [folderPath, setFolderPath] = useState('')
  const [indexing, setIndexing] = useState(false)
  const [liveProgress, setLiveProgress] = useState<(IndexStatus & { current_file: string }) | null>(null)
  const [message, setMessage] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)

  // Derive running state from BOTH local flag and polled backend status
  const isRunning = indexing || status?.status === 'running'

  // Sync local indexing flag from backend status on page load/poll
  useEffect(() => {
    if (status?.status === 'running' && !indexing) {
      setIndexing(true)
    }
  }, [status?.status, indexing])

  // SSE progress stream while indexing (driven by isRunning, survives reload)
  useEffect(() => {
    if (!isRunning) return
    const unsub = subscribeProgress((data) => {
      setLiveProgress(data)
      if (data.status !== 'running') {
        setIndexing(false)
        setLiveProgress(null)
        invalidateCache()
        refetchHealth()
        refetchStatus()
        setMessage({ type: 'ok', text: `Indexing complete — ${data.processed_files} files processed` })
      }
    })
    return unsub
  }, [isRunning, refetchHealth, refetchStatus])

  const handleBrowse = useCallback(async () => {
    try {
      const { path } = await pickFolder()
      if (path) setFolderPath(path)
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

  const handleClear = useCallback(async () => {
    if (!confirm('This will permanently delete ALL indexed data. Continue?')) return
    try {
      await clearIndex()
      invalidateCache()
      refetchHealth()
      refetchStatus()
      setMessage({ type: 'ok', text: 'All indexed data cleared' })
    } catch (e) {
      setMessage({ type: 'err', text: e instanceof Error ? e.message : 'Clear failed' })
    }
  }, [refetchHealth, refetchStatus])



  const handleDemo = useCallback(async () => {
    try {
      const res = await seedDemo()
      setIndexing(true)
      setMessage({ type: 'ok', text: res.message })
    } catch (e) {
      setMessage({ type: 'err', text: e instanceof Error ? e.message : 'Demo seed failed' })
    }
  }, [])

  const handleRefresh = useCallback(async () => {
    try {
      await clearBackendCaches()
      invalidateCache()
      refetchHealth()
      refetchStatus()
      setMessage({ type: 'ok', text: 'Data refreshed successfully' })
    } catch (e) {
      console.error('Failed to clear backend caches:', e)
      invalidateCache()
      refetchHealth()
      refetchStatus()
      setMessage({ type: 'ok', text: 'Local data refreshed' })
    }
  }, [refetchHealth, refetchStatus])

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
          <button onClick={handleRefresh} className="glass-button !bg-primary !border-primary !text-white hover:!bg-primary-h hover:!text-white !py-2 gap-2 shadow-lg transition-all duration-200">
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
          { label: 'Model', value: health?.model_ready ? 'Ready' : 'Loading…', color: health?.model_ready ? 'text-success' : 'text-warning' },
        ].map(({ label, value, color }) => (
          <div key={label} className="glass-card flex flex-col items-center justify-center py-6 px-4">
            <span className={`text-3xl font-bold ${color}`}>{value}</span>
            <span className="text-text-secondary text-xs mt-1 uppercase tracking-wider font-semibold">{label}</span>
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

      {/* Add to Memory */}
      <div className="glass-card">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2 text-text-primary">
          <FolderPlus className="w-5 h-5 text-primary" />
          Add to Memory
        </h2>
        <div className="flex gap-3">
          <input
            type="text"
            value={folderPath}
            onChange={(e) => setFolderPath(e.target.value)}
            placeholder="Select or type a folder path to index..."
            className="flex-1 bg-white/40 border border-primary/20 rounded-xl px-4 py-3 text-text-primary placeholder:text-text-secondary/50 focus:outline-none focus:ring-2 focus:ring-primary/40 shadow-inner"
          />
          <button onClick={handleBrowse} className="glass-button flex items-center gap-2">
            <HardDrive className="w-4 h-4" /> Browse
          </button>
          <button
            onClick={handleIndex}
            disabled={!folderPath.trim() || isRunning}
            className="btn bg-primary hover:bg-primary-dark text-white rounded-xl px-6 gap-2 disabled:opacity-40 shadow-lg"
          >
            {isRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            Index
          </button>
        </div>
      </div>

      {/* System Drives */}
      {sysInfo?.volumes && sysInfo.volumes.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {sysInfo.volumes.map((vol) => {
            const usedPct = vol.total_gb > 0 ? Math.round((vol.used_gb / vol.total_gb) * 100) : 0
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
                      className={`h-1.5 rounded-full ${usedPct > 90 ? 'bg-error' : (usedPct > 70 ? 'bg-warning' : 'bg-primary')}`}
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
      <div className="glass-card text-[10px] text-text-primary/90 flex flex-wrap items-center justify-between px-4 py-2 opacity-95 hover:opacity-100 transition-opacity duration-300 bg-white/10 border-white/20">
        <div className="flex flex-wrap gap-x-6 gap-y-1">
          <span className="flex gap-1.5">
            <span className="font-bold opacity-60 uppercase tracking-tighter text-primary-light">Version</span>
            <span className="font-mono font-bold">{health?.version ?? '—'}</span>
          </span>
          <span className="flex gap-1.5">
            <span className="font-bold opacity-60 uppercase tracking-tighter text-primary-light">DB</span>
            <span className="font-mono font-bold">{health?.db ?? '—'}</span>
          </span>
          <span className="flex gap-1.5">
            <span className="font-bold opacity-60 uppercase tracking-tighter text-primary-light">OS</span>
            <span className="font-mono font-bold">{sysInfo?.os ?? '—'}</span>
          </span>
          <span className="flex gap-1.5">
            <span className="font-bold opacity-60 uppercase tracking-tighter text-primary-light">Admin</span>
            <span className="font-mono font-bold text-success">{sysInfo?.is_admin ? 'Yes' : 'No'}</span>
          </span>
          <span className="flex gap-1.5">
            <span className="font-bold opacity-60 uppercase tracking-tighter text-primary-light">Scan</span>
            <span className="font-mono font-bold">{sysInfo?.scan_method ?? '—'}</span>
          </span>
        </div>
        <div className="flex gap-2 border-l border-white/10 pl-4 ml-auto">
          <button 
            onClick={handleDemo} 
            className="flex items-center gap-1.5 px-3 py-1 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 border border-emerald-500/20 transition-all font-black text-[9px] uppercase tracking-widest shadow-sm"
          >
            <Play className="w-2.5 h-2.5" />
            Seed Demo
          </button>
          <button 
            onClick={handleClear} 
            className="flex items-center gap-1.5 px-3 py-1 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-600 border border-red-500/20 transition-all font-black text-[9px] uppercase tracking-widest shadow-sm"
          >
            <Trash2 className="w-2.5 h-2.5" />
            Clear Index
          </button>
        </div>
      </div>
    </div>
  )
}
