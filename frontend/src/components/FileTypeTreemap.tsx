import { useMemo, useCallback, useRef, useState, useEffect } from 'react'
import { ChevronLeft, Home, File, Folder, Layers, Trash2 } from 'lucide-react'
import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { TreemapChart as EChartsTreemap } from 'echarts/charts'
import { TooltipComponent, VisualMapComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { type FileEntry } from '../api'
import {
  formatBytes,
  normalizePath,
  findCommonPrefix,
  buildTypeTree,
  buildFolderTree
} from '../utils/treeBuilder'

echarts.use([EChartsTreemap, TooltipComponent, VisualMapComponent, CanvasRenderer])

export interface FileTypeTreemapProps {
  readonly allFiles: Record<string, FileEntry[]>
  readonly activeFilter?: string | null
  readonly onFilterChange?: (ext: string | null) => void
  readonly onFileSelect?: (file: FileEntry) => void
  readonly onDeleteFolder?: (path: string) => void
  readonly initialMode?: 'folder' | 'type'
}

interface NavSegment {
  name: string
  fullPath: string | null
}

interface BreadcrumbProps {
  navPath: NavSegment[];
  onBreadcrumbClick: (index: number) => void;
}

const Breadcrumb: React.FC<BreadcrumbProps> = ({ navPath, onBreadcrumbClick }) => (
  <div className="flex items-center gap-1 bg-black/5 px-3 py-2 rounded-xl border border-white/10 overflow-x-auto no-scrollbar scroll-smooth">
    {navPath.map((seg, i) => {
      const isLast = i === navPath.length - 1;
      const isFile = isLast && !seg.fullPath;
      let Icon = Folder;
      if (i === 0) Icon = Home;
      else if (isFile) Icon = File;

      const itemKey = seg.fullPath ? `${seg.fullPath}-${i}` : `${seg.name}-${i}`;
      return (
        <div key={itemKey} className="flex items-center shrink-0">
          <button
            onClick={() => onBreadcrumbClick(i)}
            className={`flex items-center gap-1.5 px-2 py-1 rounded-lg text-[11px] font-medium transition-all hover:bg-black/5 ${isLast ? 'text-primary bg-primary/10' : 'text-text-secondary hover:text-text-primary'}`}
          >
            <Icon className="w-3 h-3" />
            <span className="max-w-[120px] truncate">{seg.name}</span>
          </button>
          {!isLast && <span className="text-text-secondary/20 mx-0.5">/</span>}
        </div>
      );
    })}
  </div>
);

export function FileTypeTreemap({ allFiles, activeFilter, onFilterChange, onFileSelect, onDeleteFolder, initialMode = 'folder' }: FileTypeTreemapProps) {
  const chartRef = useRef<ReactEChartsCore>(null)
  const [groupMode, setGroupMode] = useState<'folder' | 'type'>(initialMode)

  // Dynamic Root Label
  const rootLabel = useMemo(() => {
    if (groupMode === 'type') return 'File Types'
    const flat = Object.values(allFiles).flat()
    if (flat.length === 0) return 'Root'
    const paths = flat.map(f => normalizePath(f.path))
    const prefix = findCommonPrefix(paths)
    return prefix.split('/').pop() || 'Root'
  }, [allFiles, groupMode])

  const [navPath, setNavPath] = useState<NavSegment[]>([{ name: rootLabel, fullPath: null }])

  useEffect(() => {
    setNavPath([{ name: rootLabel, fullPath: null }])
  }, [rootLabel])

  /* ── Build Data Logic ──────────────────────────────────── */
  const { treeData, totalSize, buildError } = useMemo(() => {
    try {
      const flatFiles = Object.values(allFiles).flat()
      const total = flatFiles.reduce((s, f) => s + f.size, 0)
      const getVal = (s: number) => Math.sqrt(s + 1) * 10

      const data = groupMode === 'type'
        ? buildTypeTree(flatFiles, getVal)
        : [buildFolderTree(flatFiles, getVal)]

      return { treeData: data, totalSize: total, buildError: null }
    } catch (err) {
      console.error("[Treemap] Failed to build tree structure:", err)
      return { treeData: [], totalSize: 0, buildError: "Dataset too large or malformed." }
    }
  }, [allFiles, groupMode])

  /* ── Navigation ────────────────────────────────────────── */
  const handleHome = useCallback(() => {
    chartRef.current?.getEchartsInstance().dispatchAction({ type: 'treemapRootToNode', targetNode: null })
    setNavPath([{ name: rootLabel, fullPath: null }])
  }, [rootLabel])

  const handleBack = useCallback(() => {
    const instance = chartRef.current?.getEchartsInstance()
    if (!instance) return

    // Optimized Navigation: Use navPath state for safer back tracking
    if (navPath.length > 1) {
      const parentIdx = navPath.length - 2
      const parentNode = navPath[parentIdx]
      // Use name as target node for ECharts zoom action
      instance.dispatchAction({ type: 'treemapRootToNode', targetNode: parentNode.name })
      setNavPath(prev => prev.slice(0, -1))
    } else {
      handleHome()
    }
  }, [handleHome, navPath])

  const handleBreadcrumbClick = useCallback((index: number) => {
    const instance = chartRef.current?.getEchartsInstance()
    if (!instance) return
    if (index === 0) { handleHome(); return }
    instance.dispatchAction({ type: 'treemapRootToNode', targetNode: navPath[index].name })
    setNavPath(prev => prev.slice(0, index + 1))
  }, [navPath, handleHome])

  const handleDeleteCurrent = useCallback(() => {
    const current = navPath.at(-1)
    if (onDeleteFolder && current?.fullPath && confirm(`Remove index for all files in "${current.name}"?\n\nPath: ${current.fullPath}`)) {
      onDeleteFolder(current.fullPath)
    }
  }, [onDeleteFolder, navPath])

  const option = useMemo(() => ({
    backgroundColor: 'transparent',
    tooltip: {
      backgroundColor: 'rgba(255, 255, 255, 0.95)',
      borderColor: 'rgba(149, 159, 147, 0.2)',
      textStyle: { color: '#1e293b' },
      extraCssText: 'box-shadow: 0 10px 30px rgba(0,0,0,0.1); border-radius: 12px; backdrop-filter: blur(8px);',
      formatter: (info: any) => {
        const size = info.data?.realSize ?? info.value
        const pct = totalSize > 0 ? ((size / totalSize) * 100).toFixed(1) : '0.0'
        const escapeHtml = (u: string) => u.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll("\"", "&quot;").replaceAll("'", "&#039;");
        return `<div style="font-weight:700;margin-bottom:4px;color:#3d15cb">${escapeHtml(info.name)}</div>Size: <b>${formatBytes(size)}</b> (${pct}%)`
      }
    },
    animation: true,
    animationDurationUpdate: 450,
    animationEasing: 'cubicInOut' as const,
    series: [{
      type: 'treemap',
      data: treeData,
      width: '100%',
      height: '100%',
      roam: true,
      nodeClick: 'zoomToNode',
      breadcrumb: { show: false },
      leafDepth: undefined,
      visibleMinSize: 10,
      label: { show: true, formatter: '{b}', color: '#1e293b', fontSize: 10 },
      upperLabel: {
        show: true,
        height: 22,
        color: '#3d15cb',
        fontSize: 11,
        fontWeight: 'bold',
        backgroundColor: 'rgba(255,255,255,0.7)',
        formatter: (params: any) => {
          const size = params.data?.realSize
          return size == null ? ` ${params.name}` : `\u{1F4C1} ${params.name} (${formatBytes(size)})`
        }
      },
      itemStyle: { borderColor: '#f1f5e0', borderWidth: 1, gapWidth: 1 },
      levels: [
        {
          itemStyle: { color: '#f8fbf0', borderColor: '#3d15cb', borderWidth: 3, gapWidth: 3 },
          upperLabel: { show: true, height: 26, backgroundColor: 'rgba(255,255,255,0.8)', color: '#3d15cb', fontWeight: 'bold', fontSize: 12 }
        },
        {
          itemStyle: { color: '#fdfdfd', borderColor: '#3d15cb', borderWidth: 3, gapWidth: 3 },
          upperLabel: { show: true, height: 24, backgroundColor: 'rgba(255,255,255,0.7)', color: '#3d15cb', fontWeight: 'bold', fontSize: 11 }
        },
        {
          itemStyle: { color: '#ffffff', borderColor: '#9984d4', borderWidth: 2, gapWidth: 2 },
          upperLabel: {
            show: true,
            height: 22,
            backgroundColor: 'rgba(255,255,255,0.6)',
            color: '#3d15cb',
            fontWeight: 'bold',
            fontSize: 10,
            formatter: (params: any) => {
              const name = params.name;
              const isActive = activeFilter && name.toLowerCase() === activeFilter.toLowerCase();
              return isActive ? `\u{2728} ${name} (FILTERED)` : ` ${name}`;
            }
          }
        },
        {
          itemStyle: { color: '#ffffff', borderColor: '#9984d4', borderWidth: 1.5, gapWidth: 1.5 },
          upperLabel: { show: true, height: 20, backgroundColor: 'rgba(255,255,255,0.5)', color: '#3d15cb', fontSize: 10 }
        },
        {
          itemStyle: { borderColor: 'rgba(149,159,147,0.2)', borderWidth: 1, gapWidth: 0 },
          label: { show: true, position: 'inside', fontSize: 9, color: '#1e293b', formatter: (p: any) => p.value > 800 ? p.name : '' }
        }
      ]
    }]
  }), [treeData, totalSize, activeFilter])

  const onEvents = useMemo(() => ({
    click: (params: any) => {
      if (params.data?.children && chartRef.current) {
        const instance = chartRef.current.getEchartsInstance();
        instance.dispatchAction({ type: 'highlight', seriesIndex: 0, dataIndex: params.dataIndex });
        setTimeout(() => instance.dispatchAction({ type: 'downplay', seriesIndex: 0, dataIndex: params.dataIndex }), 1000);

        const pathInfo = params.treePathInfo || [];
        setNavPath(pathInfo.map((p: any) => ({ name: p.name, fullPath: p.data?.fullPath || null })).filter((p: any) => p.name !== ''));
      }

      if (params.data?.fileData && onFileSelect) onFileSelect(params.data.fileData)

      if (onFilterChange) {
        const extNode = (params.treePathInfo || []).find((p: any) => p.name?.startsWith('.'));
        if (extNode) onFilterChange(extNode.name === activeFilter ? null : extNode.name);
      }
    },
    contextmenu: (params: any) => { params.event.stop(); handleBack() }
  }), [handleBack, onFilterChange, onFileSelect, activeFilter])

  useEffect(() => {
    if (activeFilter) chartRef.current?.getEchartsInstance().dispatchAction({ type: 'highlight', seriesIndex: 0, name: activeFilter });
  }, [activeFilter, treeData]);

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="flex flex-col gap-3 glass p-3 rounded-2xl border border-white/30 shadow-inner mb-4 shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <button onClick={handleBack} disabled={navPath.length <= 1} className="flex items-center gap-1 px-3 py-1.5 bg-black/5 hover:bg-primary/10 border border-white/20 rounded-xl text-xs font-bold transition-all disabled:opacity-20 text-text-primary"><ChevronLeft className="w-4 h-4" /> BACK</button>
            <button onClick={handleHome} className="flex items-center gap-1 px-3 py-1.5 bg-black/5 hover:bg-primary/10 border border-white/20 rounded-xl text-xs font-bold transition-all text-text-primary"><Home className="w-4 h-4" /> HOME</button>
          </div>
          <div className="flex items-center gap-3">
            {onDeleteFolder && navPath.length > 1 && navPath.at(-1)?.fullPath && (
              <button onClick={handleDeleteCurrent} className="flex items-center gap-1 px-3 py-1.5 bg-error/10 hover:bg-error/20 border border-error/20 text-error rounded-xl text-[10px] font-bold transition-all"><Trash2 className="w-3.5 h-3.5" /> DELETE FOLDER INDEX</button>
            )}
            <div className="flex items-center bg-black/5 p-1 rounded-xl border border-white/20">
              <button onClick={() => { setGroupMode('folder'); handleHome() }} className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold transition-all ${groupMode === 'folder' ? 'bg-primary text-white shadow-lg' : 'text-text-secondary hover:text-text-primary'}`}><Folder className="w-3.5 h-3.5" /> BY FOLDERS</button>
              <button onClick={() => { setGroupMode('type'); handleHome() }} className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold transition-all ${groupMode === 'type' ? 'bg-primary text-white shadow-lg' : 'text-text-secondary hover:text-text-primary'}`}><Layers className="w-3.5 h-3.5" /> BY FILE TYPE</button>
            </div>
          </div>
        </div>
        <Breadcrumb navPath={navPath} onBreadcrumbClick={handleBreadcrumbClick} />
      </div>
      <div className="flex-1 relative rounded-2xl overflow-hidden border border-white/40 shadow-xl bg-white/30 group">
        <div className="absolute top-12 right-4 z-10 pointer-events-none opacity-0 group-hover:opacity-60 transition-opacity text-[10px] font-bold text-text-primary uppercase bg-white/80 border border-white/40 px-3 py-1.5 rounded-full shadow-sm">Right-click: Back • Scroll: Zoom • Drag: Pan</div>
        {buildError ? (
          <div className="flex items-center justify-center h-full text-text-secondary font-medium">{buildError}</div>
        ) : (
          <ReactEChartsCore ref={chartRef} echarts={echarts} option={option} style={{ height: '100%', width: '100%' }} onEvents={onEvents} />
        )}
      </div>
    </div>
  )
}
