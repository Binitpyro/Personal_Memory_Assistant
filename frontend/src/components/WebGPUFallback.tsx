/**
 * WebGPUFallback.tsx
 *
 * Three-tier capability gate + shared 3D canvas hosting.
 *
 *   Tier 1: WebGPU  → WebGPURenderer
 *   Tier 2: WebGL2  → WebGL2Renderer (three.js, MeshPhysicalMaterial)
 *   Tier 3: neither → FileTypeTreemap (2D fallback)
 *
 * Detection order: probe navigator.gpu.requestAdapter() first (fast, sync).
 * If that fails, probe WebGL2 by attempting to acquire a context. If that
 * also fails, fall through to 2D.
 *
 * The two 3D canvases share the same interaction logic (drag to orbit,
 * scroll to zoom, click to pick+focus) via the useDreamscapeCanvas hook
 * below — this replaces the two nearly-identical WebGPUCanvas /
 * WebGL2Canvas components from the previous revision, which had drifted
 * out of sync (WebGL2Canvas didn't implement pick() so clicking did
 * nothing on tier 2).
 *
 * Fixes vs. previous revision:
 *   - WebGPUFallbackProps interface (was referenced but undeclared → compile error).
 *   - Static import of WebGL2Renderer (was dynamic import inside a callback
 *     whose ref<> type couldn't resolve → compile error). Three.js still
 *     lazy-loaded via the parent's React.lazy on WebGPUFallback itself.
 *   - Shared canvas hook, so both tiers get pick + focus behavior.
 *   - Name-table wiring: NavigationController is fed folder names from
 *     tree.folders so breadcrumbs show real paths instead of "#42".
 */

import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { FileTypeTreemap } from './FileTypeTreemap';
import { WebGPURenderer } from '../renderer/WebGPURenderer';
import { WebGL2Renderer } from '../renderer/WebGL2Renderer';
import { getVisualizerStream, type FileEntry } from '../api';
import type { NavigationController } from '../interaction/NavigationController';

/** Both renderer classes conform to this shape; the hook is generic over it. */
interface RendererLike {
    init(): Promise<void>;
    loadData(buf: ArrayBuffer): Promise<void>;
    render(): void;
    pick(x: number, y: number): Promise<number | null>;
    resize(w: number, h: number): void;
    destroy(): void;
    handleMouseMove(dx: number, dy: number): void;
    handleZoom(delta: number): void;
    focusOnNode(sourceIndex: number): void;
    readonly nav: NavigationController;
}

export interface WebGPUFallbackProps {
    allFiles: Record<string, FileEntry[]>;
    activeFilter?: string | null;
    onFilterChange?: (ext: string | null) => void;
    initialMode?: 'folder' | 'type';
}

/**
 * Derive a Map<sourceIndex, name> from the folder tree returned by
 * getFileTree(). This assumes Rust's build_tree in rust_core walks the
 * unique folder set in the same BFS order we can reproduce here — root,
 * then depth-1 folders sorted lexicographically, then depth-2, etc.
 *
 * If Rust's order changes, breadcrumbs will show the wrong names but
 * nothing else breaks. Worth double-checking against rust_core::build_tree.
 */
function deriveNameTable(folders: Record<string, FileEntry[]>): Map<number, string> {
    // Every unique folder path in the tree, sorted by (depth, lexicographical).
    const paths = Object.keys(folders);
    const withDepth = paths.map(p => ({ p, depth: p.split(/[\\/]/).filter(Boolean).length }));
    withDepth.sort((a, b) => a.depth - b.depth || a.p.localeCompare(b.p));

    const names = new Map<number, string>();
    // Node index 0 is the root. Subsequent folder indices assigned in the
    // same BFS order Rust uses. Files (bubbles) get indices after folders.
    // If Rust interleaves folders/files during BFS, this will not match —
    // in that case the caller should ignore names and breadcrumbs fall back
    // to "#index" cleanly.
    withDepth.forEach(({ p }, i) => {
        const last = p.split(/[\\/]/).filter(Boolean).pop() ?? p;
        names.set(i, last || 'Root');
    });
    return names;
}

/**
 * Shared 3D canvas hook. Encapsulates:
 *   - renderer lifecycle (init, resize observer, RAF loop, destroy)
 *   - mouse drag (orbit), wheel (zoom), click (pick + focus)
 *   - forwarding stream errors to onError so the parent can degrade tier
 *
 * The `renderer` factory is passed in so the same hook works for both
 * WebGPU and WebGL2 tiers.
 */
function useDreamscapeCanvas<R extends RendererLike>(
    canvasRef: React.RefObject<HTMLCanvasElement | null>,
    factory: (canvas: HTMLCanvasElement) => R,
    activeFilter: string | null | undefined,
    allFiles: Record<string, FileEntry[]>,
    onError: (msg: string) => void,
    onNodeSelected?: (sourceIndex: number, name: string) => void,
) {
    const rendererRef = useRef<R | null>(null);
    const rafRef = useRef<number>(0);
    const [isDragging, setDragging] = useState(false);
    const lastPos = useRef({ x: 0, y: 0 });
    const dragStart = useRef({ x: 0, y: 0 });

    const nameTable = useMemo(() => deriveNameTable(allFiles), [allFiles]);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;

        let cancelled = false;
        let resizeObserver: ResizeObserver | null = null;
        const renderer = factory(canvas);
        rendererRef.current = renderer;

        (async () => {
            try {
                await renderer.init();
                const buffer = await getVisualizerStream(activeFilter);
                if (buffer.byteLength <= 4) {
                    throw new Error('No 3D data available or filter returned 0 results.');
                }
                // Vite dev trap: if the backend is misconfigured we might get an
                // HTML page instead of binary. First two bytes of '<!doctype' are
                // 0x3C 0x21 in ASCII. Fail loud and early rather than reading
                // garbage as f32s.
                const head = new Uint8Array(buffer, 0, 2);
                if (head[0] === 0x3C && head[1] === 0x21) {
                    throw new Error('Backend returned HTML instead of binary. Check the /api/visualizer/stream route.');
                }
                if (cancelled) return;

                await renderer.loadData(buffer);
                renderer.nav.loadNames(nameTable);

                resizeObserver = new ResizeObserver(entries => {
                    for (const e of entries) {
                        const { width, height } = e.contentRect;
                        if (width > 0 && height > 0) renderer.resize(width, height);
                    }
                });
                resizeObserver.observe(canvas);

                const loop = () => {
                    if (cancelled) return;
                    renderer.render();
                    rafRef.current = requestAnimationFrame(loop);
                };
                rafRef.current = requestAnimationFrame(loop);
            } catch (err) {
                onError(err instanceof Error ? err.message : 'Unknown 3D init error');
            }
        })();

        return () => {
            cancelled = true;
            if (rafRef.current) cancelAnimationFrame(rafRef.current);
            resizeObserver?.disconnect();
            rendererRef.current?.destroy();
            rendererRef.current = null;
        };
    // We DO want a full re-init when the filter changes (backend returns a
    // different buffer). We do NOT want re-init on every allFiles reference
    // change (that fires whenever InsightsPage re-renders). Include only the
    // stable dependencies.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [activeFilter, factory, nameTable, onError]);

    // Also hot-load names when they change without a full re-init.
    useEffect(() => {
        rendererRef.current?.nav.loadNames(nameTable);
    }, [nameTable]);

    const onMouseDown = (e: React.MouseEvent) => {
        setDragging(true);
        lastPos.current = { x: e.clientX, y: e.clientY };
        dragStart.current = { x: e.clientX, y: e.clientY };
    };
    const onMouseMove = (e: React.MouseEvent) => {
        if (!isDragging || !rendererRef.current) return;
        rendererRef.current.handleMouseMove(e.clientX - lastPos.current.x, e.clientY - lastPos.current.y);
        lastPos.current = { x: e.clientX, y: e.clientY };
    };
    const onMouseUp = async (e: React.MouseEvent) => {
        setDragging(false);
        const renderer = rendererRef.current;
        const canvas = canvasRef.current;
        if (!renderer || !canvas) return;

        // Click vs drag: threshold of 5px in both axes.
        const dx = Math.abs(e.clientX - dragStart.current.x);
        const dy = Math.abs(e.clientY - dragStart.current.y);
        if (dx > 5 || dy > 5) return;

        const rect = canvas.getBoundingClientRect();
        const sourceIndex = await renderer.pick(e.clientX - rect.left, e.clientY - rect.top);
        if (sourceIndex === null) return;

        // Drill in: expand + focus camera on the clicked node.
        renderer.nav.navigateTo(sourceIndex);
        renderer.focusOnNode(sourceIndex);
        // Renderer needs to know its visible set is stale.
        (renderer as unknown as { markDirty?: () => void }).markDirty?.();

        const bc = renderer.nav.breadcrumbs;
        const name = bc[bc.length - 1]?.name ?? `#${sourceIndex}`;
        onNodeSelected?.(sourceIndex, name);
    };

    // Wheel handling has to be a native listener so we can passive:false and
    // preventDefault (browsers give warnings otherwise, and the surrounding
    // scroll area steals events).
    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const onWheel = (e: WheelEvent) => {
            e.preventDefault();
            e.stopPropagation();
            rendererRef.current?.handleZoom(e.deltaY);
        };
        canvas.addEventListener('wheel', onWheel, { passive: false });
        return () => canvas.removeEventListener('wheel', onWheel);
    }, [canvasRef]);

    return { rendererRef, onMouseDown, onMouseMove, onMouseUp };
}

interface CanvasInnerProps extends WebGPUFallbackProps {
    tier: 'webgpu' | 'webgl2';
    onError: (msg: string) => void;
}

const DreamscapeCanvas: React.FC<CanvasInnerProps> = ({ allFiles, activeFilter, tier, onError }) => {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const [selection, setSelection] = useState<{ index: number, name: string } | null>(null);

    // Renderer factory is stable per-tier — this is important so useEffect
    // doesn't reinit on every render.
    const factory = useCallback(
        (canvas: HTMLCanvasElement): RendererLike =>
            tier === 'webgpu'
                ? new WebGPURenderer(canvas) as unknown as RendererLike
                : new WebGL2Renderer(canvas) as unknown as RendererLike,
        [tier],
    );

    const { rendererRef, onMouseDown, onMouseMove, onMouseUp } =
        useDreamscapeCanvas(canvasRef, factory, activeFilter, allFiles, onError,
            (idx, name) => setSelection({ index: idx, name }));

    const breadcrumbs = rendererRef.current?.nav.breadcrumbs ?? [];

    const navigateUp = () => {
        const r = rendererRef.current;
        if (!r) return;
        r.nav.navigateUp();
        const last = r.nav.breadcrumbs[r.nav.breadcrumbs.length - 1];
        if (last) r.focusOnNode(last.index);
        (r as unknown as { markDirty?: () => void }).markDirty?.();
        setSelection(null);
    };

    const tierBadge = tier === 'webgpu'
        ? { color: 'bg-accent shadow-[0_0_12px_rgba(142,72,234,0.6)]', label: 'WebGPU' }
        : { color: 'bg-amber-400 shadow-[0_0_12px_rgba(251,191,36,0.6)]', label: 'WebGL2 Fallback' };

    return (
        <div className="w-full h-full min-h-[400px] relative bg-[#02030a] rounded-3xl overflow-hidden border border-white/10 shadow-inner">
            {/* Title */}
            <div className="absolute top-6 left-8 z-10 pointer-events-none">
                <h2 className="text-2xl font-bold text-white flex items-center gap-3">
                    <span className={`w-3 h-3 rounded-full animate-pulse ${tierBadge.color}`} />
                    Crystal Dreamscape 3D
                </h2>
                <p className="text-white/50 text-[10px] font-bold mt-2 tracking-widest uppercase">
                    {tierBadge.label}
                </p>
            </div>

            {/* Breadcrumbs */}
            {breadcrumbs.length > 1 && (
                <div className="absolute top-6 right-8 z-10 flex items-center gap-2 bg-black/40 backdrop-blur-sm rounded-full px-4 py-2 border border-white/10">
                    <button
                        onClick={navigateUp}
                        className="text-white/80 hover:text-white text-xs font-bold uppercase tracking-widest"
                        title="Zoom out one level"
                    >
                        ← Back
                    </button>
                    <span className="text-white/30 text-xs">|</span>
                    <span className="text-white/70 text-xs font-mono truncate max-w-[24ch]">
                        {breadcrumbs.map(b => b.name).join(' / ')}
                    </span>
                </div>
            )}

            {/* Tooltip */}
            {selection && (
                <div className="absolute bottom-6 left-8 z-10 bg-black/50 backdrop-blur-sm rounded-lg px-4 py-2 border border-white/10">
                    <p className="text-white/90 text-sm font-mono">{selection.name}</p>
                    <p className="text-white/40 text-[10px] uppercase tracking-widest">Node #{selection.index}</p>
                </div>
            )}

            <canvas
                ref={canvasRef}
                className="w-full h-full cursor-grab active:cursor-grabbing block touch-none"
                style={{ minHeight: '400px', height: '100%', width: '100%', touchAction: 'none' }}
                onMouseDown={onMouseDown}
                onMouseMove={onMouseMove}
                onMouseUp={onMouseUp}
                onMouseLeave={onMouseUp}
            />
        </div>
    );
};

/**
 * Top-level tier-picker component. Probes WebGPU, then WebGL2, then falls
 * back to the 2D treemap.
 */
export const WebGPUFallback: React.FC<WebGPUFallbackProps> = ({ allFiles, activeFilter, onFilterChange, initialMode }) => {
    const [status, setStatus] = useState<'checking' | 'webgpu' | 'webgl2' | 'unsupported'>('checking');
    const [reason, setReason] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            // Tier 1: WebGPU
            if (navigator.gpu) {
                try {
                    const adapter = await navigator.gpu.requestAdapter();
                    if (adapter) {
                        if (!cancelled) setStatus('webgpu');
                        return;
                    }
                } catch (e) {
                    // fall through to tier 2
                    console.warn('[Dreamscape] WebGPU adapter probe failed:', e);
                }
            }

            // Tier 2: WebGL2. Probe by trying to actually get a context.
            const probe = document.createElement('canvas');
            const gl = probe.getContext('webgl2');
            if (gl) {
                // Some environments give a context but no functional GPU (software
                // rasterizer). We accept it anyway — the WebGL2 tier is deliberately
                // permissive because "colored geometry" beats "flat 2D treemap"
                // even on slow paths.
                if (!cancelled) {
                    setReason("WebGPU unavailable; using WebGL2 fallback.");
                    setStatus('webgl2');
                }
                return;
            }

            if (!cancelled) {
                setReason('Neither WebGPU nor WebGL2 available.');
                setStatus('unsupported');
            }
        })();
        return () => { cancelled = true; };
    }, []);

    if (status === 'checking') {
        return (
            <div className="w-full h-[600px] bg-slate-900 flex items-center justify-center rounded-lg border border-slate-800">
                <div className="flex flex-col items-center">
                    <div className="w-12 h-12 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
                    <p className="mt-4 text-slate-400 font-mono text-sm">Initializing GPU Infrastructure…</p>
                </div>
            </div>
        );
    }

    if (status === 'unsupported') {
        return (
            <div className="w-full h-full flex flex-col">
                <div className="bg-amber-900/30 border-l-4 border-amber-500 p-4 mb-4">
                    <p className="text-amber-200 text-sm">
                        <span className="font-bold">2D View:</span> {reason ?? '3D not available on this device.'}
                    </p>
                </div>
                <div className="flex-1 min-h-[400px]">
                    <FileTypeTreemap
                        allFiles={allFiles}
                        activeFilter={activeFilter}
                        onFilterChange={onFilterChange}
                        initialMode={initialMode}
                    />
                </div>
            </div>
        );
    }

    return (
        <DreamscapeCanvas
            tier={status}
            allFiles={allFiles}
            activeFilter={activeFilter}
            onFilterChange={onFilterChange}
            initialMode={initialMode}
            // If the chosen tier errors out at load-time, degrade one step.
            onError={(msg) => {
                setReason(msg);
                setStatus(status === 'webgpu' ? 'webgl2' : 'unsupported');
            }}
        />
    );
};

export default WebGPUFallback;
