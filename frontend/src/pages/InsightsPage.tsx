import { useMemo, useState, useCallback, useEffect, Suspense, lazy } from 'react'
import { useApi } from '../useApi'
import { getInsights, getInsightsByType, getFileTree } from '../api'
import { KnowledgePortrait } from '../components/KnowledgePortrait'
import { CACHE_KEYS } from '../cacheKeys'
import { Button, SkeletonText } from '../components/ui'

const WebGPUFallback = lazy(() => import('../components/WebGPUFallback').then(m => ({ default: m.WebGPUFallback })))
const FileTypeTreemap = lazy(() => import('../components/FileTypeTreemap').then(m => ({ default: m.FileTypeTreemap })))

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

export function InsightsPage() {
  const { data: insights, loading: insightsLoading, error } = useApi(getInsights, { cacheKey: CACHE_KEYS.insights })
  const { data: tree, loading: treeLoading } = useApi(getFileTree, { cacheKey: CACHE_KEYS.fileTree })
  const [typeFilter, setTypeFilter] = useState<string | null>(null)
  const [filteredTopFiles, setFilteredTopFiles] = useState<{ path: string; size: number }[]>([])
  const [filteredColdFiles, setFilteredColdFiles] = useState<{ path: string; usage_count?: number; size?: number }[]>([])
  const [filterLoading, setFilterLoading] = useState(false)
  const [filterError, setFilterError] = useState<string | null>(null)
  const [vizMode, setVizMode] = useState<'3d' | '2d'>('3d')

  const handleFilterChange = useCallback((ext: string | null) => {
    setTypeFilter(ext)
  }, [])

  // Fetch filtered files from backend when filter changes
  useEffect(() => {
    if (!typeFilter) {
      // No filter — show the default top/cold files from insights
      setFilteredTopFiles(insights?.top_files ?? [])
      setFilteredColdFiles(insights?.cold_files ?? [])
      setFilterError(null)
      return
    }

    let cancelled = false
    setFilterLoading(true)
    setFilterError(null)

    getInsightsByType(typeFilter)
      .then((res) => {
        if (cancelled) return
        setFilteredTopFiles(res.top_files ?? [])
        setFilteredColdFiles(res.cold_files ?? [])
      })
      .catch((err) => {
        if (cancelled) return
        setFilteredTopFiles([])
        setFilteredColdFiles([])
        setFilterError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setFilterLoading(false)
      })
    return () => { cancelled = true }
  }, [typeFilter, insights])

  const typeCount = useMemo(() => {
    if (!insights?.type_breakdown) return 0
    return Object.keys(insights.type_breakdown).length
  }, [insights])

  const indexedSize = insights ? formatBytes(insights.total_size_bytes) : '—'
  const databaseSize = insights ? formatBytes(insights.database_size_bytes) : '—'
  const fileCount = insights?.file_count ?? 0

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6 animate-fade-in-up custom-scrollbar">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-baseline gap-3">
            <span aria-hidden className="font-mono text-[10px] tracking-[.1em] text-text-tertiary">04</span>
            <h1 className="stock text-[28px] leading-none m-0">Insights</h1>
          </div>
          <p className="text-text-secondary mt-2 text-sm">
            Analytics and visualizations of your personal data
          </p>
        </div>
      </div>

      {error && (
        <div className="glass-card bg-error/10 text-error text-sm">{error}</div>
      )}

      {insightsLoading && !insights && (
        <div className="glass-card flex items-center justify-center py-16">
          <span className="edge-type text-text-tertiary" role="status">Loading insights…</span>
        </div>
      )}

      {insights && (
        <>
          {/* Summary figures: stock type on one ruled line, not stat cards.
              All in ink — the lamp and the fog mean something, and a figure
              being a figure is not it. */}
          <div className="grid grid-cols-2 md:grid-cols-5 border-y border-rule">
            {[
              { label: 'Total Files', value: fileCount.toLocaleString() },
              { label: 'Indexed Files Size', value: indexedSize },
              { label: 'Database Size', value: databaseSize },
              { label: 'File Types', value: typeCount.toString() },
              { label: 'Top Used', value: (insights?.top_files?.length ?? 0).toString() },
            ].map(({ label, value }) => (
              <div key={label} className="flex flex-col gap-1.5 py-4 px-4 border-r border-rule last:border-r-0 min-w-0">
                <span className="stock text-[28px] leading-none truncate">{value}</span>
                <span className="edge-type text-text-tertiary">{label}</span>
              </div>
            ))}
          </div>

          {/* Charts area */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* File Type Distribution — 3D Crystal / Treemap fallback (spans 2 cols) */}
            {/* The panel height must be DEFINITE. This page's root is a block box,
                so `flex-1` here was inert and the `h-full` it replaced resolved
                against an auto-sized grid row - leaving the canvas at its attribute
                size, which the ResizeObserver then fed back into itself. A pixel
                height breaks that loop at the source. 560px leaves ~442px for the
                canvas after the card's p-8 and the title row, comfortably clear of
                the 400px floor on the wrapper. */}
            <div className="glass-card lg:col-span-2 flex flex-col h-[560px] overflow-hidden">
              <div className="flex items-center justify-between mb-4 shrink-0">
                <h2 className="font-bold [font-stretch:80%] text-lg text-text-primary m-0">
                  File Type Hierarchy
                </h2>
                {/* Two views of one thing: the same segmented idiom as Explorer's. */}
                <div className="inline-flex border border-edge" role="group" aria-label="Visualization">
                  <button
                    onClick={() => setVizMode('3d')}
                    aria-pressed={vizMode === '3d'}
                    className={`h-8 px-3 font-mono text-xs tracking-widest uppercase transition-colors ${vizMode === '3d' ? 'bg-plate text-on-plate' : 'text-text-secondary hover:text-text-primary'}`}
                  >
                    3D crystal
                  </button>
                  <button
                    onClick={() => setVizMode('2d')}
                    aria-pressed={vizMode === '2d'}
                    className={`h-8 px-3 font-mono text-xs tracking-widest uppercase border-l border-edge transition-colors ${vizMode === '2d' ? 'bg-plate text-on-plate' : 'text-text-secondary hover:text-text-primary'}`}
                  >
                    2D treemap
                  </button>
                </div>
              </div>

              {/* Fix: Explicitly check if folders object has keys before rendering visualizations */}
              {tree?.folders && Object.keys(tree.folders).length > 0 ? (
                <div className="flex-1 min-h-0 flex flex-col relative">
                  <Suspense fallback={
                    <div className="flex-1 flex flex-col items-center justify-center">
                      <p className="edge-type text-text-tertiary" role="status">Loading visualization engine…</p>
                    </div>
                  }>
                    {vizMode === '3d' ? (
                      <WebGPUFallback
                        allFiles={tree.folders}
                        activeFilter={typeFilter}
                        onFilterChange={handleFilterChange}
                        initialMode="type"
                      />
                    ) : (
                      <FileTypeTreemap
                        allFiles={tree.folders}
                        activeFilter={typeFilter}
                        onFilterChange={handleFilterChange}
                        initialMode="type"
                      />
                    )}
                  </Suspense>
                </div>
              ) : (
                <div className="flex-1 flex flex-col items-center justify-center text-text-secondary text-sm bg-raised rounded-2xl border border-rule">
                  {treeLoading ? (
                    <p className="edge-type text-text-tertiary" role="status">Loading folder structure…</p>
                  ) : (
                    <div className="flex flex-col items-center gap-3 text-text-tertiary">
                      <p className="m-0">No file hierarchy data available.</p>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Top & Cold Files (filtered by treemap selection) */}
            <div className="glass-card space-y-6">
              {/* Filter Status Badge */}
              {typeFilter && (
                <div className="bg-raised border border-rule flex items-center justify-between p-3 shrink-0">
                  <span className="edge-type text-text-primary">{typeFilter} active</span>
                  <Button variant="quiet" size="sm" onClick={() => handleFilterChange(null)}>
                    Clear
                  </Button>
                </div>
              )}

              {/* Top Files */}
              <div>
                <h2 className="font-bold [font-stretch:80%] text-lg mb-3 text-text-primary">
                  Top Files
                </h2>
                {(() => {
                  if (filterLoading) {
                    return (
                      // The layout is known here, so show its shape rather than
                      // a spinner that only says "wait".
                      <div className="py-2"><SkeletonText lines={5} /></div>
                    )
                  }
                  if (filterError) {
                    return (
                      <div className="text-center py-8">
                        <p className="text-error text-sm font-medium">{filterError}</p>
                      </div>
                    )
                  }
                  if (filteredTopFiles.length > 0) {
                    return (
                      // A ruled register, not ten stacked cards. Cards in a
                      // narrow column read as a dashboard; this is an index.
                      <div>
                        {filteredTopFiles.slice(0, 10).map((f) => (
                          <div key={f.path} className="flex items-baseline justify-between gap-3 py-2 border-b border-rule last:border-b-0">
                            <span className="truncate text-[13px] text-text-primary">{f.path.split(/[\\/]/).pop()}</span>
                            <span className="font-mono text-xs text-primary shrink-0">{formatBytes(f.size)}</span>
                          </div>
                        ))}
                      </div>
                    )
                  }
                  return (
                    <div className="text-center py-8">
                      <p className="text-text-tertiary text-sm">
                        {typeFilter ? `No ${typeFilter} files found` : 'No files indexed yet'}
                      </p>
                    </div>
                  )
                })()}
              </div>

              {/* Cold Files */}
              {!filterLoading && filteredColdFiles.length > 0 && (
                <div>
                  <h2 className="font-bold [font-stretch:80%] text-lg mb-3 text-text-primary">
                    Cold Files
                  </h2>
                  <div>
                    {filteredColdFiles.slice(0, 8).map((f) => (
                      <div key={f.path} className="flex items-baseline justify-between gap-3 py-2 border-b border-rule last:border-b-0">
                        <span className="truncate text-[13px] text-text-primary">{f.path.split(/[\\/]/).pop()}</span>
                        <span className="font-mono text-xs text-info shrink-0">
                          {f.usage_count === undefined ? formatBytes(f.size || 0) : `${f.usage_count} hits`}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="mt-6">
            <KnowledgePortrait />
          </div>

          {/* Error notice */}
          {insights.error && (
            <div className="glass-card bg-warning/10 text-warning text-sm">
              Partial data — some statistics unavailable: {insights.error}
            </div>
          )}
        </>
      )}

      {/* Empty state when no insights at all */}
      {!insightsLoading && insights && fileCount === 0 && (
        <div className="glass-card text-center py-12">
          <p className="text-text-secondary">Index some files to generate insights about your personal data.</p>
        </div>
      )}
    </div>
  )
}