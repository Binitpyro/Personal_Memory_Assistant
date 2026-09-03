import { useState, useMemo, useCallback } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { File, Folder, ChevronRight, ChevronDown, Loader2, LayoutGrid, List, Trash2, Search, Download, Bot, ScanText } from 'lucide-react'
import { toast } from 'sonner'
import { useApi } from '../useApi'
import { getFileTree, removeFolderIndex, getOcrStatus, forceOcr, type FileEntry, type FileTree } from '../api'
import { formatBytes } from '../utils/treeBuilder'
import { FileTypeTreemap } from '../components/FileTypeTreemap'
import { CACHE_KEYS } from '../cacheKeys'
import { useOptimisticMutation } from '../useOptimisticMutation'
import {
  Badge, Button, buttonClasses, EmptyState, Field, LabelSlip, Panel, SkeletonText, SpecimenCard,
} from '../components/ui'



/** The three-letter shelf mark a file carries in the sidebar lists. */
function typeMark(type: string): string {
  return type.replace('.', '').slice(0, 3).toUpperCase() || '??'
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

  // The confirmation itself now lives in the page's `handleDeleteFolder`, as a
  // sonner action-toast. `confirm()` blocks the event loop and draws a platform
  // dialog that belongs to no theme; SearchPage.tsx and DiagnosticsPage.tsx
  // already document that trade and use the toast.
  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (isDeleting) return
    onDeleteFolder(node.fullPath)
  }

  const sortedFiles = useMemo(() => [...node.files].sort((a, b) => b.size - a.size), [node.files]);
  const visibleFiles = sortedFiles.slice(0, MAX_VISIBLE_FILES);
  const remainingFiles = sortedFiles.length - MAX_VISIBLE_FILES;

  return (
    <div className="select-none">
      {/* Two SIBLING buttons, not a button inside a role="button" div. The
          delete control was nested inside the disclosure, which is invalid in
          both directions: a click on it also toggled the folder, and AT saw one
          control containing another. */}
      <div className={`group flex items-center gap-2 w-full px-2 py-1 rounded-sm transition-colors ${open ? 'bg-surface' : 'hover:bg-surface'}`}>
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
          className="flex items-center gap-2 min-w-0 flex-1 text-left cursor-pointer"
        >
          <span className="w-4 h-4 flex items-center justify-center text-text-secondary shrink-0">
            {(node.children.size > 0 || node.files.length > 0) && (
              open ? <ChevronDown className="w-3.5 h-3.5" aria-hidden /> : <ChevronRight className="w-3.5 h-3.5" aria-hidden />
            )}
          </span>
          <Folder className="w-4 h-4 text-primary shrink-0" aria-hidden />
          <span className="text-sm font-medium truncate" title={node.fullPath}>{node.name}</span>
        </button>

        <button
          type="button"
          onClick={handleDelete}
          disabled={isDeleting}
          className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100 p-1 text-text-secondary hover:text-error rounded-xs transition-[opacity,color] disabled:opacity-50 disabled:cursor-not-allowed mr-2"
          aria-label={`Delete the index for ${node.name}`}
        >
          {isDeleting
            ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden />
            : <Trash2 className="w-3.5 h-3.5" aria-hidden />}
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
                    type="button"
                    style={{ contentVisibility: 'auto', containIntrinsicSize: 'auto 28px' }}
                    onClick={() => onSelect(f)}
                    className={`flex items-center gap-2 w-full px-6 py-1 rounded-sm text-left text-sm transition-colors cursor-pointer ${isSelected ? 'bg-surface text-primary' : 'hover:bg-surface text-text-secondary'
                      }`}
                  >
                    <File className="w-3.5 h-3.5 shrink-0 text-text-tertiary" />
                    <span className="truncate flex-1">{fileName}</span>
                    <span className="font-mono text-[10px] text-text-tertiary tabular-nums">{formatBytes(f.size)}</span>
                  </button>
                )
              })
            }

            {remainingFiles > 0 && (
              <div className="px-6 py-2 font-mono text-[10px] text-text-tertiary">
                {remainingFiles} more files in this folder
              </div>
            )}
          </div>
        )
      }
    </div >
  )
}

/**
 * One clickable row in the Largest / Cold lists.
 *
 * `LabelSlip` is the same mono-mark-over-name pairing the nav's drawer fronts
 * use, so a file in the sidebar and a drawer in the rail read as the same
 * catalogue. This replaces a tinted `bg-primary/10` chip per row.
 */
function FileRow({ file, extent, onSelect }: Readonly<{
  file: FileEntry
  extent: string
  onSelect: (f: FileEntry) => void
}>) {
  return (
    <button
      onClick={() => onSelect(file)}
      className="w-full text-left group px-2 py-1.5 rounded-sm hover:bg-surface transition-colors cursor-pointer"
      title={file.path}
    >
      <LabelSlip
        mark={typeMark(file.type)}
        name={file.path.split(/[\\/]/).pop() ?? file.path}
        extent={extent}
      />
    </button>
  )
}

/** A sidebar list. Header is a plain serif line; the section's position is its category. */
function SidebarList({ title, files, extentOf, onSelect }: Readonly<{
  title: string
  files: FileEntry[]
  extentOf: (f: FileEntry) => string
  onSelect: (f: FileEntry) => void
}>) {
  return (
    <Panel className="flex-1 min-h-0 flex flex-col p-4">
      <h3 className="font-serif text-base font-medium text-text-primary mb-3 shrink-0 border-b border-rule pb-2">
        {title}
      </h3>
      <div className="space-y-0.5 overflow-y-auto custom-scrollbar pr-2 flex-1">
        {files.map(f => (
          <FileRow key={f.path} file={f} extent={extentOf(f)} onSelect={onSelect} />
        ))}
      </div>
    </Panel>
  )
}

/* ── Main Explorer Page ─────────────────────────────────── */

export function ExplorerPage() {
  const { data: tree, loading } = useApi(getFileTree, { cacheKey: CACHE_KEYS.fileTree })
  const [selectedFile, setSelectedFile] = useState<FileEntry | null>(null)
  // Filters, the view mode and the active extension live in the URL, so a
  // particular view of the corpus is linkable and survives a reload. They were
  // useState, which made every one of them unreachable except by re-clicking.
  const [searchParams, setSearchParams] = useSearchParams()
  const viewMode: 'tree' | 'treemap' = searchParams.get('view') === 'treemap' ? 'treemap' : 'tree'
  const activeExtension = searchParams.get('ext')
  const searchQuery = searchParams.get('q') ?? ''

  // `replace` so typing in the filter box does not push one history entry per
  // keystroke; the view toggle is a real navigation and pushes.
  const setParam = useCallback(
    (key: string, value: string | null, replace = true) => {
      setSearchParams(
        prev => {
          const next = new URLSearchParams(prev)
          if (value === null || value === '') next.delete(key)
          else next.set(key, value)
          return next
        },
        { replace },
      )
    },
    [setSearchParams],
  )

  const setViewMode = useCallback((v: 'tree' | 'treemap') => setParam('view', v === 'tree' ? null : v, false), [setParam])
  const setActiveExtension = useCallback((ext: string | null) => setParam('ext', ext), [setParam])
  const setSearchQuery = useCallback((q: string) => setParam('q', q), [setParam])
  const [ocrBusy, setOcrBusy] = useState<string | null>(null)
  const [ocrMessage, setOcrMessage] = useState('')

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
      toast.error(
        e instanceof Error ? e.message : 'Could not remove the folder index.',
      )
    },
  })

  // Guarded rather than just disabling the button: the treemap has its own
  // delete affordance, and useMutation does not dedupe concurrent calls.
  const handleDeleteFolder = (path: string) => {
    if (deleteFolder.isPending) return
    toast('Remove the index for this folder?', {
      description: `${path} — the files on disk are untouched. Only PMA's index of them is removed.`,
      action: {
        label: 'Remove',
        onClick: () => {
          if (deleteFolder.isPending) return
          deleteFolder.mutate(path)
        },
      },
      cancel: { label: 'Cancel', onClick: () => {} },
    })
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
      // A skeleton in the shape of the tree, rather than a centred spinner: it
      // says what is coming instead of only that something is.
      return (
        <div className="flex-1 p-4">
          <SkeletonText lines={12} />
        </div>
      )
    }

    if (isEmptyTree) {
      return (
        <div className="flex-1 p-6 overflow-y-auto">
          <EmptyState
            title="Nothing is indexed yet"
            body="Explorer shows what PMA has already read. Add a folder in Library and it will appear here."
            actions={
              <Link className={buttonClasses({ variant: 'plate' })} to="/library">
                Go to Library
              </Link>
            }
          />
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
      <div className="flex items-start justify-between gap-6 mb-6 shrink-0">
        <div className="min-w-0">
          <div className="font-mono text-[10px] tracking-[0.16em] uppercase text-text-tertiary">
            III · EXPL
          </div>
          <h1 className="font-serif text-2xl font-normal text-text-primary leading-tight">
            Explorer
          </h1>
          <p className="text-text-secondary mt-1 text-sm flex items-center gap-3">
            Browse indexed data
            {tree && (
              <Badge mono>
                {tree.total_files} files · {formatBytes(tree.total_size)}
              </Badge>
            )}
          </p>
        </div>

        <div className="flex items-center gap-3 shrink-0">
          <div className="relative">
            <Search className="w-4 h-4 text-text-tertiary absolute left-3 top-1/2 -translate-y-1/2" aria-hidden />
            <input
              type="search"
              spellCheck={false}
              autoComplete="off"
              aria-label="Filter indexed files by path or name"
              placeholder="e.g. report.pdf…"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="glass-input pl-9 pr-4 py-2 text-sm rounded-sm w-56"
            />
          </div>

          <Button
            variant="secondary"
            onClick={handleExportCSV}
            aria-label="Export file list to CSV format"
            icon={<Download className="w-4 h-4" />}
          >
            CSV
          </Button>

          {/* Two views of one thing, so a bordered segmented group rather than
              two buttons - the same idiom as the theme switch in the rail. */}
          <div className="inline-flex border border-edge rounded-sm overflow-hidden" role="group" aria-label="View mode">
            <button
              type="button"
              onClick={() => setViewMode('tree')}
              aria-pressed={viewMode === 'tree'}
              className={`flex items-center gap-2 h-10 px-4 font-mono text-[11px] tracking-[0.12em] uppercase transition-colors ${
                viewMode === 'tree' ? 'bg-plate text-on-plate' : 'text-text-secondary hover:bg-surface'
              }`}
            >
              <List className="w-4 h-4" /> Tree
            </button>
            <button
              type="button"
              onClick={() => setViewMode('treemap')}
              aria-pressed={viewMode === 'treemap'}
              className={`flex items-center gap-2 h-10 px-4 font-mono text-[11px] tracking-[0.12em] uppercase border-l border-edge transition-colors ${
                viewMode === 'treemap' ? 'bg-plate text-on-plate' : 'text-text-secondary hover:bg-surface'
              }`}
            >
              <LayoutGrid className="w-4 h-4" /> Treemap
            </button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 flex-1 min-h-0">
        {/* Main View Area */}
        <Panel className="lg:col-span-8 flex flex-col overflow-hidden">
          {renderMainContent()}
        </Panel>

        {/* Sidebar */}
        <div className="lg:col-span-4 flex flex-col gap-4 overflow-hidden h-full">
          {/* Active Filter Tile */}
          {activeExtension && (
            <Panel className="flex items-center justify-between gap-3 p-4 shrink-0">
              <div className="min-w-0">
                <div className="font-mono text-[10px] tracking-[0.16em] uppercase text-text-tertiary">
                  Active filter
                </div>
                <div className="font-serif text-xl leading-tight truncate">{activeExtension}</div>
              </div>
              <Button variant="quiet" size="sm" onClick={() => setActiveExtension(null)}>
                Clear
              </Button>
            </Panel>
          )}

          {/* Selection Detail Tile */}
          {selectedFile ? (
            // SpecimenCard + Field is what these primitives were extracted for:
            // a mounted specimen with its ruled label in fixed positions, so the
            // eye reads down a column. Fields carry exactly what `FileEntry`
            // exposes - path, size, type, usage_count - and nothing invented.
            <SpecimenCard
              className="shrink-0"
              name={selectedFile.path.split(/[\\/]/).pop() ?? selectedFile.path}
              kind={typeMark(selectedFile.type)}
            >
              <Field label="Size">{formatBytes(selectedFile.size)}</Field>
              <Field label="Usage">{selectedFile.usage_count ?? 0}</Field>
              <div className="border-t border-rule pt-2 mt-0">
                <div className="font-mono text-[9px] tracking-[0.14em] uppercase text-text-tertiary mb-1">Path</div>
                <div className="font-mono text-[10px] text-text-secondary break-all leading-snug">
                  {selectedFile.path}
                </div>
              </div>

              <div className="flex flex-col gap-2 mt-4">
                <Link
                  to="/search"
                  state={{ query: `Summarize or explain this file: ${selectedFile.path}` }}
                  className={buttonClasses({ variant: 'secondary', size: 'sm', className: 'w-full' })}
                >
                  <Bot className="w-4 h-4" aria-hidden />
                  Ask about this file
                </Link>
                {/* The detection gate only spots *missing* text, never wrong
                    text. This is the manual override for a PDF whose text
                    layer extracts but is scrambled or mis-mapped. */}
                {ocrReady && selectedFile.type.toLowerCase() === '.pdf' && (
                  <Button
                    variant="quiet"
                    size="sm"
                    className="w-full"
                    onClick={() => handleForceOcr(selectedFile.path)}
                    loading={ocrBusy === selectedFile.path}
                    icon={<ScanText className="w-4 h-4" />}
                  >
                    Force OCR
                  </Button>
                )}
                {ocrMessage && (
                  <p className="font-mono text-[10px] text-text-tertiary m-0">{ocrMessage}</p>
                )}
              </div>
            </SpecimenCard>
          ) : (
            // opacity-30 put this text at roughly a third of its measured
            // ratio. The tertiary token already means "quiet" and stays legible.
            <Panel className="shrink-0 p-4">
              <div className="text-center py-4">
                <File className="w-8 h-8 mx-auto mb-1 text-text-tertiary" aria-hidden />
                <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-text-tertiary m-0">No Selection</p>
              </div>
            </Panel>
          )}

          {/* One accent for the page. These two lists used to be brass and
              `accent` respectively, which put two accent families on one
              column; rank is carried by the heading, not by a second hue. */}
          <SidebarList
            title="Largest data"
            files={largestFiles}
            extentOf={f => formatBytes(f.size)}
            onSelect={setSelectedFile}
          />
          <SidebarList
            title="Cold files"
            files={coldFiles}
            extentOf={f => `${f.usage_count || 0} hits`}
            onSelect={setSelectedFile}
          />
        </div>
      </div>
    </div>
  )
}
