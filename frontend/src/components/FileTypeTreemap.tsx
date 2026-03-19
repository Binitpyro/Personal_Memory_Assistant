import { useMemo, useCallback, useRef, useState, useEffect } from 'react'
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
  const r = Number.parseInt(c.substring(0, 2), 16)
  const g = Number.parseInt(c.substring(2, 4), 16)
  const b = Number.parseInt(c.substring(4, 6), 16)
  const lr = Math.min(255, Math.round(r + (255 - r) * amount))
  const lg = Math.min(255, Math.round(g + (255 - g) * amount))
  const lb = Math.min(255, Math.round(b + (255 - b) * amount))
  return `#${lr.toString(16).padStart(2, '0')}${lg.toString(16).padStart(2, '0')}${lb.toString(16).padStart(2, '0')}`
}

function normalizePath(p: string): string {
  let norm = p.replaceAll('\\', '/');
  while (norm.endsWith('/')) { norm = norm.slice(0, -1); }
  return norm;
}

function findCommonPrefix(paths: string[]): string {
  if (paths.length === 0) return '';
  const sorted = [...paths].sort((a, b) => a.localeCompare(b));
  const first = sorted[0].split('/');
  const last = (sorted.at(-1) || '').split('/');
  let i = 0;
  while (i < first.length && i < last.length && first[i] === last[i]) {
    i++;
  }
  return first.slice(0, i).join('/');
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

function buildTypeTree(flatFiles: FileEntry[], getVal: (s: number) => number) {
  const structure = new Map<string, Map<string, FileEntry[]>>()
  for (const f of flatFiles) {
    const ext = ('.' + f.path.split('.').pop()?.toLowerCase()) || '.other'
    const category = CATEGORY_MAP[ext] || 'Other'
    if (!structure.has(category)) structure.set(category, new Map())
    const catMap = structure.get(category)!
    if (!catMap.has(ext)) catMap.set(ext, [])
    catMap.get(ext)!.push(f)
  }

  return Array.from(structure.entries()).map(([category, extMap]) => {
    const baseColor = COLORS[category] || COLORS.Other
    const children = Array.from(extMap.entries()).map(([ext, files], i) => {
      const shade = i === 0 ? baseColor : lightenColor(baseColor, Math.min(0.4, 0.05 * i))
      const fileNodes = [...files].sort((a: FileEntry, b: FileEntry) => b.size - a.size).map((f: FileEntry) => ({
        name: f.path.split(/[\\/]/).pop() || f.path,
        value: getVal(f.size),
        realSize: f.size,
        fileData: f,
        itemStyle: { color: shade }
      }))
      return {
        name: ext,
        value: fileNodes.reduce((s: number, c: any) => s + c.value, 0),
        realSize: files.reduce((s: number, f: FileEntry) => s + f.size, 0),
        children: fileNodes,
        itemStyle: { color: shade }
      }
    })
    return {
      name: category,
      value: children.reduce((s: number, c: any) => s + c.value, 0),
      realSize: children.reduce((s: number, c: any) => s + c.realSize, 0),
      children,
      itemStyle: { color: baseColor }
    }
  }).sort((a: any, b: any) => b.realSize - a.realSize)
}

function finalizeTree(node: any): any {
  if (node.fileData) return node;
  const childArray = Array.from(node.children.values()).map(finalizeTree);
  return {
    ...node,
    children: childArray,
    value: childArray.reduce((s: number, c: any) => s + c.value, 0),
    realSize: childArray.reduce((s: number, c: any) => s + (c.realSize || 0), 0)
  };
}

function collapseFolderChains(node: any): any {
  if (!node.children || !Array.isArray(node.children) || node.children.length === 0) return node;
  node.children = node.children.map(collapseFolderChains);
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
}

function buildFolderTree(flatFiles: FileEntry[], getVal: (s: number) => number) {
  const normalizedPaths = flatFiles.map(f => normalizePath(f.path));
  const commonPrefix = findCommonPrefix(normalizedPaths);
  const prefixParts = commonPrefix.split('/').filter(Boolean);
  const startFolderName = prefixParts.at(-1) || 'Root';
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

  const finalizedTree = finalizeTree(rootNode);
  return collapseFolderChains(finalizedTree);
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
      return { treeData: buildTypeTree(flatFiles, getVal), totalSize: total }
    } else {
      return { treeData: [buildFolderTree(flatFiles, getVal)], totalSize: total }
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
      if (currentRoot?.parent) {
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
    const current = navPath.at(-1)!
    if (current.fullPath && confirm(`Remove index for all files in "${current.name}"?\n\nPath: ${current.fullPath}`)) {
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
        return `<div style="font-weight:700;margin-bottom:4px;color:#3d15cb">${info.name}</div>Size: <b>${formatBytes(size)}</b> (${pct}%)`
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
        color: '#1e293b',
        fontSize: 10
      },
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
      itemStyle: {
        borderColor: '#f1f5e0',
        borderWidth: 1,
        gapWidth: 1
      },
      levels: [
        {
          colorAlpha: [1, 1],
          colorSaturation: [1, 1],
          itemStyle: {
            color: '#f8fbf0',
            borderColor: '#3d15cb',
            borderWidth: 3,
            gapWidth: 3
          },
          upperLabel: { show: true, height: 26, backgroundColor: 'rgba(255,255,255,0.8)', color: '#3d15cb', fontWeight: 'bold', fontSize: 12 }
        },
        {
          colorAlpha: [1, 1],
          colorSaturation: [1, 1],
          itemStyle: {
            color: '#fdfdfd',
            borderColor: '#3d15cb',
            borderWidth: 3,
            gapWidth: 3
          },
          upperLabel: { show: true, height: 24, backgroundColor: 'rgba(255,255,255,0.7)', color: '#3d15cb', fontWeight: 'bold', fontSize: 11 }
        },
        {
          colorAlpha: [1, 1],
          colorSaturation: [1, 1],
          itemStyle: {
            color: '#ffffff',
            borderColor: '#9984d4',
            borderWidth: 2,
            gapWidth: 2
          },
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
          colorAlpha: [1, 1],
          colorSaturation: [1, 1],
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

      // Improved extension filter logic: works in any mode, searches path for an extension
      if (onFilterChange) {
        const pathInfo = params.treePathInfo || [];
        const extNode = pathInfo.find((p: any) => p.name?.startsWith('.'));
        if (extNode) {
          onFilterChange(extNode.name === activeFilter ? null : extNode.name);
        }
      }
    },
    contextmenu: (params: any) => { params.event.stop(); handleBack() }
  }), [handleBack, onFilterChange, onFileSelect, activeFilter, groupMode])

  // Automatically highlight the active filter node
  useEffect(() => {
    const instance = chartRef.current?.getEchartsInstance();
    if (instance && activeFilter) {
      instance.dispatchAction({
        type: 'highlight',
        seriesIndex: 0,
        name: activeFilter
      });
    }
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
            {onDeleteFolder && navPath.length > 1 && navPath[navPath.length - 1].fullPath && (
              <button onClick={handleDeleteCurrent} className="flex items-center gap-1 px-3 py-1.5 bg-error/10 hover:bg-error/20 border border-error/20 text-error rounded-xl text-[10px] font-bold transition-all"><Trash2 className="w-3.5 h-3.5" /> DELETE FOLDER INDEX</button>
            )}
            <div className="flex items-center bg-black/5 p-1 rounded-xl border border-white/20">
              <button onClick={() => { setGroupMode('folder'); handleHome() }} className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold transition-all ${groupMode === 'folder' ? 'bg-primary text-white shadow-lg' : 'text-text-secondary hover:text-text-primary'}`}><Folder className="w-3.5 h-3.5" /> BY FOLDERS</button>
              <button onClick={() => { setGroupMode('type'); handleHome() }} className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold transition-all ${groupMode === 'type' ? 'bg-primary text-white shadow-lg' : 'text-text-secondary hover:text-text-primary'}`}><Layers className="w-3.5 h-3.5" /> BY FILE TYPE</button>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1 bg-black/5 px-3 py-2 rounded-xl border border-white/10 overflow-x-auto no-scrollbar scroll-smooth">
          {navPath.map((seg, i) => {
            const isLast = i === navPath.length - 1;
            const isFile = isLast && !seg.fullPath;
            let Icon;
            if (i === 0) Icon = Home;
            else if (isFile) Icon = File;
            else Icon = Folder;
            const itemKey = seg.fullPath ? `${seg.fullPath}-${i}` : `${seg.name}-${i}`;
            return (
              <div key={itemKey} className="flex items-center shrink-0">
                <button onClick={() => handleBreadcrumbClick(i)} className={`flex items-center gap-1.5 px-2 py-1 rounded-lg text-[11px] font-medium transition-all hover:bg-black/5 ${isLast ? 'text-primary bg-primary/10' : 'text-text-secondary hover:text-text-primary'}`}>
                  <Icon className="w-3 h-3" />
                  <span className="max-w-[120px] truncate">{seg.name}</span>
                </button>
                {!isLast && <span className="text-text-secondary/20 mx-0.5">/</span>}
              </div>
            )
          })}
        </div>
      </div>
      <div className="flex-1 relative rounded-2xl overflow-hidden border border-white/40 shadow-xl bg-white/30 group">
        <div className="absolute top-12 right-4 z-10 pointer-events-none opacity-0 group-hover:opacity-60 transition-opacity text-[10px] font-bold text-text-primary uppercase bg-white/80 border border-white/40 px-3 py-1.5 rounded-full shadow-sm">Right-click: Back • Scroll: Zoom • Drag: Pan</div>
        <ReactEChartsCore
          ref={chartRef}
          echarts={echarts}
          option={option}
          style={{ height: '100%', width: '100%' }}
          onEvents={onEvents}
        />
      </div>
    </div>
  )
}
