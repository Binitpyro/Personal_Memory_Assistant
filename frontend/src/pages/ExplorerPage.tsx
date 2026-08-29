import { useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { FolderTree, File, Folder, ChevronRight, ChevronDown, Loader2, LayoutGrid, List, Trash2, Search, Download, Bot, ScanText } from 'lucide-react'
import { useApi } from '../useApi'
import { getFileTree, removeFolderIndex, getOcrStatus, forceOcr, type FileEntry, type FileTree } from '../api'
import { FileTypeTreemap } from '../components/FileTypeTreemap'
import { CACHE_KEYS } from '../cacheKeys'
import { useOptimisticMutation } from '../useOptimisticMutation'

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

const MAX_VISIBLE_FILES = 100;

interface FolderNodeProps {
  readonly node: TreeNode
  readonly depth: number
  readonly onSelect: (file: FileEntry) => void
  readonly selectedPath: string | null
  readonly onDeleteFolder: (path: string) => void
  readonly deletingPath: string | null
}

function FolderNode({ node, depth, onSelect, selectedPath, onDeleteFolder, deletingPath }: FolderNodeProps) {
  const [open, setOpen] = useState(depth === 0)

  const isDeleting = deletingPath === node.fullPath

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (isDeleting) return
    if (confirm(`Are you sure you want to remove the index for this folder and all its contents?\n\nPath: ${node.fullPath}`)) {
      onDeleteFolder(node.fullPath)
    }
  }

  const sortedFiles = useMemo(() => [...node.files].sort((a, b) => b.size - a.size), [node.files]);
  const visibleFiles = sortedFiles.slice(0, MAX_VISIBLE_FILES);
  const remainingFiles = sortedFiles.length - MAX_VISIBLE_FILES;

  return (
    <div className="select-none">
      <div
        role="button"
        tabIndex={0}
        className={`group flex items-center gap-2 w-full px-2 py-1 rounded-lg transition-colors cursor-pointer text-left ${open ? 'bg-raised' : 'hover:bg-raised'}`}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen(!open); } }}
      >
        <div className="w-4 h-4 flex items-center justify-center text-text-secondary">
          {(node.children.size > 0 || node.files.length > 0) && (
            open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />
          )}
        </div>
        <Folder className="w-4 h-4 text-primary shrink-0" />
        <span className="text-sm font-medium truncate flex-1" title={node.fullPath}>{node.name}</span>

        <button
          onClick={handleDelete}
          disabled={isDeleting}
          className="opacity-0 group-hover:opacity-100 p-1 hover:bg-error/20 hover:text-error rounded transition-all mr-2 disabled:opacity-50 disabled:cursor-not-allowed"
          title="Delete this folder index"
        >
          {isDeleting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
        </button>
      </div>

      {
        open && (
          <div className="ml-4 border-l border-rule pl-1">
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
                  deletingPath={deletingPath}
                />
              ))
            }

            {visibleFiles.map((f) => {
                const fileName = f.path.split(/[\\/]/).pop() ?? f.path
                const isSelected = f.path === selectedPath
                return (
                  <button
                    key={f.path}
                    onClick={() => onSelect(f)}
                    className={`flex items-center gap-2 w-full px-6 py-1 rounded-lg text-left text-sm transition-colors cursor-pointer ${isSelected ? 'bg-primary/20 text-primary-light' : 'hover:bg-raised text-text-secondary'
                      }`}
                  >
                    <File className="w-3.5 h-3.5 shrink-0 opacity-60" />
                    <span className="truncate flex-1">{fileName}</span>
                    <span className="text-[10px] opacity-40 tabular-nums">{formatSize(f.size)}</span>
                  </button>
                )
              })
            }

            {remainingFiles > 0 && (
              <div className="px-6 py-2 text-[10px] italic text-text-secondary opacity-50 flex items-center gap-2">
                <span className="w-1 h-1 rounded-full bg-text-secondary"></span>
                {remainingFiles} more files in this folder
              </div>
            )}
          </div>
        )
      }
    </div >
  )
}

/* ── Main Explorer Page ─────────────────────────────────── */

export function ExplorerPage() {
  const { data: tree, loading } = useApi(getFileTree, { cacheKey: CACHE_KEYS.fileTree })
  const [selectedFile, setSelectedFile] = useState<FileEntry | null>(null)
  const [viewMode, setViewMode] = useState<'tree' | 'treemap'>('tree')
  const [activeExtension, setActiveExtension] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [ocrBusy, setOcrBusy] = useState<string | null>(null)
  const [ocrMessage, setOcrMessage] = useState('')
  const navigate = useNavigate()

  const { data: ocr } = useApi(getOcrStatus, { cacheKey: CACHE_KEYS.ocrStatus })
  const ocrReady = !!ocr?.installed && !!ocr?.enabled

  const handleForceOcr = async (path: string) => {
    setOcrBusy(path)
    setOcrMessage('')
    try {
      const res = await forceOcr(path)
      setOcrMessage(
        res.ok
          ? `Queued ${res.pages_queued ?? 0} page(s) for OCR.`
          : `Could not queue: ${res.error_code ?? 'unknown error'}`,
      )
    } catch (e) {
      setOcrMessage(e instanceof Error ? e.message : 'Could not queue OCR.')
    } finally {
      setOcrBusy(null)
    }
  }

  const handleExportCSV = () => {
    if (!tree?.folders) return
    const flat = Object.values(tree.folders).flat()
    const filtered = flat.filter(f => {
      const extMatch = activeExtension ? ('.' + f.path.split('.').pop()?.toLowerCase()) === activeExtension.toLowerCase() : true
      const searchMatch = searchQuery ? f.path.toLowerCase().includes(searchQuery.toLowerCase()) : true
      return extMatch && searchMatch
    })

    const lines = ['Path,Size_Bytes,Usage_Count,Type']
    filtered.forEach(f => {
      lines.push(`"${f.path}",${f.size},${f.usage_count || 0},"${f.type}"`)
    })

    const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'pma_explorer_export.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  // The folder disappears from the tree on click rather than after the round
  // trip. Previously this had no pending state at all, so the row stayed fully
  // interactive and a double-click sent two removal requests.
  const deleteFolder = useOptimisticMutation<string, unknown, FileTree>({
    mutationFn: (path: string) => removeFolderIndex([path]),
    cacheKey: CACHE_KEYS.fileTree,
    invalidates: [CACHE_KEYS.insights],
    optimistic: (current, path) => {
      if (!current?.folders?.[path]) return current
      const { [path]: removed, ...rest } = current.folders
      return {
        ...current,
        folders: rest,
        total_files: Math.max(0, current.total_files - removed.length),
        total_size: Math.max(0, current.total_size - removed.reduce((n, f) => n + f.size, 0)),
      }
    },
    onSuccess: (_data, path) => {
      if (selectedFile?.path.startsWith(path)) setSelectedFile(null)
    },
    onError: (e) => {
      alert(`Failed to delete folder index: ${e instanceof Error ? e.message : 'Unknown error'}`)
    },
  })

  // Guarded rather than just disabling the button: the treemap has its own
  // delete affordance, and useMutation does not dedupe concurrent calls.
  const handleDeleteFolder = (path: string) => {
    if (deleteFolder.isPending) return
    deleteFolder.mutate(path)
  }
  const deletingPath = deleteFolder.isPending ? deleteFolder.variables : null

  const hierarchicalTree = useMemo(() => {
    if (!tree?.folders) return null

    const rootNodes: TreeNode[] = []

    Object.entries(tree.folders).forEach(([root, files]) => {
      // `root` is the indexed folder's full path (see GET /api/files/tree).
      // Everything below hangs off stripping it from each file path.
      let normRoot = root.replaceAll('\\', '/').toLowerCase();
      while (normRoot.endsWith('/')) { normRoot = normRoot.slice(0, -1); }

      const filteredFiles = files.filter(f => {
        const extMatch = activeExtension ? ('.' + f.path.split('.').pop()?.toLowerCase()) === activeExtension.toLowerCase() : true;
        const searchMatch = searchQuery ? f.path.toLowerCase().includes(searchQuery.toLowerCase()) : true;
        return extMatch && searchMatch;
      });

      if (filteredFiles.length === 0 && activeExtension) return;

      const rootNode: TreeNode = { name: root, fullPath: root, children: new Map(), files: [] }
      // Build child paths with the separator the root actually uses. The
      // removal endpoint matches `files.path` with a LIKE prefix, and that
      // column carries the host separator.
      const sep = root.includes('\\') ? '\\' : '/'

      filteredFiles.forEach(f => {
        const normPath = f.path.replaceAll('\\', '/')
        const normPathLower = normPath.toLowerCase()

        let relative = normPath
        if (normPathLower.startsWith(normRoot)) {
          relative = normPath.slice(normRoot.length);
          while (relative.startsWith('/')) { relative = relative.slice(1); }
        }

        const parts = relative.split('/').filter(Boolean)
        let current = rootNode

        // No root-name skipping here. A subfolder is allowed to share the
        // root's name (D:\College\College), and with the prefix stripped
        // correctly there is nothing left to deduplicate.
        for (let i = 0; i < parts.length; i++) {
          const part = parts[i]
          if (i === parts.length - 1) {
            current.files.push(f)
          } else {
            if (!current.children.has(part)) {
              current.children.set(part, {
                name: part,
                fullPath: current.fullPath + sep + part,
                children: new Map(),
                files: []
              })
            }
            current = current.children.get(part)!
          }
        }
      })
      rootNodes.push(rootNode)
    })

    return rootNodes.sort((a, b) => a.name.localeCompare(b.name))
  }, [tree, activeExtension])

  const largestFiles = useMemo(() => {
    if (!tree?.folders) return []
    const flat = Object.values(tree.folders).flat()
    const filtered = flat.filter(f => {
      const extMatch = activeExtension ? ('.' + f.path.split('.').pop()?.toLowerCase()) === activeExtension.toLowerCase() : true;
      const searchMatch = searchQuery ? f.path.toLowerCase().includes(searchQuery.toLowerCase()) : true;
      return extMatch && searchMatch;
    })
    return [...filtered].sort((a, b) => b.size - a.size).slice(0, 15)
  }, [tree, activeExtension, searchQuery])

  const coldFiles = useMemo(() => {
    if (!tree?.folders) return []
    const flat = Object.values(tree.folders).flat()
    const filtered = flat.filter(f => {
      const extMatch = activeExtension ? ('.' + f.path.split('.').pop()?.toLowerCase()) === activeExtension.toLowerCase() : true;
      const searchMatch = searchQuery ? f.path.toLowerCase().includes(searchQuery.toLowerCase()) : true;
      return extMatch && searchMatch;
    })
    return [...filtered].sort((a, b) => (a.usage_count || 0) - (b.usage_count || 0)).slice(0, 15)
  }, [tree, activeExtension, searchQuery])

  const isEmptyTree = !hierarchicalTree || hierarchicalTree.length === 0;

  const renderMainContent = () => {
    // `&& !tree` matters: useApi reports `isLoading || isFetching`, so this is
    // true on every background refetch too - and with refetchOnWindowFocus on,
    // alt-tabbing back replaced the whole explorer with a spinner. Only show it
    // when there is genuinely nothing to display yet.
    if (loading && !tree) {
      return (
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="w-12 h-12 text-primary animate-spin" />
        </div>
      )
    }

    if (isEmptyTree) {
      return (
        <div className="flex-1 flex items-center justify-center text-text-secondary text-lg">
          No indexed data. Go to Library to add folders.
        </div>
      )
    }

    return (
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
                deletingPath={deletingPath}
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
    )
  }

  return (
    <div className="flex flex-col h-full p-6 animate-fade-in-up overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between mb-6 shrink-0">
        <div>
          <h1 className="font-serif text-2xl font-normal flex items-center gap-3 text-text-primary">
            <FolderTree className="w-7 h-7 text-primary" />
            Explorer
          </h1>
          <p className="text-text-secondary mt-1 text-sm flex items-center gap-2">
            Browse indexed data
            {tree && (
              <span className="text-xs text-primary font-bold bg-primary/10 px-2 py-0.5 rounded-full">
                {tree.total_files} files • {formatSize(tree.total_size)}
              </span>
            )}
          </p>
        </div>

        <div className="flex items-center gap-4">
          <div className="relative">
            <Search className="w-4 h-4 text-text-secondary absolute left-3 top-1/2 -translate-y-1/2 opacity-50" />
            <input
              type="text"
              aria-label="Filter indexed files by path or name"
              placeholder="Filter files..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="pl-9 pr-4 py-2 bg-raised rounded-xl border border-rule text-sm text-text-primary placeholder:text-text-secondary focus:outline-none focus:ring-2 focus:ring-primary/40 shadow-inner w-56"
            />
          </div>
          <button
            onClick={handleExportCSV}
            aria-label="Export file list to CSV format"
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold text-text-secondary hover:text-text-primary hover:bg-raised border border-rule transition-all shadow-sm"
          >
            <Download className="w-4 h-4" /> CSV
          </button>

          <div className="flex bg-raised p-1 rounded-xl border border-rule shadow-inner ml-2">
            <button
              onClick={() => setViewMode('tree')}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-all ${viewMode === 'tree' ? 'bg-plate text-on-plate shadow-lg' : 'text-text-secondary hover:text-text-primary'
                }`}
            >
              <List className="w-4 h-4" /> TREE
            </button>
            <button
              onClick={() => setViewMode('treemap')}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-all ${viewMode === 'treemap' ? 'bg-plate text-on-plate shadow-lg' : 'text-text-secondary hover:text-text-primary'
                }`}
            >
              <LayoutGrid className="w-4 h-4" /> TREEMAP
            </button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 flex-1 min-h-0">
        {/* Main View Area */}
        <div className="glass-card lg:col-span-8 flex flex-col overflow-hidden p-0">
          {renderMainContent()}
        </div>

        {/* Sidebar */}
        <div className="lg:col-span-4 flex flex-col gap-4 overflow-hidden h-full">
          {/* Active Filter Tile */}
          {activeExtension && (
            <div className="glass rounded-2xl flex items-center justify-between p-4 shrink-0">
              <div className="flex items-center gap-4">
                <div className="bg-plate p-2 rounded-xl shadow-lg">
                  <LayoutGrid className="w-5 h-5 text-on-plate" />
                </div>
                <div className="flex flex-col">
                  <span className="text-[10px] uppercase font-bold text-primary tracking-widest leading-tight">Active Filter</span>
                  <span className="text-xl font-black text-text-primary uppercase leading-none">{activeExtension}</span>
                </div>
              </div>
              <button
                onClick={() => setActiveExtension(null)}
                className="text-[10px] font-black bg-raised text-primary hover:bg-surface px-3 py-2 rounded-lg transition-all border border-primary/10"
              >
                CLEAR
              </button>
            </div>
          )}

          {/* Selection Detail Tile */}
          <div className="glass-card shrink-0 p-4">
            {selectedFile ? (
              <div className="space-y-3">
                <div className="flex items-center gap-3 border-b border-rule pb-2">
                  <div className="bg-primary/10 p-2 rounded-xl border border-primary/20 shrink-0">
                    <File className="w-5 h-5 text-primary" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <h3 className="font-bold text-sm text-text-primary truncate">{selectedFile.path.split(/[\\/]/).pop()}</h3>
                    <p className="text-[9px] text-primary uppercase font-black tracking-widest">{selectedFile.type.replace('.', '')}</p>
                  </div>
                </div>
                <dl className="grid grid-cols-2 gap-3">
                  <div className="col-span-2">
                    <dt className="text-[9px] font-black text-text-secondary uppercase tracking-widest mb-1">Path</dt>
                    <dd className="text-[10px] text-text-primary bg-raised p-2 rounded-lg break-all font-mono border border-rule leading-tight">{selectedFile.path}</dd>
                  </div>
                  <div>
                    <dt className="text-[9px] font-black text-text-secondary uppercase tracking-widest">Size</dt>
                    <dd className="text-sm font-black text-primary">{formatSize(selectedFile.size)}</dd>
                  </div>
                  <div>
                    <dt className="text-[9px] font-black text-text-secondary uppercase tracking-widest">Usage</dt>
                    <dd className="text-sm font-black text-text-primary">{selectedFile.usage_count ?? 0}</dd>
                  </div>
                </dl>
                <button
                  onClick={() => navigate('/search', { state: { query: `Summarize or explain this file: ${selectedFile.path}` } })}
                  className="w-full mt-2 flex items-center justify-center gap-2 py-2.5 bg-primary/10 hover:bg-primary/20 text-primary rounded-xl text-sm font-bold transition-all border border-primary/20"
                >
                  <Bot className="w-4 h-4" />
                  Ask AI about this file
                </button>
                {/* The detection gate only spots *missing* text, never wrong
                    text. This is the manual override for a PDF whose text
                    layer extracts but is scrambled or mis-mapped. */}
                {ocrReady && selectedFile.type.toLowerCase() === '.pdf' && (
                  <button
                    onClick={() => handleForceOcr(selectedFile.path)}
                    disabled={ocrBusy === selectedFile.path}
                    className="w-full mt-2 flex items-center justify-center gap-2 py-2.5 bg-primary/5 hover:bg-primary/15 text-primary rounded-xl text-sm font-bold transition-all border border-primary/20 disabled:opacity-50"
                  >
                    {ocrBusy === selectedFile.path
                      ? <Loader2 className="w-4 h-4 animate-spin" />
                      : <ScanText className="w-4 h-4" />}
                    Force OCR
                  </button>
                )}
                {ocrMessage && (
                  <p className="mt-2 text-[10px] text-center text-text-secondary">{ocrMessage}</p>
                )}
              </div>
            ) : (
              // opacity-30 put this text at roughly a third of its measured
              // ratio. The tertiary token already means "quiet" and stays legible.
              <div className="text-center py-4">
                <File className="w-8 h-8 mx-auto mb-1 text-text-tertiary" aria-hidden />
                <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-text-tertiary">No Selection</p>
              </div>
            )}
          </div>

          {/* Sidebar Tile: Largest Data */}
          <div className="glass-card flex-1 min-h-0 flex flex-col p-4">
            <h3 className="text-[10px] font-black uppercase tracking-[0.25em] text-text-secondary mb-3 flex items-center gap-2 shrink-0">
              <div className="w-1 h-3 bg-primary rounded-full"></div> Largest Data
            </h3>
            <div className="space-y-1.5 overflow-y-auto custom-scrollbar pr-2 flex-1">
              {largestFiles.map(f => (
                <button
                  key={f.path}
                  onClick={() => setSelectedFile(f)}
                  className="w-full text-left group flex items-center gap-3 p-2 rounded-xl bg-raised hover:bg-primary/10 cursor-pointer transition-all border border-rule hover:border-primary/20"
                >
                  <div className="bg-surface px-1.5 py-1 rounded-lg border border-edge shrink-0 text-center min-w-[32px]">
                    <span className="text-[9px] font-black text-primary uppercase">{f.type.replace('.', '').slice(0, 3) || '??'}</span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[11px] font-bold text-text-primary truncate">{f.path.split(/[\\/]/).pop()}</div>
                    <div className="text-[9px] text-text-secondary font-bold uppercase tracking-tight">{formatSize(f.size)}</div>
                  </div>
                  <ChevronRight className="w-3.5 h-3.5 text-text-secondary/20 group-hover:text-primary transition-all" />
                </button>
              ))}
            </div>
          </div>

          {/* Sidebar Tile: Cold Files */}
          <div className="glass-card flex-1 min-h-0 flex flex-col p-4">
            <h3 className="text-[10px] font-black uppercase tracking-[0.25em] text-text-secondary mb-3 flex items-center gap-2 shrink-0">
              <div className="w-1 h-3 bg-accent rounded-full"></div> Cold Files
            </h3>
            <div className="space-y-1.5 overflow-y-auto custom-scrollbar pr-2 flex-1">
              {coldFiles.map(f => (
                <button
                  key={f.path}
                  onClick={() => setSelectedFile(f)}
                  className="w-full text-left group flex items-center gap-3 p-2 rounded-xl bg-raised hover:bg-accent/10 cursor-pointer transition-all border border-rule hover:border-accent/20"
                >
                  <div className="bg-surface px-1.5 py-1 rounded-lg border border-edge shrink-0 text-center min-w-[32px]">
                    <span className="text-[9px] font-black text-accent uppercase">{f.type.replace('.', '').slice(0, 3) || '??'}</span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[11px] font-bold text-text-primary truncate">{f.path.split(/[\\/]/).pop()}</div>
                    <div className="text-[9px] text-text-secondary font-bold">{f.usage_count || 0} hits</div>
                  </div>
                  <ChevronRight className="w-3.5 h-3.5 text-text-secondary/20 group-hover:text-accent transition-all" />
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
