import { useMemo, useCallback, useRef, useState, useEffect } from 'react'
import { useTheme } from '../theme'
import { AccessibleTree, type A11yNode } from './AccessibleTree'
import { ShortcutOverlay } from './ShortcutOverlay'
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

/**
 * ECharts paints to a canvas, so it cannot inherit a CSS variable — every
 * colour has to be handed over as a literal. This chart previously hardcoded
 * about twenty light-theme values (#ffffff grounds, #3d15cb labels), which
 * meant that in Cabinet — the DEFAULT theme — it rendered a near-white chart
 * inside a dark panel.
 *
 * Read at option-build time rather than at module scope: a module-level read
 * would snapshot whichever theme happened to be active on first import and
 * never update.
 */
function readTokens() {
  const cs = getComputedStyle(document.documentElement)
  const v = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback
  return {
    surface: v('--pma-surface', '#1C1815'),
    raised: v('--pma-raised', '#302A23'),
    bg: v('--pma-bg', '#14110E'),
    rule: v('--pma-rule', '#3E362D'),
    edge: v('--pma-edge', '#85765B'),
    text: v('--pma-text', '#F2EBDD'),
    text2: v('--pma-text-2', '#C4B79F'),
    accent: v('--pma-accent', '#C4A26B'),
    plate: v('--pma-plate', '#B08D57'),
  }
}

function prefersReducedMotion() {
  return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
}

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
  <div className="flex items-center gap-1 bg-raised px-3 py-2 rounded-xl border border-rule overflow-x-auto no-scrollbar scroll-smooth">
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
            className={`flex items-center gap-1.5 px-2 py-1 rounded-lg text-[11px] font-medium transition-colors hover:bg-raised ${isLast ? 'text-primary bg-primary/10' : 'text-text-secondary hover:text-text-primary'}`}
          >
            <Icon className="w-3 h-3" aria-hidden />
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
  const theme = useTheme()

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

  // No confirm() here any more. `onDeleteFolder` is ExplorerPage's
  // `handleDeleteFolder` — its only caller — and that now raises the sonner
  // action-toast itself, so asking here as well made the treemap path confirm
  // twice: a platform dialog, then a toast.
  /**
   * The children of whatever level the treemap is currently zoomed into.
   *
   * Walked from `treeData` by `navPath` rather than asked of ECharts: the
   * chart's zoom root is internal state with no read accessor, and the two are
   * kept in step by `navPath` already — the breadcrumb depends on it.
   */
  const currentLevel = useMemo<any[]>(() => {
    let level: any[] = groupMode === 'type' ? treeData : (treeData[0]?.children ?? [])
    for (let i = 1; i < navPath.length; i++) {
      const next = level.find(n => n.name === navPath[i].name)
      if (!next?.children?.length) return level
      level = next.children
    }
    return level
  }, [treeData, navPath, groupMode])

  const [cursor, setCursor] = useState(0)
  const [announcement, setAnnouncement] = useState('')
  const [shortcutsOpen, setShortcutsOpen] = useState(false)

  // Descending a level invalidates any previous position.
  useEffect(() => { setCursor(0) }, [navPath.length, groupMode])

  const cursorNode = currentLevel[Math.min(cursor, Math.max(0, currentLevel.length - 1))]

  // Mirror the cursor into the chart with the same highlight action the filter
  // sync already uses, so a sighted keyboard user can see where they are.
  useEffect(() => {
    if (!cursorNode) return
    const instance = chartRef.current?.getEchartsInstance()
    if (!instance) return
    instance.dispatchAction({ type: 'downplay', seriesIndex: 0 })
    instance.dispatchAction({ type: 'highlight', seriesIndex: 0, name: cursorNode.name })
  }, [cursorNode])

  /** What a screen reader hears for one node, with its place in the level. */
  const describeAt = useCallback((node: any, at: number, total: number) => {
    if (!node) return ''
    const kind = node.children?.length ? 'folder' : 'file'
    const size = node.realSize !== undefined ? `, ${formatBytes(node.realSize)}` : ''
    return `${node.name}, ${kind}${size}, ${at + 1} of ${total}`
  }, [])

  const enterNode = useCallback((node: any) => {
    if (!node) return
    // `navPath` is the source of truth for where we are; the chart dispatch is
    // the visual echo of it. Advancing state only when the chart instance
    // happens to exist would make the model depend on the view being ready.
    const instance = chartRef.current?.getEchartsInstance()
    if (node.children?.length) {
      instance?.dispatchAction({ type: 'treemapRootToNode', targetNode: node.name })
      setNavPath(prev => [...prev, { name: node.name, fullPath: node.fullPath ?? null }])
      setAnnouncement(`Entered ${node.name}, ${node.children.length} items`)
    } else if (node.fileData && onFileSelect) {
      onFileSelect(node.fileData)
      setAnnouncement(`Selected ${node.name}`)
    }
  }, [onFileSelect])

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLElement>) => {
    if (e.key === '?' || e.key === 'F1') {
      e.preventDefault()
      setShortcutsOpen(true)
      return
    }
    if (!currentLevel.length) return

    switch (e.key) {
      case 'ArrowDown':
      case 'ArrowRight': {
        e.preventDefault()
        const next = Math.min(cursor + 1, currentLevel.length - 1)
        if (next === cursor) { setAnnouncement('Last item'); return }
        setCursor(next)
        setAnnouncement(describeAt(currentLevel[next], next, currentLevel.length))
        return
      }
      case 'ArrowUp':
      case 'ArrowLeft': {
        e.preventDefault()
        const next = Math.max(cursor - 1, 0)
        if (next === cursor) { setAnnouncement('First item'); return }
        setCursor(next)
        setAnnouncement(describeAt(currentLevel[next], next, currentLevel.length))
        return
      }
      case 'Enter':
        e.preventDefault()
        enterNode(cursorNode)
        return
      case 'Backspace':
        e.preventDefault()
        handleBack()
        setAnnouncement('Up one level')
        return
      case 'Home':
        e.preventDefault()
        handleHome()
        setAnnouncement('Back to root')
        return
      default:
        return
    }
  }, [currentLevel, cursor, cursorNode, describeAt, enterNode, handleBack, handleHome])

  // Only the current level is materialised. The treemap shows one level at a
  // time, so a deeper mirror would describe something not on screen.
  const a11yNodes = useMemo<A11yNode[]>(() => currentLevel.map((n, i) => ({
    id: String(i),
    name: n.name,
    isFolder: !!n.children?.length,
    expanded: n.children?.length ? false : undefined,
    detail: n.realSize !== undefined ? formatBytes(n.realSize) : undefined,
  })), [currentLevel])

  const handleDeleteCurrent = useCallback(() => {
    const current = navPath.at(-1)
    if (onDeleteFolder && current?.fullPath) {
      onDeleteFolder(current.fullPath)
    }
  }, [onDeleteFolder, navPath])

  const option = useMemo(() => {
    const t = readTokens()
    const reduced = prefersReducedMotion()
    return {
    backgroundColor: 'transparent',
    tooltip: {
      backgroundColor: t.surface,
      borderColor: t.edge,
      textStyle: { color: t.text },
      extraCssText: 'box-shadow: 0 10px 30px rgba(0,0,0,0.35); border-radius: 12px;',
      formatter: (info: any) => {
        const size = info.data?.realSize ?? info.value
        const pct = totalSize > 0 ? ((size / totalSize) * 100).toFixed(1) : '0.0'
        const escapeHtml = (u: string) => u.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll("\"", "&quot;").replaceAll("'", "&#039;");
        return `<div style="font-weight:700;margin-bottom:4px;color:${t.accent}">${escapeHtml(info.name)}</div>Size: <b>${formatBytes(size)}</b> (${pct}%)`
      }
    },
    // ECharts drives its own animation loop, which the CSS reduced-motion
    // block in index.css cannot reach.
    animation: !reduced,
    animationDurationUpdate: reduced ? 0 : 450,
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
      label: { show: true, formatter: '{b}', color: t.text, fontSize: 10 },
      upperLabel: {
        show: true,
        height: 22,
        color: t.accent,
        fontSize: 11,
        fontWeight: 'bold',
        backgroundColor: t.bg,
        formatter: (params: any) => {
          const size = params.data?.realSize
          return size == null ? ` ${params.name}` : `\u{1F4C1} ${params.name} (${formatBytes(size)})`
        }
      },
      itemStyle: { borderColor: t.rule, borderWidth: 1, gapWidth: 1 },
      levels: [
        {
          itemStyle: { color: t.bg, borderColor: t.plate, borderWidth: 3, gapWidth: 3 },
          upperLabel: { show: true, height: 26, backgroundColor: t.bg, color: t.accent, fontWeight: 'bold', fontSize: 12 }
        },
        {
          itemStyle: { color: t.surface, borderColor: t.plate, borderWidth: 3, gapWidth: 3 },
          upperLabel: { show: true, height: 24, backgroundColor: t.surface, color: t.accent, fontWeight: 'bold', fontSize: 11 }
        },
        {
          itemStyle: { color: t.surface, borderColor: t.edge, borderWidth: 2, gapWidth: 2 },
          upperLabel: {
            show: true,
            height: 22,
            backgroundColor: t.surface,
            color: t.accent,
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
          itemStyle: { color: t.raised, borderColor: t.edge, borderWidth: 1.5, gapWidth: 1.5 },
          upperLabel: { show: true, height: 20, backgroundColor: t.raised, color: t.accent, fontSize: 10 }
        },
        {
          itemStyle: { borderColor: t.rule, borderWidth: 1, gapWidth: 0 },
          label: { show: true, position: 'inside', fontSize: 9, color: t.text2, formatter: (p: any) => p.value > 800 ? p.name : '' }
        }
      ]
    }]
    }
  }, [treeData, totalSize, activeFilter, theme])

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
      <div className="flex flex-col gap-3 glass p-3 rounded-2xl border border-edge shadow-inner mb-4 shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <button onClick={handleBack} disabled={navPath.length <= 1} className="flex items-center gap-1 px-3 py-1.5 bg-raised hover:bg-primary/10 border border-rule rounded-xl text-xs font-bold transition-colors disabled:opacity-20 text-text-primary"><ChevronLeft className="w-4 h-4" aria-hidden /> BACK</button>
            <button onClick={handleHome} className="flex items-center gap-1 px-3 py-1.5 bg-raised hover:bg-primary/10 border border-rule rounded-xl text-xs font-bold transition-colors text-text-primary"><Home className="w-4 h-4" aria-hidden /> HOME</button>
          </div>
          <div className="flex items-center gap-3">
            {onDeleteFolder && navPath.length > 1 && navPath.at(-1)?.fullPath && (
              <button onClick={handleDeleteCurrent} className="flex items-center gap-1 px-3 py-1.5 bg-error/10 hover:bg-error/20 border border-error/20 text-error rounded-xl text-[10px] font-bold transition-colors"><Trash2 className="w-3.5 h-3.5" aria-hidden /> DELETE FOLDER INDEX</button>
            )}
            <div className="flex items-center bg-raised p-1 rounded-xl border border-rule">
              <button onClick={() => { setGroupMode('folder'); handleHome() }} className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold transition-colors ${groupMode === 'folder' ? 'bg-plate text-on-plate shadow-lg' : 'text-text-secondary hover:text-text-primary'}`}><Folder className="w-3.5 h-3.5" aria-hidden /> BY FOLDERS</button>
              <button onClick={() => { setGroupMode('type'); handleHome() }} className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold transition-colors ${groupMode === 'type' ? 'bg-plate text-on-plate shadow-lg' : 'text-text-secondary hover:text-text-primary'}`}><Layers className="w-3.5 h-3.5" aria-hidden /> BY FILE TYPE</button>
            </div>
          </div>
        </div>
        <Breadcrumb navPath={navPath} onBreadcrumbClick={handleBreadcrumbClick} />
      </div>
      <div
        className="flex-1 relative rounded-2xl overflow-hidden border border-edge shadow-xl bg-surface group focus-visible:outline-2 focus-visible:outline-offset-[-2px]"
        role="application"
        tabIndex={0}
        aria-label="File treemap"
        aria-describedby="treemap-keyhint"
        onKeyDown={handleKeyDown}
      >
        <AccessibleTree
          label="Treemap level"
          nodes={a11yNodes}
          selectedId={String(Math.min(cursor, Math.max(0, currentLevel.length - 1)))}
          onSelect={id => setCursor(Number(id))}
          onActivate={id => enterNode(currentLevel[Number(id)])}
          onUnhandledKey={handleKeyDown}
        />
        <span className="sr-only" aria-live="polite">{announcement}</span>
        {/* Was `opacity-0 group-hover:opacity-60`: the only statement of how
            to drive the chart, revealed only on mouse hover. */}
        <div id="treemap-keyhint" className="absolute top-12 right-4 z-10 pointer-events-none opacity-70 text-[10px] font-bold text-text-primary uppercase bg-surface border border-edge px-3 py-1.5 rounded-full shadow-sm">↑↓ browse · Enter open · ⌫ back · ? keys</div>
        {buildError ? (
          <div className="flex items-center justify-center h-full text-text-secondary font-medium">{buildError}</div>
        ) : (
          <ReactEChartsCore ref={chartRef} echarts={echarts} option={option} style={{ height: '100%', width: '100%' }} onEvents={onEvents} />
        )}
        <ShortcutOverlay
          open={shortcutsOpen}
          onClose={() => setShortcutsOpen(false)}
          groups={['outliner']}
          title="Treemap keyboard reference"
        />
      </div>
    </div>
  )
}
