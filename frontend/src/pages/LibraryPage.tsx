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
  compactDatabase,
  getCompactStatus,
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

  // Poll for compaction status
  const { data: compactStatus, refetch: refetchCompactStatus } = useApi(getCompactStatus, {
    cacheKey: 'compact-status',
    refetchInterval: 3000,
  })
  const isCompacting = compactStatus?.is_running ?? false

  // SSE progress stream while indexing
  useEffect(() => {
    if (!indexing) return
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
  }, [indexing, refetchHealth, refetchStatus])

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

  const handleCompact = useCallback(async () => {
    if (isCompacting) return
    if (!confirm('This will background vacuum the SQLite database to reclaim space. Continue?')) return
    try {
      const res = await compactDatabase()
      setMessage({ type: 'ok', text: res.message })
      refetchCompactStatus()
    } catch (e) {
      setMessage({ type: 'err', text: e instanceof Error ? e.message : 'Compaction request failed' })
    }
  }, [isCompacting, refetchCompactStatus])

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
    } catch (e) {
      console.error('Failed to clear backend caches:', e)
      // Fallback to normal refresh even if cache clear fails
      invalidateCache()
      refetchHealth()
      refetchStatus()
    }
  }, [refetchHealth, refetchStatus])

  const filesIndexed = status?.files_indexed ?? 0
  const chunksIndexed = status?.chunks_indexed ?? 0
  const scanStatus = indexing ? 'Indexing…' : (status?.status === 'running' ? 'Indexing…' : 'Idle')
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
          <p className="text-text-secondary mt-1">
            Manage your indexed files and memory sources
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={handleDemo} className="btn bg-accent/20 text-accent hover:bg-accent/30 rounded-xl px-4 text-sm">
            Demo
          </button>
          <button 
            onClick={handleCompact} 
            disabled={isCompacting}
            className="btn bg-surface-lighter text-text-secondary hover:text-text-primary rounded-xl px-4 text-sm gap-1 disabled:opacity-50"
          >
            {isCompacting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <HardDrive className="w-3.5 h-3.5" />}
            {isCompacting ? 'Compacting…' : 'Compact'}
          </button>
          <button onClick={handleClear} className="btn bg-error/20 text-error hover:bg-error/30 rounded-xl px-4 text-sm gap-1">
            <Trash2 className="w-3.5 h-3.5" /> Clear
          </button>
          <button onClick={handleRefresh} className="btn bg-primary hover:bg-primary-dark text-white gap-2 rounded-xl">
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
        </div>
      </div>

      {/* Message banner */}
      {message && (
        <div className={`flex items-center gap-2 px-4 py-3 rounded-xl text-sm ${message.type === 'ok' ? 'bg-success/20 text-success' : 'bg-error/20 text-error'}`}>
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
          <div key={label} className="glass-card flex flex-col items-center justify-center py-8">
            <span className={`text-3xl font-bold ${color}`}>{value}</span>
            <span className="text-text-secondary text-sm mt-1">{label}</span>
          </div>
        ))}
      </div>

      {/* Indexing progress bar */}
      {indexing && liveProgress && (
        <div className="glass-card">
          <div className="flex items-center gap-3 mb-2">
            <Loader2 className="w-5 h-5 text-primary animate-spin" />
            <span className="text-sm text-text-secondary truncate">
              {liveProgress.current_file || 'Processing…'} ({liveProgress.processed_files}/{liveProgress.total_files})
            </span>
          </div>
          <div className="w-full bg-surface-lighter rounded-full h-2.5">
            <div
              className="bg-primary h-2.5 rounded-full transition-all duration-300"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      )}

      {/* Add to Memory */}
      <div className="glass-card">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <FolderPlus className="w-5 h-5 text-primary" />
          Add to Memory
        </h2>
        <div className="flex gap-3">
          <input
            type="text"
            value={folderPath}
            onChange={(e) => setFolderPath(e.target.value)}
            placeholder="Select or type a folder path to index..."
            className="flex-1 bg-surface-lighter border border-primary/20 rounded-xl px-4 py-3 text-text-primary placeholder:text-text-secondary focus:outline-none focus:ring-2 focus:ring-primary/40"
          />
          <button onClick={handleBrowse} className="btn bg-primary/20 text-primary-light hover:bg-primary/30 rounded-xl px-6">
            Browse
          </button>
          <button
            onClick={handleIndex}
            disabled={!folderPath.trim() || indexing}
            className="btn bg-primary hover:bg-primary-dark text-white rounded-xl px-6 gap-2 disabled:opacity-40"
          >
            {indexing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
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
                <HardDrive className="w-8 h-8 text-primary shrink-0" />
                <div className="flex-1 min-w-0">
                  <span className="text-lg font-semibold">{vol.letter}</span>
                  <p className="text-text-secondary text-sm">
                    {vol.used_gb} / {vol.total_gb} GB used ({usedPct}%)
                  </p>
                  <div className="w-full bg-surface-lighter rounded-full h-1.5 mt-1">
                    <div
                      className={`h-1.5 rounded-full ${usedPct > 90 ? 'bg-error' : usedPct > 70 ? 'bg-warning' : 'bg-primary'}`}
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
      <div className="glass-card text-xs text-text-secondary flex flex-wrap gap-x-6 gap-y-1">
        <span>Version: {health?.version ?? '—'}</span>
        <span>DB: {health?.db ?? '—'}</span>
        <span>OS: {sysInfo?.os ?? '—'}</span>
        <span>Admin: {sysInfo?.is_admin ? 'Yes' : 'No'}</span>
        <span>Scan: {sysInfo?.scan_method ?? '—'}</span>
      </div>
    </div>
  )
}
