import { useState, useMemo } from 'react'
import { FolderTree, File, Folder, ChevronRight, ChevronDown, Loader2, LayoutGrid, List, Trash2 } from 'lucide-react'
import { useApi, invalidateCache } from '../useApi'
import { getFileTree, removeFolderIndex, type FileEntry } from '../api'
import { FileTypeTreemap } from '../components/FileTypeTreemap'

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

interface TreeNode {
  name: string
  fullPath: string
  children: Map<string, TreeNode>
  files: FileEntry[]
}

/* ── Recursive Node Component ───────────────────────────── */

interface FolderNodeProps {
  node: TreeNode
  depth: number
  onSelect: (file: FileEntry) => void
  selectedPath: string | null
  onDeleteFolder: (path: string) => void
}

function FolderNode({ node, depth, onSelect, selectedPath, onDeleteFolder }: FolderNodeProps) {
  const [open, setOpen] = useState(depth === 0) 

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (confirm(`Are you sure you want to remove the index for this folder and all its contents?\n\nPath: ${node.fullPath}`)) {
      onDeleteFolder(node.fullPath)
    }
  }

  return (
    <div className="select-none">
      <div 
        className={`group flex items-center gap-2 w-full px-2 py-1 rounded-lg transition-colors cursor-pointer ${open ? 'bg-white/5' : 'hover:bg-white/5'}`}
        onClick={() => setOpen(!open)}
      >
        <div className="w-4 h-4 flex items-center justify-center text-text-secondary">
          {node.children.size > 0 || node.files.length > 0 ? (
            open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />
          ) : null}
        </div>
        <Folder className="w-4 h-4 text-primary shrink-0" />
        <span className="text-sm font-medium truncate flex-1">{node.name}</span>
        
        <button 
          onClick={handleDelete}
          className="opacity-0 group-hover:opacity-100 p-1 hover:bg-error/20 hover:text-error rounded transition-all mr-2"
          title="Delete this folder index"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>

      {open && (
        <div className="ml-4 border-l border-white/5 pl-1">
          {Array.from(node.children.values())
            .sort((a, b) => a.name.localeCompare(b.name))
            .map((child) => (
              <FolderNode 
                key={child.fullPath} 
                node={child} 
                depth={depth + 1} 
                onSelect={onSelect} 
                selectedPath={selectedPath}
                onDeleteFolder={onDeleteFolder}
              />
            ))
          }
          
          {node.files
            .sort((a, b) => b.size - a.size)
            .map((f) => {
              const fileName = f.path.split(/[\\/]/).pop() ?? f.path
              const isSelected = f.path === selectedPath
              return (
                <div
                  key={f.path}
                  onClick={() => onSelect(f)}
                  className={`flex items-center gap-2 w-full px-6 py-1 rounded-lg text-left text-sm transition-colors cursor-pointer ${
                    isSelected ? 'bg-primary/20 text-primary-light' : 'hover:bg-white/5 text-text-secondary'
                  }`}
                >
                  <File className="w-3.5 h-3.5 shrink-0 opacity-60" />
                  <span className="truncate flex-1">{fileName}</span>
                  <span className="text-[10px] opacity-40 tabular-nums">{formatSize(f.size)}</span>
                </div>
              )
            })
          }
        </div>
      )}
    </div>
  )
}

/* ── Main Explorer Page ─────────────────────────────────── */

export function ExplorerPage() {
  const { data: tree, loading, refetch } = useApi(getFileTree, { cacheKey: 'file-tree' })
  const [selectedFile, setSelectedFile] = useState<FileEntry | null>(null)
  const [viewMode, setViewMode] = useState<'tree' | 'treemap'>('tree')
  const [activeExtension, setActiveExtension] = useState<string | null>(null)

  const handleDeleteFolder = async (path: string) => {
    try {
      await removeFolderIndex([path])
      invalidateCache('file-tree')
      invalidateCache('insights')
      refetch()
      if (selectedFile?.path.startsWith(path)) setSelectedFile(null)
      alert(`Successfully removed index for: ${path}`)
    } catch (e) {
      alert(`Failed to delete folder index: ${e instanceof Error ? e.message : 'Unknown error'}`)
    }
  }

  const hierarchicalTree = useMemo(() => {
    if (!tree?.folders) return null
    
    const rootNodes: TreeNode[] = []
    
    Object.entries(tree.folders).forEach(([tag, files]) => {
      // Step 1: Normalize root tag
      const normTag = tag.replace(/\\/g, '/').toLowerCase().replace(/\/+$/, '')
      const tagName = tag.split(/[\\/]/).filter(Boolean).pop() || tag;

      const filteredFiles = activeExtension 
        ? files.filter(f => ('.' + f.path.split('.').pop()?.toLowerCase()) === activeExtension.toLowerCase())
        : files;

      if (filteredFiles.length === 0 && activeExtension) return;

      const root: TreeNode = { name: tagName, fullPath: tag, children: new Map(), files: [] }
      
      filteredFiles.forEach(f => {
        const normPath = f.path.replace(/\\/g, '/')
        const normPathLower = normPath.toLowerCase()
        
        let relative = normPath
        if (normPathLower.startsWith(normTag)) {
          // Robustly remove the tag prefix
          relative = normPath.slice(normTag.length).replace(/^\/+/, '')
        }
        
        const parts = relative.split('/').filter(Boolean)
        let current = root
        
        // Skip parts that match root name to avoid "Root > Root > Sub" nesting
        let startIdx = 0;
        while(startIdx < parts.length && parts[startIdx].toLowerCase() === tagName.toLowerCase()) {
          startIdx++;
        }

        for (let i = startIdx; i < parts.length; i++) {
          const part = parts[i]
          if (i === parts.length - 1) {
            current.files.push(f)
          } else {
            if (!current.children.has(part)) {
              current.children.set(part, { 
                name: part, 
                fullPath: current.fullPath + '/' + part, 
                children: new Map(), 
                files: [] 
              })
            }
            current = current.children.get(part)!
          }
        }
      })
      rootNodes.push(root)
    })
    
    return rootNodes.sort((a, b) => a.name.localeCompare(b.name))
  }, [tree, activeExtension])

  const largestFiles = useMemo(() => {
    if (!tree?.folders) return []
    const flat = Object.values(tree.folders).flat()
    const filtered = activeExtension 
      ? flat.filter(f => ('.' + f.path.split('.').pop()?.toLowerCase()) === activeExtension.toLowerCase())
      : flat
    return filtered.sort((a, b) => b.size - a.size).slice(0, 15)
  }, [tree, activeExtension])

  const coldFiles = useMemo(() => {
    if (!tree?.folders) return []
    const flat = Object.values(tree.folders).flat()
    const filtered = activeExtension 
      ? flat.filter(f => ('.' + f.path.split('.').pop()?.toLowerCase()) === activeExtension.toLowerCase())
      : flat
    return filtered.sort((a, b) => (a.usage_count || 0) - (b.usage_count || 0)).slice(0, 15)
  }, [tree, activeExtension])

  return (
    <div className="flex flex-col h-full p-6 animate-fade-in-up overflow-hidden">
      {/* Header */}
      <div className="flex justify-between items-center mb-6 shrink-0">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-3 text-white">
            <FolderTree className="w-7 h-7 text-primary" />
            Explorer
          </h1>
          <p className="text-text-secondary mt-1">
            Browse indexed data
            {tree && (
              <span className="ml-2 text-xs text-primary-light font-mono bg-primary/10 px-2 py-0.5 rounded-full">
                {tree.total_files} files • {formatSize(tree.total_size)}
              </span>
            )}
          </p>
        </div>

        <div className="flex bg-surface-lighter p-1 rounded-xl border border-white/5 shadow-inner">
          <button
            onClick={() => setViewMode('tree')}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-all ${
              viewMode === 'tree' ? 'bg-primary text-white shadow-lg' : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            <List className="w-4 h-4" /> TREE
          </button>
          <button
            onClick={() => setViewMode('treemap')}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-all ${
              viewMode === 'treemap' ? 'bg-primary text-white shadow-lg' : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            <LayoutGrid className="w-4 h-4" /> TREEMAP
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 flex-1 min-h-0">
        {/* Main View Area */}
        <div className="glass-card lg:col-span-8 flex flex-col overflow-hidden p-0 border-white/5 shadow-2xl">
          {loading ? (
            <div className="flex-1 flex items-center justify-center">
              <Loader2 className="w-12 h-12 text-primary animate-spin" />
            </div>
          ) : !hierarchicalTree || hierarchicalTree.length === 0 ? (
            <div className="flex-1 flex items-center justify-center text-text-secondary text-lg">
              No indexed data. Go to Library to add folders.
            </div>
          ) : (
            <div className="flex-1 flex flex-col overflow-hidden">
              {viewMode === 'tree' ? (
                <div className="p-4 space-y-1 overflow-y-auto flex-1 custom-scrollbar">
                  {hierarchicalTree.map((root) => (
                    <FolderNode
                      key={root.fullPath}
                      node={root}
                      depth={0}
                      onSelect={setSelectedFile}
                      selectedPath={selectedFile?.path ?? null}
                      onDeleteFolder={handleDeleteFolder}
                    />
                  ))}
                </div>
              ) : (
                <div className="flex-1 p-2 flex flex-col min-h-0">
                  <FileTypeTreemap 
                    allFiles={tree!.folders} 
                    onFileSelect={setSelectedFile} 
                    onDeleteFolder={handleDeleteFolder}
                    activeFilter={activeExtension}
                    onFilterChange={setActiveExtension}
                    initialMode="folder"
                  />
                </div>
              )}
            </div>
          )}
        </div>

        {/* Sidebar */}
        <div className="lg:col-span-4 flex flex-col gap-4 overflow-hidden h-full">
          {/* Active Filter Tile */}
          {activeExtension && (
            <div className="bg-primary/20 border border-primary/30 rounded-2xl flex items-center justify-between p-4 shrink-0 shadow-lg glow-purple">
              <div className="flex items-center gap-4">
                <div className="bg-primary p-2 rounded-xl shadow-lg">
                  <LayoutGrid className="w-5 h-5 text-white" />
                </div>
                <div className="flex flex-col">
                  <span className="text-[10px] uppercase font-bold text-primary-light tracking-widest leading-tight">Active Filter</span>
                  <span className="text-xl font-black text-white uppercase leading-none">{activeExtension}</span>
                </div>
              </div>
              <button 
                onClick={() => setActiveExtension(null)}
                className="text-[10px] font-black bg-white/10 hover:bg-white/20 px-3 py-2 rounded-lg transition-all border border-white/10"
              >
                CLEAR
              </button>
            </div>
          )}

          {/* Selection Detail Tile (Smaller now) */}
          <div className="glass-card shrink-0 border-white/5 p-4">
            {selectedFile ? (
              <div className="space-y-3">
                <div className="flex items-center gap-3 border-b border-white/5 pb-2">
                  <div className="bg-primary/10 p-2 rounded-xl border border-primary/20 shrink-0">
                    <File className="w-5 h-5 text-primary-light" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <h3 className="font-bold text-sm text-text-primary truncate">{selectedFile.path.split(/[\\/]/).pop()}</h3>
                    <p className="text-[9px] text-primary-light/60 uppercase font-black tracking-widest">{selectedFile.type.replace('.','')}</p>
                  </div>
                </div>
                <dl className="grid grid-cols-2 gap-3">
                  <div className="col-span-2">
                    <dt className="text-[9px] font-black text-text-secondary uppercase tracking-widest mb-1">Path</dt>
                    <dd className="text-[10px] text-text-primary bg-black/30 p-2 rounded-lg break-all font-mono border border-white/5 leading-tight">{selectedFile.path}</dd>
                  </div>
                  <div>
                    <dt className="text-[9px] font-black text-text-secondary uppercase tracking-widest">Size</dt>
                    <dd className="text-sm font-black text-primary-light">{formatSize(selectedFile.size)}</dd>
                  </div>
                  <div>
                    <dt className="text-[9px] font-black text-text-secondary uppercase tracking-widest">Usage</dt>
                    <dd className="text-sm font-black text-white">{selectedFile.usage_count ?? 0}</dd>
                  </div>
                </dl>
              </div>
            ) : (
              <div className="text-center py-4 opacity-30">
                <File className="w-8 h-8 mx-auto mb-1 text-primary" />
                <p className="text-[10px] font-black uppercase tracking-[0.3em]">No Selection</p>
              </div>
            )}
          </div>

          {/* Sidebar Tile: Largest Data (Expanded view) */}
          <div className="glass-card flex-1 min-h-0 flex flex-col p-4 border-white/5">
            <h3 className="text-[10px] font-black uppercase tracking-[0.25em] text-text-secondary mb-3 flex items-center gap-2 shrink-0">
              <div className="w-1 h-3 bg-primary rounded-full"></div> Largest Data
            </h3>
            <div className="space-y-1.5 overflow-y-auto custom-scrollbar pr-2 flex-1">
              {largestFiles.map(f => (
                <div key={f.path} onClick={() => setSelectedFile(f)} className="group flex items-center gap-3 p-2 rounded-xl bg-white/[0.02] hover:bg-primary/10 cursor-pointer transition-all border border-white/5 hover:border-primary/20">
                  <div className="bg-surface-lighter px-1.5 py-1 rounded-lg border border-white/5 shrink-0 text-center min-w-[32px]">
                    <span className="text-[9px] font-black text-primary-light uppercase">{f.type.replace('.', '').slice(0,3) || '??'}</span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[11px] font-bold text-text-primary truncate">{f.path.split(/[\\/]/).pop()}</div>
                    <div className="text-[9px] text-text-secondary font-bold uppercase tracking-tight">{formatSize(f.size)}</div>
                  </div>
                  <ChevronRight className="w-3.5 h-3.5 text-white/5 group-hover:text-primary transition-all" />
                </div>
              ))}
            </div>
          </div>

          {/* Sidebar Tile: Cold Files (Expanded view) */}
          <div className="glass-card flex-1 min-h-0 flex flex-col p-4 border-white/5">
            <h3 className="text-[10px] font-black uppercase tracking-[0.25em] text-text-secondary mb-3 flex items-center gap-2 shrink-0">
              <div className="w-1 h-3 bg-accent rounded-full"></div> Cold Files
            </h3>
            <div className="space-y-1.5 overflow-y-auto custom-scrollbar pr-2 flex-1">
              {coldFiles.map(f => (
                <div key={f.path} onClick={() => setSelectedFile(f)} className="group flex items-center gap-3 p-2 rounded-xl bg-white/[0.02] hover:bg-accent/10 cursor-pointer transition-all border border-white/5 hover:border-accent/20">
                  <div className="bg-surface-lighter px-1.5 py-1 rounded-lg border border-white/5 shrink-0 text-center min-w-[32px]">
                    <span className="text-[9px] font-black text-accent uppercase">{f.type.replace('.', '').slice(0,3) || '??'}</span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[11px] font-bold text-text-primary truncate">{f.path.split(/[\\/]/).pop()}</div>
                    <div className="text-[9px] text-text-secondary font-bold">{f.usage_count || 0} hits</div>
                  </div>
                  <ChevronRight className="w-3.5 h-3.5 text-white/5 group-hover:text-accent transition-all" />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
