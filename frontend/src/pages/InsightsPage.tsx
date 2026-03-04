import { useMemo, useState, useCallback, useEffect } from 'react'
import { BarChart3, PieChart, TrendingUp, FileType, Loader2, Flame, Snowflake, HardDrive } from 'lucide-react'
import { useApi } from '../useApi'
import { getInsights, getInsightsByType, getFileTree } from '../api'
import { FileTypeTreemap } from '../components/FileTypeTreemap'

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

export function InsightsPage() {
  const { data: insights, loading: insightsLoading, error } = useApi(getInsights, { cacheKey: 'insights', refetchInterval: 30_000 })
  const { data: tree, loading: treeLoading } = useApi(getFileTree, { cacheKey: 'file-tree' })
  const [typeFilter, setTypeFilter] = useState<string | null>(null)
  const [filteredTopFiles, setFilteredTopFiles] = useState<{ path: string; size: number }[]>([])
  const [filteredColdFiles, setFilteredColdFiles] = useState<{ path: string; usage_count?: number; size?: number }[]>([])
  const [filterLoading, setFilterLoading] = useState(false)

  const handleFilterChange = useCallback((ext: string | null) => {
    setTypeFilter(ext)
  }, [])

  // Fetch filtered files from backend when filter changes
  useEffect(() => {
    if (!typeFilter) {
      // No filter — show the default top/cold files from insights
      setFilteredTopFiles(insights?.top_files ?? [])
      setFilteredColdFiles(insights?.cold_files ?? [])
      return
    }

    let cancelled = false
    setFilterLoading(true)
    getInsightsByType(typeFilter)
      .then((res) => {
        if (cancelled) return
        setFilteredTopFiles(res.top_files ?? [])
        setFilteredColdFiles(res.cold_files ?? [])
      })
      .catch(() => {
        if (cancelled) return
        setFilteredTopFiles([])
        setFilteredColdFiles([])
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
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-3">
          <BarChart3 className="w-7 h-7 text-primary" />
          Insights
        </h1>
        <p className="text-text-secondary mt-1">
          Analytics and visualizations of your personal data
        </p>
      </div>

      {error && (
        <div className="glass-card bg-error/10 text-error text-sm">{error}</div>
      )}

      {insightsLoading && !insights && (
        <div className="glass-card flex items-center justify-center py-16">
          <Loader2 className="w-8 h-8 text-primary animate-spin" />
        </div>
      )}

      {insights && (
        <>
          {/* Summary Metrics */}
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
            {[
              { label: 'Total Files', value: fileCount.toLocaleString(), icon: FileType, color: 'text-primary-light' },
              { label: 'Indexed Files Size', value: indexedSize, icon: PieChart, color: 'text-accent' },
              { label: 'Database Size', value: databaseSize, icon: HardDrive, color: 'text-primary' },
              { label: 'File Types', value: typeCount.toString(), icon: TrendingUp, color: 'text-success' },
              { label: 'Top Used', value: (insights?.top_files?.length ?? 0).toString(), icon: BarChart3, color: 'text-warning' },
            ].map(({ label, value, icon: Icon, color }) => (
              <div key={label} className="glass-card flex flex-col items-center justify-center py-6 px-2">
                <Icon className={`w-6 h-6 ${color} mb-2`} />
                <span className={`text-xl font-bold ${color} text-center`}>{value}</span>
                <span className="text-text-secondary text-xs mt-1 text-center">{label}</span>
              </div>
            ))}
          </div>

          {/* Charts area */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* File Type Distribution — Treemap (spans 2 cols) */}
            <div className="glass-card lg:col-span-2 flex flex-col h-[650px]">
              <h2 className="text-lg font-semibold mb-4 text-primary-light shrink-0">File Type Hierarchy</h2>
              {tree?.folders ? (
                <FileTypeTreemap
                  allFiles={tree.folders}
                  activeFilter={typeFilter}
                  onFilterChange={handleFilterChange}
                  initialMode="type"
                />
              ) : (
                <div className="flex-1 flex items-center justify-center text-text-secondary text-sm">
                  {treeLoading ? <Loader2 className="animate-spin" /> : 'No data yet'}
                </div>
              )}
            </div>

            {/* Top & Cold Files (filtered by treemap selection) */}
            <div className="glass-card space-y-6">
              {/* Filter Status Badge */}
              {typeFilter && (
                <div className="bg-primary/10 border border-primary/20 rounded-xl flex items-center justify-between p-3 shrink-0 shadow-sm animate-fade-in-up">
                  <div className="flex items-center gap-3">
                    <FileType className="w-4 h-4 text-primary" />
                    <span className="text-xs font-bold text-white uppercase">{typeFilter} Active</span>
                  </div>
                  <button 
                    onClick={() => handleFilterChange(null)}
                    className="text-[9px] font-black bg-primary/20 hover:bg-primary/30 px-2 py-1 rounded transition-all"
                  >
                    CLEAR
                  </button>
                </div>
              )}

              {/* Top Files */}
              <div>
                <h2 className="text-lg font-semibold mb-3 flex items-center gap-2 text-text-primary">
                  <Flame className="w-5 h-5 text-warning" />
                  Top Files
                </h2>
                {filterLoading ? (
                  <div className="flex items-center justify-center py-12">
                    <Loader2 className="w-6 h-6 text-primary animate-spin" />
                  </div>
                ) : filteredTopFiles.length > 0 ? (
                  <div className="space-y-2">
                    {filteredTopFiles.slice(0, 10).map((f, i) => (
                      <div key={i} className="group flex items-center justify-between text-sm bg-white/5 hover:bg-white/10 rounded-xl px-4 py-3 transition-all border border-white/5">
                        <span className="truncate text-text-primary font-medium">{f.path.split(/[\\/]/).pop()}</span>
                        <span className="text-primary-light text-xs font-mono font-bold shrink-0 ml-2">{formatBytes(f.size)}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-center py-8 opacity-40">
                    <p className="text-text-secondary text-sm">
                      {typeFilter ? `No ${typeFilter} files found` : 'No files indexed yet'}
                    </p>
                  </div>
                )}
              </div>

              {/* Cold Files */}
              {!filterLoading && filteredColdFiles.length > 0 && (
                <div>
                  <h2 className="text-lg font-semibold mb-3 flex items-center gap-2 text-text-primary">
                    <Snowflake className="w-5 h-5 text-accent" />
                    Cold Files
                  </h2>
                  <div className="space-y-2">
                    {filteredColdFiles.slice(0, 8).map((f, i) => (
                      <div key={i} className="group flex items-center justify-between text-sm bg-white/5 hover:bg-white/10 rounded-xl px-4 py-3 transition-all border border-white/5">
                        <span className="truncate text-text-primary font-medium">{f.path.split(/[\\/]/).pop()}</span>
                        <span className="text-accent text-xs font-bold shrink-0 ml-2">
                          {f.usage_count !== undefined ? `${f.usage_count} hits` : formatBytes(f.size || 0)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
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
          <BarChart3 className="w-12 h-12 text-primary/20 mx-auto mb-4" />
          <p className="text-text-secondary">Index some files to generate insights about your personal data.</p>
        </div>
      )}
    </div>
  )
}
