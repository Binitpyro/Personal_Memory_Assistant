// FileTypeTreemap.tsx
// ─────────────────────────────────────────────────────────────────
//  BY FOLDERS  →  3D Isometric Dreamscape: Encompassing Crystals + Bubbles
//  BY FILE TYPE → ECharts Treemap (unchanged)
// ─────────────────────────────────────────────────────────────────

import { useMemo, useRef, useState, useEffect, useCallback } from 'react'
import { ChevronLeft, Home, Layers, Sparkles } from 'lucide-react'
import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { TreemapChart as EChartsTreemap } from 'echarts/charts'
import { TooltipComponent, VisualMapComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { FileEntry } from '../api'

echarts.use([EChartsTreemap, TooltipComponent, VisualMapComponent, CanvasRenderer])

/* ═══════════════════════════════════════════════════════════════
   CONSTANTS & PURE HELPERS
═══════════════════════════════════════════════════════════════ */

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
  Code:          '#3572A5',
  Web:           '#e34c26',
  Documents:     '#e11d48',
  Data:          '#a4c639',
  Unreal:        '#007fff',
  Images:        '#a36ad5',
  Media:         '#1db954',
  Presentations: '#d24726',
  Archives:      '#f59e0b',
  Executables:   '#9333ea',
  Other:         '#6b7280',
}

const DEPTH_HUES = [260, 200, 310, 170, 40, 280, 150, 340]

function formatBytes(bytes: number): string {
  if (bytes < 1024)                  return `${bytes} B`
  if (bytes < 1024 * 1024)           return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024)    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function lightenColor(hex: string, amount: number): string {
  const c = hex.replace('#', '')
  if (c.length !== 6) return hex
  const r = parseInt(c.substring(0, 2), 16)
  const g = parseInt(c.substring(2, 4), 16)
  const b = parseInt(c.substring(4, 6), 16)
  return `#${[r, g, b]
    .map(v => Math.min(255, Math.round(v + (255 - v) * amount)).toString(16).padStart(2, '0'))
    .join('')}`
}

function normalizePath(p: string): string {
  return p.replace(/\\/g, '/').replace(/\/+$/, '')
}

function findCommonPrefix(paths: string[]): string {
  if (paths.length === 0) return ''
  const sorted = [...paths].sort()
  const first = sorted[0].split('/')
  const last  = sorted[sorted.length - 1].split('/')
  let i = 0
  while (i < first.length && i < last.length && first[i] === last[i]) i++
  return first.slice(0, i).join('/')
}

/* ═══════════════════════════════════════════════════════════════
   FOLDER TREE MODEL & LOCAL TREEMAP LAYOUT
═══════════════════════════════════════════════════════════════ */

export interface NestedFileEntry extends FileEntry {
  localX: number
  localY: number
  localW: number
  localH: number
}

interface FolderNode {
  name:     string
  fullPath: string | null
  realSize: number
  depth:    number
  children: FolderNode[]
  files:    NestedFileEntry[]
  // Layout bounds (0-100 normalized relative to parent)
  localX: number
  localY: number
  localW: number
  localH: number
}

function computeLocalLayout(node: FolderNode) {
  // Limit nodes to avoid extreme DOM overload when rendering
  const MAX_CHILDREN = 40;
  const MAX_FILES = 80;

  const displayFolders = node.children.slice(0, MAX_CHILDREN);
  const displayFiles = node.files.slice(0, MAX_FILES);

  const items = [
    ...displayFolders.map(c => ({ type: 'folder' as const, size: c.realSize, ref: c })),
    ...displayFiles.map(f => ({ type: 'file' as const, size: f.size, ref: f }))
  ].sort((a, b) => b.size - a.size)

  let curX = 0, curY = 0, curW = 100, curH = 100

  items.forEach((item, idx) => {
    const isLast = idx === items.length - 1
    const remainingSize = items.slice(idx).reduce((acc, i) => acc + i.size, 0)
    const ratio = remainingSize === 0 ? 0 : item.size / remainingSize
    
    const splitHorizontally = curW > curH

    let itemX = curX, itemY = curY, itemW = curW, itemH = curH

    if (splitHorizontally) {
      itemW = isLast ? curW : curW * ratio
      curX += itemW
      curW -= itemW
    } else {
      itemH = isLast ? curH : curH * ratio
      curY += itemH
      curH -= itemH
    }

    if (item.type === 'folder') {
      const f = item.ref as FolderNode
      f.localX = itemX; f.localY = itemY; f.localW = itemW; f.localH = itemH;
    } else {
      const f = item.ref as NestedFileEntry
      f.localX = itemX; f.localY = itemY; f.localW = itemW; f.localH = itemH;
    }
  })
}

function buildFolderTree(allFiles: Record<string, FileEntry[]>): FolderNode | null {
  const flatFiles = Object.values(allFiles).flat()
  if (flatFiles.length === 0) return null

  const normalizedPaths = flatFiles.map(f => normalizePath(f.path))
  const commonPrefix    = findCommonPrefix(normalizedPaths)
  const prefixParts     = commonPrefix.split('/').filter(Boolean)
  const startFolderName = prefixParts[prefixParts.length - 1] || 'Root'
  const stripPath       = prefixParts.slice(0, -1).join('/')

  const root: any = {
    name: startFolderName,
    fullPath: commonPrefix || null,
    children: new Map<string, any>(),
    files: [] as NestedFileEntry[],
    realSize: 0,
    depth: 0,
  }

  for (const f of flatFiles) {
    const fullPath = normalizePath(f.path)
    let relative = fullPath
    if (stripPath && fullPath.startsWith(stripPath)) {
      relative = fullPath.slice(stripPath.length).replace(/^\/+/, '')
    }
    const parts    = relative.split('/').filter(Boolean)
    let current    = root
    const startIdx = parts[0] === startFolderName ? 1 : 0

    for (let i = startIdx; i < parts.length; i++) {
      const part   = parts[i]
      const isFile = i === parts.length - 1
      if (isFile) {
        current.files.push(f as NestedFileEntry)
      } else {
        if (!current.children.has(part)) {
          current.children.set(part, {
            name:     part,
            fullPath: (current.fullPath ? current.fullPath + '/' : '') + part,
            children: new Map<string, any>(),
            files:    [] as NestedFileEntry[],
            realSize: 0,
            depth:    current.depth + 1,
          })
        }
        current = current.children.get(part)
      }
    }
  }

  const finalize = (node: any, depth: number): FolderNode => {
    const childrenArray: FolderNode[] = Array.from(node.children.values()).map((c: any) =>
      finalize(c, depth + 1)
    )
    const ownSize      = node.files.reduce((s: number, f: FileEntry) => s + f.size, 0)
    const childrenSize = childrenArray.reduce((s, c) => s + c.realSize, 0)
    
    const finalizedNode: FolderNode = {
      name:     node.name,
      fullPath: node.fullPath,
      children: childrenArray.sort((a, b) => b.realSize - a.realSize),
      files:    node.files.sort((a: FileEntry, b: FileEntry) => b.size - a.size).map(f => ({...f, localX:0, localY:0, localW:0, localH:0})),
      realSize: ownSize + childrenSize,
      depth,
      localX: 0, localY: 0, localW: 0, localH: 0
    }

    computeLocalLayout(finalizedNode)
    return finalizedNode
  }

  return finalize(root, 0)
}

function findNode(tree: FolderNode, path: string | null): FolderNode {
  if (!path || tree.fullPath === path) return tree
  for (const child of tree.children) {
    const found = findNode(child, path)
    if (found.fullPath === path) return found
  }
  return tree
}

/* ═══════════════════════════════════════════════════════════════
   NESTED BUBBLE COMPONENT (FILE)
═══════════════════════════════════════════════════════════════ */

interface NestedBubbleProps {
  file: NestedFileEntry
  parentHeight: number
  onFileSelect?: (file: FileEntry) => void
  onFileHover: (e: React.MouseEvent, file: FileEntry) => void
  onLeave: () => void
}

function NestedBubble({ file, parentHeight, onFileSelect, onFileHover, onLeave }: NestedBubbleProps) {
  const ext = ('.' + file.path.split('.').pop()?.toLowerCase()) || '.other'
  const category = CATEGORY_MAP[ext] || 'Other'
  const color = COLORS[category] || COLORS.Other
  
  const size = 16; 
  const x = file.localX + file.localW / 2;
  const y = file.localY + file.localH / 2;

  const altitude = useMemo(() => 5 + Math.random() * Math.max(0, parentHeight - size - 10), [parentHeight, size])
  const delay = useMemo(() => -Math.random() * 5, [])

  return (
    <div
      style={{
        position: 'absolute',
        left: `${x}%`, top: `${y}%`,
        transformStyle: 'preserve-3d',
        transform: `translate3d(-50%, -50%, ${altitude}px)`,
        pointerEvents: 'auto',
        cursor: 'pointer',
      }}
      onClick={(e) => { e.stopPropagation(); onFileSelect?.(file); }}
      onMouseEnter={(e) => { e.stopPropagation(); onFileHover(e, file); }}
      onMouseLeave={(e) => { e.stopPropagation(); onLeave(); }}
    >
      <div 
        className="rounded-full animate-float-slow"
        style={{
          width: size, height: size,
          background: `radial-gradient(circle at 30% 30%, white 0%, ${color}cc 40%, ${color}66 80%, transparent 100%)`,
          boxShadow: `0 0 10px ${color}88, inset 0 0 4px white`,
          border: '1px solid rgba(255,255,255,0.5)',
          animationDelay: `${delay}s`,
        }}
      />
    </div>
  )
}

/* ═══════════════════════════════════════════════════════════════
   RECURSIVE ENCOMPASSING CRYSTAL COMPONENT (FOLDER)
═══════════════════════════════════════════════════════════════ */

interface RecursiveCrystalProps {
  node: FolderNode
  depth: number
  maxDepth: number
  onFolderClick: (node: FolderNode) => void
  onFileHover: (e: React.MouseEvent, file: FileEntry) => void
  onFolderHover: (e: React.MouseEvent, folder: FolderNode) => void
  onLeave: () => void
  onFileSelect?: (file: FileEntry) => void
}

function RecursiveCrystal({ node, depth, maxDepth, onFolderClick, onFileHover, onFolderHover, onLeave, onFileSelect }: RecursiveCrystalProps) {
  const isViewRoot = depth === 0;
  
  const x = isViewRoot ? 0 : node.localX;
  const y = isViewRoot ? 0 : node.localY;
  const w = isViewRoot ? 100 : node.localW;
  const h = isViewRoot ? 100 : node.localH;

  const baseHeight = 160;
  const height = Math.max(30, baseHeight - depth * 50);
  const zOffset = isViewRoot ? 0 : 4; // Lift nested crystals slightly off parent's floor
  
  const hue = DEPTH_HUES[(node.depth) % DEPTH_HUES.length];
  const alpha = 0.10 + (depth * 0.05); // Deeper = slightly more opaque

  const glassStyle = {
    position: 'absolute' as const,
    border: '1px solid rgba(255,255,255,0.25)',
    background: `linear-gradient(135deg, hsla(${hue}, 80%, 70%, ${alpha}), hsla(${hue}, 80%, 30%, ${alpha + 0.1}))`,
    boxShadow: `inset 0 0 15px hsla(${hue}, 80%, 60%, 0.15)`,
    backdropFilter: depth <= 1 ? 'blur(2px)' : 'none',
  }

  const wallStyle = { ...glassStyle, pointerEvents: 'none' as const };

  const renderContents = depth < maxDepth;
  const displayFolders = node.children.slice(0, 40);
  const displayFiles = node.files.slice(0, 80);

  return (
    <div
      style={{
        position: 'absolute',
        left: `${x}%`, top: `${y}%`, width: `${w}%`, height: `${h}%`,
        transformStyle: 'preserve-3d',
        transform: isViewRoot ? 'none' : `translateZ(${zOffset}px)`,
        transition: 'all 0.5s ease',
      }}
    >
      <div 
        className="crystal-volume group absolute"
        onClick={(e) => { e.stopPropagation(); onFolderClick(node); }}
        onMouseEnter={(e) => { e.stopPropagation(); onFolderHover(e, node); }}
        onMouseLeave={(e) => { e.stopPropagation(); onLeave(); }}
        style={{
          inset: isViewRoot ? '0%' : '1.5%', // 1.5% padding inside layout cell
          transformStyle: 'preserve-3d',
          cursor: 'pointer',
        }}
      >
        {/* Floor (Catches interactions for the entire crystal volume if empty) */}
        <div style={{ ...glassStyle, inset: 0, transform: 'translateZ(0)', pointerEvents: 'auto' }} />
        
        {/* Ceiling (Faint to allow viewing inside) */}
        <div 
          style={{ ...wallStyle, inset: 0, transform: `translateZ(${height}px)`, opacity: 0.05 }} 
          className="group-hover:opacity-30 transition-opacity duration-300"
        />

        {/* Walls (Pointer events none so they don't block hovering interior elements) */}
        <div style={{ ...wallStyle, bottom: 0, left: 0, right: 0, height, transformOrigin: 'bottom', transform: 'rotateX(90deg)' }} />
        <div style={{ ...wallStyle, top: 0, left: 0, right: 0, height, transformOrigin: 'top', transform: 'rotateX(-90deg)' }} />
        <div style={{ ...wallStyle, top: 0, bottom: 0, left: 0, width: height, transformOrigin: 'left', transform: 'rotateY(90deg)' }} />
        <div style={{ ...wallStyle, top: 0, bottom: 0, right: 0, width: height, transformOrigin: 'right', transform: 'rotateY(-90deg)' }} />

        {/* Contents Encompassed Within */}
        {renderContents && (
          <div style={{ position: 'absolute', inset: 0, transformStyle: 'preserve-3d' }}>
            {displayFolders.map((c: any) => (
              <RecursiveCrystal 
                key={c.name} node={c} depth={depth + 1} maxDepth={maxDepth}
                onFolderClick={onFolderClick} onFileHover={onFileHover} onFolderHover={onFolderHover} onLeave={onLeave} onFileSelect={onFileSelect}
              />
            ))}
            {displayFiles.map((f: any) => (
              <NestedBubble 
                key={f.path} file={f} parentHeight={height}
                onFileHover={onFileHover} onLeave={onLeave} onFileSelect={onFileSelect}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

/* ═══════════════════════════════════════════════════════════════
   ISO MAP 3D WORLD
═══════════════════════════════════════════════════════════════ */

interface IsoMap3DWorldProps {
  rootNode: FolderNode
  currentPath: string | null
  onFolderClick: (node: FolderNode) => void
  onFileSelect?: (file: FileEntry) => void
}

function IsoMap3DWorld({ rootNode, currentPath, onFolderClick, onFileSelect }: IsoMap3DWorldProps) {
  const [tooltip, setTooltip] = useState<any>({ visible: false, x: 0, y: 0, name: '', size: 0, isFolder: false })
  const containerRef = useRef<HTMLDivElement>(null)

  const showTooltip = useCallback((e: React.MouseEvent, item: any, isFolder: boolean) => {
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return
    setTooltip({
      visible: true,
      x: e.clientX - rect.left + 15,
      y: e.clientY - rect.top - 15,
      name: isFolder ? item.name : item.path.split(/[/\\]/).pop(),
      size: isFolder ? item.realSize : item.size,
      isFolder
    })
  }, [])

  const hideTooltip = useCallback(() => setTooltip((t: any) => ({ ...t, visible: false })), [])

  const currentNode = findNode(rootNode, currentPath)

  return (
    <div ref={containerRef} className="relative w-full h-full overflow-hidden bg-[#050510] flex items-center justify-center">
      <style>{`
        @keyframes float-slow {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-8px); }
        }
        .animate-float-slow {
          animation: float-slow 3s ease-in-out infinite;
        }
      `}</style>

      {/* 3D Scene Container */}
      <div style={{
        width: '100%',
        height: '100%',
        perspective: '1500px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center'
      }}>
        {/* Isometric Rotator */}
        <div style={{
          width: '550px',
          height: '550px',
          position: 'relative',
          transform: 'rotateX(60deg) rotateZ(-45deg)',
          transformStyle: 'preserve-3d',
          transition: 'transform 0.8s cubic-bezier(0.4, 0, 0.2, 1)',
        }}>
          {currentNode && (
             <RecursiveCrystal 
               node={currentNode} 
               depth={0} 
               maxDepth={2} 
               onFolderClick={onFolderClick}
               onFileHover={(e: React.MouseEvent, f: FileEntry) => showTooltip(e, f, false)}
               onFolderHover={(e: React.MouseEvent, f: FolderNode) => showTooltip(e, f, true)}
               onLeave={hideTooltip}
               onFileSelect={onFileSelect}
             />
          )}
        </div>
      </div>

      {/* Tooltip */}
      {tooltip.visible && (
        <div className="absolute z-[100] pointer-events-none bg-surface-dark/95 border border-primary/30 p-2 rounded-lg shadow-2xl backdrop-blur-md"
             style={{ left: tooltip.x, top: tooltip.y }}>
          <div className="text-xs font-bold text-primary-light">{tooltip.isFolder ? 'FOLDER' : 'FILE'}</div>
          <div className="text-sm text-text-primary truncate max-w-[200px]">{tooltip.name}</div>
          <div className="text-xs text-text-secondary">{formatBytes(tooltip.size)}</div>
        </div>
      )}

      {/* Path Indicator */}
      <div className="absolute top-4 left-4 text-[10px] font-mono text-primary/40 uppercase tracking-widest">
        DREAMSCAPE // {currentPath || 'ROOT'}
      </div>
    </div>
  )
}

/* ═══════════════════════════════════════════════════════════════
   MAIN EXPORTED COMPONENT
═══════════════════════════════════════════════════════════════ */

export interface FileTypeTreemapProps {
  allFiles:        Record<string, FileEntry[]>
  activeFilter?:   string | null
  onFilterChange?: (ext: string | null) => void
  onFileSelect?:   (file: FileEntry) => void
  onDeleteFolder?: (path: string) => void
  initialMode?:    'folder' | 'type'
}

export function FileTypeTreemap({
  allFiles,
  activeFilter,
  onFilterChange,
  onFileSelect,
  initialMode = 'folder',
}: FileTypeTreemapProps) {
  const chartRef = useRef<ReactEChartsCore>(null)
  const [groupMode, setGroupMode] = useState<'folder' | 'type'>(initialMode)

  const folderTree = useMemo(() => buildFolderTree(allFiles), [allFiles])
  const rootLabel = folderTree?.name || 'Root'

  const [navPath, setNavPath] = useState<{name: string, fullPath: string | null}[]>([
    { name: rootLabel, fullPath: folderTree?.fullPath ?? null }
  ])

  useEffect(() => {
    if (folderTree) {
      setNavPath([{ name: folderTree.name, fullPath: folderTree.fullPath }])
    }
  }, [folderTree])

  const currentFolderPath = navPath[navPath.length - 1]?.fullPath ?? null

  // ── ECharts type-mode data ───────────────────────────────────
  const { typeTreeData } = useMemo(() => {
    const flatFiles = Object.values(allFiles).flat()
    const getVal    = (s: number) => Math.sqrt(s + 1) * 10

    const structure = new Map<string, Map<string, FileEntry[]>>()
    for (const f of flatFiles) {
      const ext      = ('.' + f.path.split('.').pop()?.toLowerCase()) || '.other'
      const category = CATEGORY_MAP[ext] || 'Other'
      if (!structure.has(category)) structure.set(category, new Map())
      const catMap = structure.get(category)!
      if (!catMap.has(ext)) catMap.set(ext, [])
      catMap.get(ext)!.push(f)
    }

    const nodes: any[] = Array.from(structure.entries())
      .map(([category, extMap]) => {
        const baseColor = COLORS[category] || COLORS.Other
        const children  = Array.from(extMap.entries()).map(([ext, files], i) => {
          const shade     = i === 0 ? baseColor : lightenColor(baseColor, Math.min(0.4, 0.05 * i))
          const fileNodes = files.sort((a, b) => b.size - a.size).map(f => ({
            name:      f.path.split(/[/\\]/).pop() || f.path,
            value:     getVal(f.size),
            realSize:  f.size,
            fileData:  f,
            itemStyle: { color: shade },
          }))
          return {
            name:      ext,
            value:     fileNodes.reduce((s, c) => s + c.value, 0),
            realSize:  files.reduce((s, f) => s + f.size, 0),
            children:  fileNodes,
            itemStyle: { color: shade },
          }
        })
        return {
          name:      category,
          value:     children.reduce((s, c) => s + c.value, 0),
          realSize:  children.reduce((s, c) => s + c.realSize, 0),
          children,
          itemStyle: { color: baseColor },
        }
      })
      .sort((a, b) => b.realSize - a.realSize)

    return { typeTreeData: nodes }
  }, [allFiles])

  // ── Handlers ────────────────────────────────────────────────

  const handleBack = () => setNavPath(p => p.length > 1 ? p.slice(0, -1) : p)
  const handleHome = () => setNavPath([navPath[0]])
  const handleFolderClick = (node: FolderNode) => setNavPath([...navPath, { name: node.name, fullPath: node.fullPath }])

  const option = useMemo(() => ({
    backgroundColor: 'transparent',
    tooltip: {
      formatter: (info: any) => {
        const size = info.data?.realSize ?? info.value
        return `${info.name}<br/>Size: <b>${formatBytes(size)}</b>`
      }
    },
    series: [{
      type: 'treemap',
      data: typeTreeData,
      breadcrumb: { show: false },
      label: { show: true, fontSize: 10 },
      itemStyle: { borderColor: '#050510', borderWidth: 1, gapWidth: 1 },
      levels: [
        { itemStyle: { borderWidth: 4, borderColor: '#050510', gapWidth: 4 } },
        { itemStyle: { borderWidth: 2, borderColor: '#050510', gapWidth: 2 } }
      ]
    }]
  }), [typeTreeData])

  const onEvents = useMemo(() => ({
    click: (params: any) => {
      if (params.data?.fileData && onFileSelect) onFileSelect(params.data.fileData)
      if (onFilterChange) {
        const pathInfo = params.treePathInfo || []
        const extNode = pathInfo.find((p: any) => p.name?.startsWith('.'))
        if (extNode) onFilterChange(extNode.name === activeFilter ? null : extNode.name)
      }
    }
  }), [onFileSelect, onFilterChange, activeFilter])

  return (
    <div className="flex-1 flex flex-col min-h-0 gap-4">
      {/* Controls */}
      <div className="flex items-center justify-between bg-surface-lighter/30 p-3 rounded-2xl border border-white/5 backdrop-blur-sm z-10">
        <div className="flex items-center gap-2">
          <button onClick={handleBack} disabled={navPath.length <= 1} 
                  className="p-2 hover:bg-primary/20 rounded-xl disabled:opacity-20 transition-colors">
            <ChevronLeft className="w-5 h-5" />
          </button>
          <button onClick={handleHome} className="p-2 hover:bg-primary/20 rounded-xl transition-colors">
            <Home className="w-5 h-5" />
          </button>
          <div className="h-4 w-px bg-white/10 mx-2" />
          <div className="flex items-center gap-1 overflow-x-auto no-scrollbar max-w-[400px]">
            {navPath.map((seg, i) => (
              <div key={i} className="flex items-center shrink-0">
                <button onClick={() => setNavPath(navPath.slice(0, i + 1))}
                        className={`text-xs font-medium px-2 py-1 rounded-lg transition-colors ${
                          i === navPath.length - 1 ? 'text-primary-light bg-primary/10' : 'text-text-secondary hover:text-text-primary'
                        }`}>
                  {seg.name}
                </button>
                {i < navPath.length - 1 && <span className="text-white/20 text-[10px]">/</span>}
              </div>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2 bg-surface-dark/50 p-1 rounded-xl border border-white/5">
          <button onClick={() => setGroupMode('folder')}
                  className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                    groupMode === 'folder' ? 'bg-primary text-white shadow-lg shadow-primary/20' : 'text-text-secondary hover:text-text-primary'
                  }`}>
            <Sparkles className="w-4 h-4" /> 3D DREAMSCAPE
          </button>
          <button onClick={() => setGroupMode('type')}
                  className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                    groupMode === 'type' ? 'bg-primary text-white shadow-lg shadow-primary/20' : 'text-text-secondary hover:text-text-primary'
                  }`}>
            <Layers className="w-4 h-4" /> BY FILE TYPE
          </button>
        </div>
      </div>

      {/* Visualizer */}
      <div className="flex-1 relative rounded-3xl overflow-hidden border border-white/5 shadow-2xl bg-[#050510]">
        {groupMode === 'folder' && folderTree ? (
          <IsoMap3DWorld 
            rootNode={folderTree} 
            currentPath={currentFolderPath}
            onFolderClick={handleFolderClick}
            onFileSelect={onFileSelect}
          />
        ) : (
          <ReactEChartsCore ref={chartRef} echarts={echarts} option={option} onEvents={onEvents} style={{ height: '100%', width: '100%' }} />
        )}
      </div>
    </div>
  )
}
