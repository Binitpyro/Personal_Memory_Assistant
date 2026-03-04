import { useMemo, useCallback, useRef, useState } from 'react'
import { ChevronLeft, Home, File, Folder, Layers, Trash2 } from 'lucide-react'
import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { TreemapChart as EChartsTreemap } from 'echarts/charts'
import { TooltipComponent, VisualMapComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { type FileEntry } from '../api'

echarts.use([EChartsTreemap, TooltipComponent, VisualMapComponent, CanvasRenderer])

/* ── Constants & Helpers ─────────────────────────────────── */

const CATEGORY_MAP: Record<string, string> = {
  '.py': 'Code', '.js': 'Code', '.ts': 'Code', '.tsx': 'Code', '.jsx': 'Code',
  '.java': 'Code', '.c': 'Code', '.cpp': 'Code', '.h': 'Code', '.hpp': 'Code',
  '.rs': 'Code', '.go': 'Code', '.rb': 'Code', '.sh': 'Code', '.bat': 'Code',
  '.cs': 'Code', '.swift': 'Code', '.kt': 'Code', '.scala': 'Code',
  '.lua': 'Code', '.r': 'Code', '.pl': 'Code', '.php': 'Code', '.m': 'Code',
  '.sql': 'Code', '.css': 'Web', '.html': 'Web', '.htm': 'Web',
  '.scss': 'Web', '.sass': 'Web', '.less': 'Web', '.vue': 'Web', '.svelte': 'Web',
  '.json': 'Data', '.xml': 'Data', '.yaml': 'Data', '.yml': 'Data',
  '.pdf': 'Documents', '.doc': 'Documents', '.docx': 'Documents',
  '.txt': 'Documents', '.md': 'Documents', '.rtf': 'Documents',
  '.odt': 'Documents', '.pages': 'Documents', '.tex': 'Documents',
  '.log': 'Documents', '.csv': 'Data',
  '.uasset': 'Unreal', '.umap': 'Unreal', '.uproject': 'Unreal',
  '.png': 'Images', '.jpg': 'Images', '.jpeg': 'Images', '.gif': 'Images',
  '.svg': 'Images', '.zip': 'Archives', '.exe': 'Executables',
}

const COLORS: Record<string, string> = {
  Code: '#3572A5', Web: '#e34c26', Documents: '#e11d48', Data: '#a4c639',
  Unreal: '#007fff', Images: '#a36ad5', Media: '#1db954', Presentations: '#d24726',
  Archives: '#f59e0b', Executables: '#9333ea', Other: '#6b7280',
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function lightenColor(hex: string, amount: number): string {
  const c = hex.replace('#', '')
  if (c.length !== 6) return hex
  const r = parseInt(c.substring(0, 2), 16)
  const g = parseInt(c.substring(2, 4), 16)
  const b = parseInt(c.substring(4, 6), 16)
  const lr = Math.min(255, Math.round(r + (255 - r) * amount))
  const lg = Math.min(255, Math.round(g + (255 - g) * amount))
  const lb = Math.min(255, Math.round(b + (255 - b) * amount))
  return `#${lr.toString(16).padStart(2, '0')}${lg.toString(16).padStart(2, '0')}${lb.toString(16).padStart(2, '0')}`
}

function normalizePath(p: string): string {
  return p.replace(/\\/g, '/').replace(/\/+$/, '')
}

function findCommonPrefix(paths: string[]): string {
  if (paths.length === 0) return '';
  const sorted = [...paths].sort();
  const first = sorted[0].split('/');
  const last = sorted[sorted.length - 1].split('/');
  let i = 0;
  while (i < first.length && i < last.length && first[i] === last[i]) {
    i++;
  }
  return first.slice(0, i).join('/');
}

export interface FileTypeTreemapProps {
  allFiles: Record<string, FileEntry[]>
  activeFilter?: string | null
  onFilterChange?: (ext: string | null) => void
  onFileSelect?: (file: FileEntry) => void
  onDeleteFolder?: (path: string) => void
  initialMode?: 'folder' | 'type'
}

interface NavSegment {
  name: string
  fullPath: string | null
}

export function FileTypeTreemap({ allFiles, activeFilter, onFilterChange, onFileSelect, onDeleteFolder, initialMode = 'folder' }: FileTypeTreemapProps) {
  const chartRef = useRef<ReactEChartsCore>(null)
  const [groupMode, setGroupMode] = useState<'folder' | 'type'>(initialMode)
  
  // Dynamic Root Label
  const rootLabel = useMemo(() => {
    if (groupMode === 'type') return 'File Types'
    const paths = Object.values(allFiles).flat().map(f => normalizePath(f.path))
    if (paths.length === 0) return 'Root'
    const prefix = findCommonPrefix(paths)
    return prefix.split('/').pop() || 'Root'
  }, [allFiles, groupMode])

  const [navPath, setNavPath] = useState<NavSegment[]>([{ name: rootLabel, fullPath: null }])

  /* ── Build Data Logic ──────────────────────────────────── */
  const { treeData, totalSize } = useMemo(() => {
    const flatFiles = Object.values(allFiles).flat()
    const total = flatFiles.reduce((s, f) => s + f.size, 0)
    
    // Square root scale: better than pow(0.2) for relative impact, 
    // better than linear for seeing small files.
    const getVal = (s: number) => Math.sqrt(s + 1) * 10

    if (groupMode === 'type') {
      const structure = new Map<string, Map<string, FileEntry[]>>()
      for (const f of flatFiles) {
        const ext = ('.' + f.path.split('.').pop()?.toLowerCase()) || '.other'
        const category = CATEGORY_MAP[ext] || 'Other'
        if (!structure.has(category)) structure.set(category, new Map())
        const catMap = structure.get(category)!
        if (!catMap.has(ext)) catMap.set(ext, [])
        catMap.get(ext)!.push(f)
      }

      const nodes: any[] = Array.from(structure.entries()).map(([category, extMap]) => {
        const baseColor = COLORS[category] || COLORS.Other
        const children = Array.from(extMap.entries()).map(([ext, files], i) => {
          const shade = i === 0 ? baseColor : lightenColor(baseColor, Math.min(0.4, 0.05 * i))
          const fileNodes = files.sort((a, b) => b.size - a.size).map(f => ({
            name: f.path.split(/[\\/]/).pop() || f.path,
            value: getVal(f.size),
            realSize: f.size,
            fileData: f,
            itemStyle: { color: shade }
          }))
          return {
            name: ext,
            value: fileNodes.reduce((s, c) => s + c.value, 0),
            realSize: files.reduce((s, f) => s + f.size, 0),
            children: fileNodes,
            itemStyle: { color: shade }
          }
        })
        return {
          name: category,
          value: children.reduce((s, c) => s + c.value, 0),
          realSize: children.reduce((s, c) => s + c.realSize, 0),
          children,
          itemStyle: { color: baseColor }
        }
      }).sort((a, b) => b.realSize - a.realSize)

      return { treeData: nodes, totalSize: total }
    } else {
      // FOLDER MODE
      const normalizedPaths = flatFiles.map(f => normalizePath(f.path));
      const commonPrefix = findCommonPrefix(normalizedPaths);
      const prefixParts = commonPrefix.split('/').filter(Boolean);
      const startFolderName = prefixParts[prefixParts.length - 1] || 'Root';
      const stripPath = prefixParts.slice(0, -1).join('/');
      
      const rootNode: any = { 
        name: startFolderName, 
        fullPath: commonPrefix, 
        children: new Map(), 
        realSize: 0, 
        value: 0,
        itemStyle: { color: '#1e1a3a' }
      };

      for (const f of flatFiles) {
        const fullPath = normalizePath(f.path);
        let relative = fullPath;
        if (stripPath && fullPath.startsWith(stripPath)) {
          relative = fullPath.slice(stripPath.length).replace(/^\/+/, '');
        }
        
        const parts = relative.split('/').filter(Boolean);
        let current = rootNode;
        const startIdx = parts[0] === startFolderName ? 1 : 0;

        for (let i = startIdx; i < parts.length; i++) {
          const part = parts[i];
          const isFile = (i === parts.length - 1);
          if (isFile) {
            const ext = ('.' + part.split('.').pop()?.toLowerCase()) || '.other';
            const category = CATEGORY_MAP[ext] || 'Other';
            current.children.set(part, {
              name: part,
              value: getVal(f.size),
              realSize: f.size,
              fileData: f,
              itemStyle: { color: COLORS[category] || COLORS.Other }
            });
          } else {
            if (!current.children.has(part)) {
              current.children.set(part, {
                name: part,
                children: new Map(),
                fullPath: current.fullPath + '/' + part,
                realSize: 0, 
                value: 0,
                itemStyle: { color: '#1e1a3a' }
              });
            }
            current = current.children.get(part);
          }
        }
      }

      const finalize = (node: any): any => {
        if (node.fileData) return node;
        const childArray = Array.from(node.children.values()).map(finalize);
        return {
          ...node,
          children: childArray,
          value: childArray.reduce((s: number, c: any) => s + c.value, 0),
          realSize: childArray.reduce((s: number, c: any) => s + (c.realSize || 0), 0)
        };
      };

      const finalizedTree = finalize(rootNode);

      // Collapse single-child folder chains (Root > C: > Users > binit → "Root / C: / Users / binit")
      // This eliminates wasted header rows for ancestor paths, matching TreeSize behavior
      const collapseChains = (node: any): any => {
        if (!node.children || !Array.isArray(node.children) || node.children.length === 0) return node;
        node.children = node.children.map(collapseChains);
        while (
          node.children.length === 1 &&
          node.children[0].children &&
          Array.isArray(node.children[0].children) &&
          node.children[0].children.length > 0
        ) {
          const only = node.children[0];
          node.name = `${node.name} / ${only.name}`;
          node.fullPath = only.fullPath || node.fullPath;
          node.children = only.children;
          node.realSize = only.realSize;
          node.value = only.value;
        }
        return node;
      };

      const collapsed = collapseChains(finalizedTree);
      return { treeData: [collapsed], totalSize: total };
    }
  }, [allFiles, groupMode])

  /* ── Navigation ────────────────────────────────────────── */
  const handleHome = useCallback(() => {
    const instance = chartRef.current?.getEchartsInstance()
    if (instance) {
      instance.dispatchAction({ type: 'treemapRootToNode', targetNode: null })
      setNavPath([{ name: rootLabel, fullPath: null }])
    }
  }, [rootLabel])

  const handleBack = useCallback(() => {
    const instance = chartRef.current?.getEchartsInstance()
    if (!instance) return
    try {
      const series = (instance as any).getModel().getSeriesByIndex(0)
      const currentRoot = series.getViewRoot()
      if (currentRoot && currentRoot.parent) {
        instance.dispatchAction({ type: 'treemapRootToNode', targetNode: currentRoot.parent })
        setNavPath(prev => prev.length > 1 ? prev.slice(0, -1) : [{ name: rootLabel, fullPath: null }])
      } else handleHome()
    } catch { handleHome() }
  }, [handleHome, rootLabel])

  const handleBreadcrumbClick = useCallback((index: number) => {
    const instance = chartRef.current?.getEchartsInstance()
    if (!instance) return
    if (index === 0) { handleHome(); return }
    const targetName = navPath[index].name
    instance.dispatchAction({ type: 'treemapRootToNode', targetNode: targetName })
    setNavPath(prev => prev.slice(0, index + 1))
  }, [navPath, handleHome])

  const handleDeleteCurrent = useCallback(() => {
    if (!onDeleteFolder || navPath.length <= 1) return
    const current = navPath[navPath.length - 1]
    if (current.fullPath && confirm(`Remove index for all files in "${current.name}"?\n\nPath: ${current.fullPath}`)) {
      onDeleteFolder(current.fullPath) 
    }
  }, [onDeleteFolder, navPath])

  const option = useMemo(() => ({
    backgroundColor: '#0e0b1a', // Deep Surface background
    tooltip: {
      backgroundColor: '#1a1735', borderColor: '#3b3766', textStyle: { color: '#ffffff' },
      formatter: (info: any) => {
        const size = info.data?.realSize ?? info.value
        const pct = totalSize > 0 ? ((size / totalSize) * 100).toFixed(1) : '0.0'
        return `<div style="font-weight:600;margin-bottom:4px">${info.name}</div>Size: <b>${formatBytes(size)}</b> (${pct}%)`
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
      label: {
        show: true,
        formatter: '{b}',
        color: '#f1f5e0',
        fontSize: 10
      },
      upperLabel: {
        show: true,
        height: 22,
        color: '#f1f5e0',
        fontSize: 11,
        fontWeight: 'bold',
        backgroundColor: 'rgba(14,11,26,0.85)',
        formatter: (params: any) => {
          const size = params.data?.realSize
          return size != null ? `\u{1F4C1} ${params.name} (${formatBytes(size)})` : ` ${params.name}`
        }
      },
      itemStyle: {
        borderColor: '#0e0b1a',
        borderWidth: 1,
        gapWidth: 1
      },
      levels: [
        // ── Level 0 (Root) ── Ultrasonic Blue primary border
        {
          colorAlpha: [1, 1],
          colorSaturation: [1, 1],
          itemStyle: { color: '#1e1a3a', borderColor: '#3d15cb', borderWidth: 3, gapWidth: 3 },
          upperLabel: { show: true, height: 26, backgroundColor: 'rgba(61,21,203,0.18)', color: '#f1f5e0', fontWeight: 'bold', fontSize: 12 }
        },
        // ── Level 1 (Primary folders) ── Ultrasonic Blue
        {
          colorAlpha: [1, 1],
          colorSaturation: [1, 1],
          itemStyle: { color: '#1e1a3a', borderColor: '#3d15cb', borderWidth: 3, gapWidth: 3 },
          upperLabel: { show: true, height: 24, backgroundColor: 'rgba(61,21,203,0.15)', color: '#f1f5e0', fontWeight: 'bold', fontSize: 11 }
        },
        // ── Level 2 (Sub-folders) ── Soft Periwinkle
        {
          colorAlpha: [1, 1],
          colorSaturation: [1, 1],
          itemStyle: { color: '#1e1a3a', borderColor: '#9984d4', borderWidth: 2, gapWidth: 2 },
          upperLabel: { show: true, height: 22, backgroundColor: 'rgba(153,132,212,0.12)', color: '#f1f5e0', fontWeight: 'bold', fontSize: 10 }
        },
        // ── Level 3 ── Soft Periwinkle
        {
          colorAlpha: [1, 1],
          colorSaturation: [1, 1],
          itemStyle: { color: '#1e1a3a', borderColor: '#9984d4', borderWidth: 1.5, gapWidth: 1.5 },
          upperLabel: { show: true, height: 20, backgroundColor: 'rgba(153,132,212,0.10)', color: '#f1f5e0', fontSize: 10 }
        },
        // ── Level 4 ── Soft Periwinkle
        {
          colorAlpha: [1, 1],
          colorSaturation: [1, 1],
          itemStyle: { color: '#1e1a3a', borderColor: '#9984d4', borderWidth: 1.5, gapWidth: 1 },
          upperLabel: { show: true, height: 18, backgroundColor: 'rgba(153,132,212,0.08)', color: '#f1f5e0', fontSize: 9 }
        },
        // ── Level 5 ── Periwinkle thin
        {
          colorAlpha: [1, 1],
          colorSaturation: [1, 1],
          itemStyle: { color: '#1e1a3a', borderColor: '#9984d4', borderWidth: 1, gapWidth: 1 },
          upperLabel: { show: true, height: 16, backgroundColor: 'rgba(153,132,212,0.06)', color: '#f1f5e0', fontSize: 9 }
        },
        // ── Level 6 ── Periwinkle minimal
        {
          colorAlpha: [1, 1],
          colorSaturation: [1, 1],
          itemStyle: { color: '#1e1a3a', borderColor: '#9984d4', borderWidth: 1, gapWidth: 0 },
          upperLabel: { show: true, height: 14, backgroundColor: 'rgba(153,132,212,0.05)', color: '#f1f5e0', fontSize: 8 }
        },
        // ── Level 7 ── Deep folders catch-all
        {
          colorAlpha: [1, 1],
          colorSaturation: [1, 1],
          itemStyle: { color: '#1e1a3a', borderColor: '#9984d4', borderWidth: 1, gapWidth: 0 },
          upperLabel: { show: true, height: 14, backgroundColor: 'rgba(153,132,212,0.05)', color: '#f1f5e0', fontSize: 8 }
        },
        // ── Level 8 (Leaf files) ── Per-item category color, minimal borders
        {
          colorAlpha: [1, 1],
          colorSaturation: [1, 1],
          itemStyle: { borderColor: 'rgba(14,11,26,0.5)', borderWidth: 1, gapWidth: 0 },
          label: { show: true, position: 'inside', fontSize: 9, color: '#f1f5e0', formatter: (p: any) => p.value > 800 ? p.name : '' }
        }
      ]
    }]
  }), [treeData, totalSize])

  const onEvents = useMemo(() => ({
    click: (params: any) => {
      // 1. Outline effect for folders
      if (params.data?.children && chartRef.current) {
        const instance = chartRef.current.getEchartsInstance();
        instance.dispatchAction({
          type: 'highlight',
          seriesIndex: 0,
          dataIndex: params.dataIndex
        });
        setTimeout(() => {
          instance.dispatchAction({
            type: 'downplay',
            seriesIndex: 0,
            dataIndex: params.dataIndex
          });
        }, 1000);
      }

      // 2. Navigation & Selection logic
      if (params.data?.children) {
        // Construct hierarchical path from root to current node using treePathInfo
        const pathInfo = params.treePathInfo || [];
        const newNav = pathInfo
          .map((p: any) => ({
            name: p.name,
            fullPath: p.data?.fullPath || null
          }))
          .filter((p: any) => p.name !== '');
        
        setNavPath(newNav);
      }

      if (params.data?.fileData && onFileSelect) onFileSelect(params.data.fileData)
      
      // Extension filter logic
      if (groupMode === 'type' && params.treePathInfo?.length === 3 && onFilterChange) {
        onFilterChange(params.name === activeFilter ? null : params.name)
      }
    },
    contextmenu: (params: any) => { params.event.stop(); handleBack() }
  }), [handleBack, onFilterChange, onFileSelect, activeFilter, groupMode])

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 bg-surface-lighter/50 p-3 rounded-2xl border border-white/5 shadow-inner">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <button onClick={handleBack} disabled={navPath.length <= 1} className="flex items-center gap-1 px-3 py-1.5 bg-surface-dark/50 hover:bg-primary/20 border border-white/10 rounded-xl text-xs font-bold transition-all disabled:opacity-20 text-text-primary"><ChevronLeft className="w-4 h-4" /> BACK</button>
            <button onClick={handleHome} className="flex items-center gap-1 px-3 py-1.5 bg-surface-dark/50 hover:bg-primary/20 border border-white/10 rounded-xl text-xs font-bold transition-all text-text-primary"><Home className="w-4 h-4" /> HOME</button>
          </div>
          <div className="flex items-center gap-3">
            {onDeleteFolder && navPath.length > 1 && navPath[navPath.length - 1].fullPath && (
              <button onClick={handleDeleteCurrent} className="flex items-center gap-1 px-3 py-1.5 bg-error/10 hover:bg-error/20 border border-error/20 text-error rounded-xl text-[10px] font-bold transition-all"><Trash2 className="w-3.5 h-3.5" /> DELETE FOLDER INDEX</button>
            )}
            <div className="flex items-center bg-surface-dark/50 p-1 rounded-xl border border-white/10">
              <button onClick={() => { setGroupMode('folder'); handleHome() }} className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold transition-all ${groupMode === 'folder' ? 'bg-primary text-white shadow-lg' : 'text-text-secondary hover:text-text-primary'}`}><Folder className="w-3.5 h-3.5" /> BY FOLDERS</button>
              <button onClick={() => { setGroupMode('type'); handleHome() }} className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold transition-all ${groupMode === 'type' ? 'bg-primary text-white shadow-lg' : 'text-text-secondary hover:text-text-primary'}`}><Layers className="w-3.5 h-3.5" /> BY FILE TYPE</button>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1 bg-surface-dark/80 px-3 py-2 rounded-xl border border-white/5 overflow-x-auto no-scrollbar scroll-smooth">
          {navPath.map((seg, i) => (
            <div key={i} className="flex items-center shrink-0">
              <button onClick={() => handleBreadcrumbClick(i)} className={`flex items-center gap-1.5 px-2 py-1 rounded-lg text-[11px] font-medium transition-all hover:bg-white/5 ${i === navPath.length - 1 ? 'text-primary-light bg-primary/10' : 'text-text-secondary hover:text-text-primary'}`}>
                {i === 0 ? <Home className="w-3 h-3" /> : (i === navPath.length - 1 && !seg.fullPath) ? <File className="w-3 h-3" /> : <Folder className="w-3 h-3" />}
                <span className="max-w-[120px] truncate">{seg.name}</span>
              </button>
              {i < navPath.length - 1 && <span className="text-white/20 mx-0.5">/</span>}
            </div>
          ))}
        </div>
      </div>
      <div className="flex-1 min-h-[500px] relative rounded-2xl overflow-hidden border border-white/5 shadow-2xl bg-surface-dark group">
        <div className="absolute top-12 right-4 z-10 pointer-events-none opacity-0 group-hover:opacity-40 transition-opacity text-[10px] font-bold text-white uppercase bg-black/60 px-3 py-1.5 rounded-full">Right-click: Back • Scroll: Zoom • Drag: Pan</div>
        <ReactEChartsCore ref={chartRef} echarts={echarts} option={option} style={{ height: '100%', width: '100%' }} onEvents={onEvents} />
      </div>
    </div>
  )
}
