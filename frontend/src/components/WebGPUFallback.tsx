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

import React, { useEffect, useRef, useState, useCallback } from 'react';
import { FileTypeTreemap } from './FileTypeTreemap';
import { WebGPURenderer } from '../renderer/WebGPURenderer';
import { WebGL2Renderer } from '../renderer/WebGL2Renderer';
import { getVisualizerStream, getVisualizerMeta, type FileEntry, type VisualizerNodeMeta } from '../api';
import { FLAG_FOLDER } from '../interaction/NavigationController';
import type { NavigationController } from '../interaction/NavigationController';
import { useDreamscapeStore } from '../store/dreamscapeStore';

interface CachedStream {
    buffer: ArrayBuffer;
    meta: Record<string, VisualizerNodeMeta>;
}

const streamCache = new Map<string, CachedStream>();


/** Both renderer classes conform to this shape; the hook is generic over it. */
interface RendererLike {
    readonly init: () => Promise<void>;
    readonly loadData: (buf: ArrayBuffer) => Promise<void>;
    readonly render: () => void;
    readonly pick: (x: number, y: number) => Promise<number | null>;
    readonly resize: (w: number, h: number) => void;
    readonly destroy: () => void;
    readonly handleMouseMove: (dx: number, dy: number) => void;
    readonly handleZoom: (delta: number) => void;
    readonly focusOnNode: (sourceIndex: number) => void;
    readonly nav: NavigationController;
    onDeviceLost?: () => void;
}

export interface WebGPUFallbackProps {
    readonly allFiles: Record<string, FileEntry[]>;
    readonly activeFilter?: string | null;
    readonly onFilterChange?: (ext: string | null) => void;
    readonly initialMode?: 'folder' | 'type';
    readonly exposure?: number;
    readonly showOutlines?: boolean;
}

/**
 * Which element the ResizeObserver should measure.
 *
 * Never the canvas. `renderer.resize()` assigns `canvas.width`/`canvas.height`,
 * and those attributes ARE the canvas's intrinsic layout size - so observing the
 * canvas makes the observer react to its own output. Wherever the height chain
 * above is indefinite, that closes into a feedback loop which multiplies the
 * canvas by the device pixel ratio on every cycle, and the page grows without
 * bound. Measuring the wrapper breaks the cycle: its size is decided by layout
 * alone and nothing ever writes to it.
 *
 * Exported so the invariant is directly testable - jsdom has no layout engine,
 * so the loop itself cannot be reproduced in a unit test.
 */
export function resizeTarget(
    canvas: HTMLCanvasElement,
    wrapper: HTMLElement | null,
): HTMLElement | null {
    const target = wrapper ?? canvas.parentElement;
    // Defensive: a caller passing the canvas as its own wrapper would re-arm
    // exactly the bug this function exists to prevent.
    return target === canvas ? null : target;
}

/**
 * Shared 3D canvas hook. Encapsulates:
 *   - renderer lifecycle (init, resize observer, RAF loop, destroy)
 *   - mouse drag (orbit), wheel (zoom), click (pick + focus)
 *   - hover pick (throttled GPU readback) for info cards
 *   - forwarding stream errors to onError so the parent can degrade tier
 *
 * The `renderer` factory is passed in so the same hook works for both
 * WebGPU and WebGL2 tiers.
 */
function useDreamscapeCanvas<R extends RendererLike>(
    canvasRef: React.RefObject<HTMLCanvasElement | null>,
    // Measured instead of the canvas. `renderer.resize` writes canvas.width/height,
    // which ARE the canvas's intrinsic layout size - so observing the canvas means
    // the observer reacts to its own output. With any indefinite height in the
    // chain above, that closes into a feedback loop that multiplies by DPR every
    // cycle. The wrapper's size is decided by layout alone and is never written to.
    wrapperRef: React.RefObject<HTMLElement | null>,
    factory: (canvas: HTMLCanvasElement) => R,
    activeFilter: string | null | undefined,
    onError: (msg: string) => void,
    onNodeSelected?: (sourceIndex: number, name: string) => void,
    rendererOptions?: {
        exposure?: number;
        showOutlines?: boolean;
    },
) {
    const rendererRef = useRef<R | null>(null);
    const rafRef = useRef<number>(0);
    const [isDragging, setDragging] = useState(false);
    const lastPos = useRef({ x: 0, y: 0 });
    const dragStart = useRef({ x: 0, y: 0 });

    // Hover state for info cards
    const [hover, setHover] = useState<{ x: number; y: number; index: number; name: string; kind: string; size?: number; hits?: number; fileCount?: number } | null>(null);
    const hoverBusy = useRef(false);
    const lastHoverTs = useRef(0);
    const metaRef = useRef<Record<string, VisualizerNodeMeta>>({});

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

                // Wired up the moment there is a device to lose — before the
                // stream fetch, not after. A hang during the first frames used
                // to land on an unset handler, so the tier never degraded and
                // the RAF loop kept submitting to a dead device.
                renderer.onDeviceLost = () => {
                    cancelled = true;
                    if (rafRef.current) cancelAnimationFrame(rafRef.current);
                    onError('GPU device lost. Please refresh the page to restore 3D view.');
                };

                const tuned = renderer as R & {
                    exposure?: number;
                    enableOutline?: boolean;
                };

                if (rendererOptions?.exposure !== undefined) {
                    tuned.exposure = rendererOptions.exposure;
                }

                if (rendererOptions?.showOutlines !== undefined) {
                    tuned.enableOutline = rendererOptions.showOutlines;
                }

                let buffer: ArrayBuffer;
                let meta: any;
                const cacheKey = activeFilter || 'default';

                if (streamCache.has(cacheKey)) {
                    const cached = streamCache.get(cacheKey)!;
                    buffer = cached.buffer;
                    meta = cached.meta;
                } else {
                    [buffer, meta] = await Promise.all([
                        getVisualizerStream(activeFilter),
                        getVisualizerMeta(activeFilter).catch(() => ({})),
                    ]);
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
                    streamCache.set(cacheKey, { buffer, meta });
                }

                metaRef.current = meta;
                if (cancelled) return;

                await renderer.loadData(buffer);

                // Real name table: node.typeHash → meta name. This replaces the
                // BFS-order deriveNameTable heuristic (fixes breadcrumbs too).
                const names = new Map<number, string>();
                for (const node of renderer.nav.nodes) {
                    const m = metaRef.current[String(node.typeHash)];
                    if (m) names.set(node.index, m.name);
                }
                renderer.nav.loadNames(names);

                resizeObserver = new ResizeObserver(entries => {
                    for (const e of entries) {
                        const dpr = Math.min(window.devicePixelRatio || 1, 2);
                        const dpBox = (e as any).devicePixelContentBoxSize?.[0];
                        let w: number, h: number;
                        if (dpBox) {
                            w = dpBox.inlineSize;
                            h = dpBox.blockSize;
                        } else {
                            w = Math.round(e.contentRect.width * dpr);
                            h = Math.round(e.contentRect.height * dpr);
                        }
                        if (w > 0 && h > 0) renderer.resize(w, h);
                    }
                });
                const measured = resizeTarget(canvas, wrapperRef.current);
                if (measured) resizeObserver.observe(measured);

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
    }, [activeFilter, factory, onError, rendererOptions?.exposure, rendererOptions?.showOutlines]);

    const onMouseDown = (e: React.MouseEvent) => {
        setDragging(true);
        lastPos.current = { x: e.clientX, y: e.clientY };
        dragStart.current = { x: e.clientX, y: e.clientY };
    };
    const onMouseMove = (e: React.MouseEvent) => {
        if (isDragging && rendererRef.current) {
            setHover(null);
            rendererRef.current.handleMouseMove(e.clientX - lastPos.current.x, e.clientY - lastPos.current.y);
            lastPos.current = { x: e.clientX, y: e.clientY };
            return;
        }
        // Throttled GPU hover-pick (~12/s) — pick() does a 1px readback.
        const now = performance.now();
        if (hoverBusy.current || now - lastHoverTs.current < 80) return;
        lastHoverTs.current = now;
        const renderer = rendererRef.current;
        const canvas = canvasRef.current;
        if (!renderer || !canvas) return;
        const rect = canvas.getBoundingClientRect();
        const cx = e.clientX - rect.left;
        const cy = e.clientY - rect.top;
        hoverBusy.current = true;
        renderer.pick(cx, cy)
            .then(idx => {
                hoverBusy.current = false;
                if (idx === null) { setHover(null); return; }
                const node = renderer.nav.nodes[idx];
                const m = node ? metaRef.current[String(node.typeHash)] : undefined;
                setHover({
                    x: cx, y: cy, index: idx,
                    name: m?.name ?? `#${idx}`,
                    kind: m?.is_folder ?? ((node?.flags & 1) === 1) ? 'Folder' : 'File',
                    size: m?.size,
                    hits: m?.usage_count,
                    fileCount: m?.file_count,
                });
            })
            .catch(() => { hoverBusy.current = false; });
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

        // If it's a file, add it to the dreamscape store for chat context.
        // NavigationController has no metadata side-channel; the node's own
        // flags are the authoritative folder/file bit (FLAG_FOLDER).
        const node = renderer.nav.getGraphNode(sourceIndex);
        const isFolder = ((node?.flags ?? 0) & FLAG_FOLDER) === FLAG_FOLDER;
        
        if (!isFolder && e.shiftKey) { // Optional: require shift-click to select? Or just any click on a file? Let's just add any clicked file.
           // Actually, let's just add it anytime they click a file.
           useDreamscapeStore.getState().addChunk({
               id: sourceIndex,
               filename: name,
           });
        } else if (!isFolder) {
           useDreamscapeStore.getState().addChunk({
               id: sourceIndex,
               filename: name,
           });
        }
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

    return { rendererRef, onMouseDown, onMouseMove, onMouseUp, hover, setHover };
}

interface CanvasInnerProps extends WebGPUFallbackProps {
    readonly tier: 'webgpu' | 'webgl2';
    readonly onError: (msg: string) => void;
}

const DreamscapeCanvas: React.FC<CanvasInnerProps> = ({ activeFilter, tier, onError, exposure, showOutlines }) => {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const wrapperRef = useRef<HTMLDivElement>(null);
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

    const { rendererRef, onMouseDown, onMouseMove, onMouseUp, hover, setHover } =
        useDreamscapeCanvas(canvasRef, wrapperRef, factory, activeFilter, onError,
            (idx, name) => setSelection({ index: idx, name }),
            { exposure, showOutlines });

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

    // Helper to format bytes
    function formatBytes(n?: number): string {
        if (n === undefined) return '—';
        if (n < 1024) return `${n} B`;
        const units = ['KB', 'MB', 'GB', 'TB'];
        let v = n / 1024, u = 0;
        while (v >= 1024 && u < units.length - 1) { v /= 1024; u++; }
        return `${v.toFixed(1)} ${units[u]}`;
    }

    return (
        <div ref={wrapperRef} className="w-full h-full min-h-[400px] relative bg-[#02030a] rounded-3xl overflow-hidden border border-white/10 shadow-inner">
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

            {/* Hover card — follows the cursor */}
            {hover && (
                <div
                    className="absolute z-20 pointer-events-none bg-black/60 backdrop-blur-md rounded-lg px-3 py-2 border border-white/15 shadow-xl"
                    style={{ left: hover.x + 14, top: hover.y + 14 }}
                >
                    <p className="text-white/90 text-xs font-mono truncate max-w-[36ch]">{hover.name}</p>
                    <p className="text-white/40 text-[9px] uppercase tracking-widest">
                        {hover.kind}
                        {hover.fileCount !== undefined && ` · ${hover.fileCount} files`}
                    </p>
                    <p className="text-white/60 text-[10px] font-mono mt-1">
                        {formatBytes(hover.size)} · {hover.hits ?? 0} hits
                    </p>
                </div>
            )}

            {/* No minHeight on the canvas on purpose. A floor here re-introduces an
                intrinsic size that can exceed the wrapper, which desyncs the render
                viewport (sized from the wrapper) from the hit-test box (read off the
                canvas in onMouseMove). The floor belongs on the wrapper alone. */}
            <canvas
                ref={canvasRef}
                className="w-full h-full cursor-grab active:cursor-grabbing block touch-none"
                style={{ height: '100%', width: '100%', touchAction: 'none' }}
                onMouseDown={onMouseDown}
                onMouseMove={onMouseMove}
                onMouseUp={onMouseUp}
                onMouseLeave={(e) => { setHover(null); onMouseUp(e); }}
            />
        </div>
    );
};

/**
 * Top-level tier-picker component. Probes WebGPU, then WebGL2, then falls
 * back to the 2D treemap.
 */
export const WebGPUFallback: React.FC<WebGPUFallbackProps> = ({ allFiles, activeFilter, onFilterChange, initialMode, exposure = 1.15, showOutlines = false }) => {
    const [status, setStatus] = useState<'checking' | 'webgpu' | 'webgl2' | 'unsupported'>('checking');
    const [reason, setReason] = useState<string | null>(null);

    // Must be stable: this lands in useDreamscapeCanvas's effect dependency
    // array. As an inline arrow it changed identity on every parent render, so
    // an unrelated InsightsPage re-render tore down the renderer and built a
    // fresh GPUDevice — which is why a single fault used to log twice.
    const handleError = useCallback((msg: string) => {
        setReason(msg);
        // If the chosen tier errors out at load-time, degrade one step.
        setStatus(prev => (prev === 'webgpu' ? 'webgl2' : 'unsupported'));
    }, []);

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
        // h-full, not a fixed height: a hardcoded 600px disagreed with the
        // steady-state panel and made it jump the moment the tier resolved.
        return (
            <div className="w-full h-full min-h-[400px] bg-slate-900 flex items-center justify-center rounded-lg border border-slate-800">
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
            // Keying on tier forces a BRAND NEW <canvas> when we degrade. A
            // canvas keeps its context type for life, so reusing the same
            // element after WebGPU claimed it makes getContext('webgl2')
            // return null forever ("existing context of a different type").
            key={status}
            tier={status}
            allFiles={allFiles}
            activeFilter={activeFilter}
            onFilterChange={onFilterChange}
            initialMode={initialMode}
            exposure={exposure}
            showOutlines={showOutlines}
            onError={handleError}
        />
    );
};

export default WebGPUFallback;
