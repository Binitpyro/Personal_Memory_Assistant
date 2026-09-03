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
 *   - WebGL2Renderer is dynamically imported, so three.js and its five
 *     postprocessing passes stay out of this chunk and are fetched only when
 *     tier 2 is actually selected. An earlier revision made this import static
 *     to dodge a ref<> type error and justified it as "three.js is still lazy
 *     via React.lazy" - true of the app shell, false of the tier: every WebGPU
 *     user downloaded and parsed three.js without ever instantiating it. The
 *     type error is avoided by making `factory` async end-to-end rather than
 *     casting at the call site.
 *   - Shared canvas hook, so both tiers get pick + focus behavior.
 *   - Name-table wiring: NavigationController is fed folder names from
 *     tree.folders so breadcrumbs show real paths instead of "#42".
 */

import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { FileTypeTreemap } from './FileTypeTreemap';
import { AccessibleTree, type A11yNode } from './AccessibleTree';
import { ShortcutOverlay } from './ShortcutOverlay';
import { FLY_KEYS, FLY_BOOST } from '../interaction/keymap';
import {
    nextSibling, previousSibling, expandOrDescend, collapseOrAscend,
    initialCursor, describeNode, isFolder as navIsFolder,
} from '../interaction/KeyboardNavigation';
import { formatBytes } from '../utils/treeBuilder';
import { WebGPURenderer } from '../renderer/WebGPURenderer';
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
    readonly flyBy: (forward: number, right: number, up: number) => void;
    readonly markDirty: () => void;
    /** Mutable: the view turns the camera glide off for reduced motion. */
    smoothCamera: boolean;
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

/** Progress through the pre-first-frame work. `starting` covers renderer.init()
 *  (device + pipelines + shader compilation); `streaming` covers the graph
 *  fetch, which is the half that grows with the corpus. */
export type LoadPhase = 'starting' | 'streaming' | 'ready';

export const PHASE_LABEL: Record<Exclude<LoadPhase, 'ready'>, string> = {
    starting: 'Starting GPU renderer…',
    streaming: 'Loading graph…',
};

/** Largest backing-store ratio we will render at. Above this both the fragment
 *  cost and the footprint of all twenty render targets grow with the square of
 *  the ratio, against a ~4GB VRAM target. */
export const MAX_DPR = 2;

/**
 * Device-pixel size of the canvas backing store for one ResizeObserver entry.
 *
 * Extracted and exported for the same reason as `resizeTarget`: jsdom has no
 * layout engine so the observer itself cannot be driven in a unit test, but
 * the arithmetic can.
 *
 * `devicePixelContentBoxSize` reports TRUE device pixels, so it does not need
 * multiplying by the ratio - but it does need capping by it, and that is what
 * this previously got wrong. The cap was computed and then applied only on the
 * `contentRect` branch, so on Chromium - which has had the device-pixel box
 * since 84, i.e. the Tauri webview and the primary browser path - it never ran
 * at all. A DPR-3 display rendered at 3x: 2.25x the fragment work and 2.25x the
 * render-target footprint that the cap exists to prevent.
 */
export function canvasPixelSize(
    entry: ResizeObserverEntry,
    devicePixelRatio: number | undefined,
): { w: number; h: number } {
    const dpr = devicePixelRatio || 1;
    const capped = Math.min(dpr, MAX_DPR);
    const dpBox = (entry as unknown as {
        devicePixelContentBoxSize?: readonly { inlineSize: number; blockSize: number }[];
    }).devicePixelContentBoxSize?.[0];

    if (dpBox) {
        // Already device pixels; scale down only when the display exceeds the cap.
        const scale = capped / dpr;
        return {
            w: Math.round(dpBox.inlineSize * scale),
            h: Math.round(dpBox.blockSize * scale),
        };
    }
    // contentRect is CSS pixels, so here the ratio is applied rather than removed.
    return {
        w: Math.round(entry.contentRect.width * capped),
        h: Math.round(entry.contentRect.height * capped),
    };
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
    factory: (canvas: HTMLCanvasElement) => Promise<R>,
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

    // Keys currently held, integrated once per frame. NOT accumulated per
    // `keydown`: auto-repeat rate is an OS setting, so integrating repeats
    // would make fly speed depend on the user's control panel.
    const heldKeys = useRef<Set<string>>(new Set());
    // Restarts the render loop after the idle gate has parked it. Assigned
    // inside the init effect, where `loop` is in scope.
    const wakeRef = useRef<() => void>(() => {});

    // The Outliner cursor. Deliberately NOT NavigationController.focusIndex,
    // which means "the node drilled into" — see interaction/KeyboardNavigation.
    const [cursor, setCursor] = useState<number | null>(null);
    const cursorRef = useRef<number | null>(null);
    cursorRef.current = cursor;
    const [announcement, setAnnouncement] = useState('');
    // Bumped whenever nav's expansion state changes, to re-derive the a11y tree.
    const [navVersion, setNavVersion] = useState(0);
    const [isDragging, setDragging] = useState(false);
    const lastPos = useRef({ x: 0, y: 0 });
    const dragStart = useRef({ x: 0, y: 0 });

    // Hover state for info cards
    const [hover, setHover] = useState<{ x: number; y: number; index: number; name: string; kind: string; size?: number; hits?: number; fileCount?: number } | null>(null);
    const hoverBusy = useRef(false);
    const lastHoverTs = useRef(0);
    const metaRef = useRef<Record<string, VisualizerNodeMeta>>({});

    // The two slow phases before a first frame exists. Suspense in InsightsPage
    // only covers the module download; everything below it - adapter + device
    // request, ~15 render pipelines, WGSL compilation, then a stream fetch that
    // scales with corpus size - used to run behind a blank canvas.
    const [phase, setPhase] = useState<LoadPhase>('starting');

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;

        let cancelled = false;
        let resizeObserver: ResizeObserver | null = null;
        let onVisibility: (() => void) | null = null;
        setPhase('starting');

        (async () => {
            try {
                // Construction is awaited now: on tier 2 this is a chunk fetch,
                // so teardown can beat it. Destroy immediately in that case —
                // the cleanup below has already run and cannot see this one.
                const renderer = await factory(canvas);
                if (cancelled) { renderer.destroy(); return; }
                rendererRef.current = renderer;

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

                if (!cancelled) setPhase('streaming');

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
                        const { w, h } = canvasPixelSize(e, window.devicePixelRatio);
                        if (w > 0 && h > 0) renderer.resize(w, h);
                    }
                });
                const measured = resizeTarget(canvas, wrapperRef.current);
                if (measured) resizeObserver.observe(measured);

                // A continuously-animating scene with no way to stop it. Two
                // gates: `prefers-reduced-motion`, which the CSS block in
                // index.css cannot reach because this is a JS-driven rAF loop
                // rather than a CSS animation; and document visibility, since
                // rendering a hidden canvas burns GPU for nothing. Both still
                // render one frame, so the scene is drawn rather than blank.
                const reduced =
                    typeof matchMedia === 'function' &&
                    matchMedia('(prefers-reduced-motion: reduce)').matches;

                // The camera glide is animation, so reduced motion cuts
                // instead. It is also load-bearing for the gate below: with a
                // glide, a cursor move under reduced motion would render one
                // frame — a tenth of the way — and then park, leaving the
                // camera stranded short of the node it was asked to look at.
                renderer.smoothCamera = !reduced;

                const loop = () => {
                    if (cancelled) return;

                    // Fly is integrated per frame from the held-key set.
                    const held = heldKeys.current;
                    let f = 0, r = 0, u = 0;
                    for (const k of held) {
                        const v = FLY_KEYS[k];
                        if (v) { f += v.forward; r += v.right; u += v.up; }
                    }
                    const flying = f !== 0 || r !== 0 || u !== 0;
                    if (flying) {
                        const boost = held.has('shift') ? FLY_BOOST : 1;
                        renderer.flyBy(f * boost, r * boost, u * boost);
                    }

                    renderer.render();

                    // Direct manipulation is not the involuntary animation
                    // `prefers-reduced-motion` is about, so held keys keep the
                    // loop alive even under that setting. It is only the idle,
                    // unattended animation that the gate exists to stop.
                    if (!flying && (reduced || document.hidden)) {
                        rafRef.current = 0;
                        return;
                    }
                    rafRef.current = requestAnimationFrame(loop);
                };
                rafRef.current = requestAnimationFrame(loop);

                wakeRef.current = () => {
                    if (!cancelled && rafRef.current === 0) {
                        rafRef.current = requestAnimationFrame(loop);
                    }
                };

                // Resume when the tab comes back, or the scene freezes for good.
                onVisibility = () => {
                    if (!cancelled && !document.hidden && rafRef.current === 0) {
                        rafRef.current = requestAnimationFrame(loop);
                    }
                };
                document.addEventListener('visibilitychange', onVisibility);
                if (!cancelled) setPhase('ready');
            } catch (err) {
                onError(err instanceof Error ? err.message : 'Unknown 3D init error');
            }
        })();

        return () => {
            cancelled = true;
            heldKeys.current.clear();
            wakeRef.current = () => {};
            if (onVisibility) document.removeEventListener('visibilitychange', onVisibility);
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
        renderer.markDirty();

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


    // ── Keyboard ────────────────────────────────────────────────────────
    //
    // Two keymaps on one focus target, following the Unreal/Unity split
    // between a viewport (camera verbs) and an outliner (hierarchy verbs).
    // Both are bound here rather than on separate elements so a sighted
    // keyboard user never has to tab between panels to fly and to select.

    const nameOf = useCallback((index: number) => {
        const renderer = rendererRef.current;
        const node = renderer?.nav.getGraphNode(index);
        const meta = node ? metaRef.current[String(node.typeHash)] : undefined;
        return meta?.name ?? `#${index}`;
    }, []);

    /**
     * Move the cursor and let the camera follow.
     *
     * A DCC outliner does NOT move the camera on selection — that is what F is
     * for. This one does, because the renderer has no selection highlight, so
     * without the camera following, arrow keys would produce no visible change
     * whatsoever for a sighted keyboard user. If a highlight is ever added to
     * the instance buffer, decouple these and restore the outliner behaviour.
     */
    const moveCursorTo = useCallback((index: number, announce?: string) => {
        const renderer = rendererRef.current;
        if (!renderer) return;
        setCursor(index);
        renderer.focusOnNode(index);
        wakeRef.current();
        setAnnouncement(announce ?? describeNode(renderer.nav, index, nameOf(index)));
    }, [nameOf]);

    const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLElement>) => {
        const renderer = rendererRef.current;
        if (!renderer) return;
        const nav = renderer.nav;
        if (nav.nodes.length === 0) return;

        const lower = e.key.toLowerCase();

        // Held-key camera movement. Tracked, not acted on here.
        if (FLY_KEYS[lower]) {
            heldKeys.current.add(lower);
            if (e.shiftKey) heldKeys.current.add('shift');
            wakeRef.current();
            e.preventDefault();
            return;
        }
        if (e.key === 'Shift') { heldKeys.current.add('shift'); return; }

        const at = cursorRef.current ?? initialCursor(nav);

        switch (e.key) {
            case 'ArrowUp':
            case 'ArrowDown':
            case 'ArrowLeft':
            case 'ArrowRight': {
                e.preventDefault();
                if (e.shiftKey) {
                    // Orbit. Discrete per keypress; auto-repeat gives a
                    // continuous feel, and unlike fly the step is fixed so the
                    // repeat rate only affects how fast, not how far per unit.
                    const ORBIT = 24;
                    const dx = e.key === 'ArrowLeft' ? -ORBIT : e.key === 'ArrowRight' ? ORBIT : 0;
                    const dy = e.key === 'ArrowUp' ? -ORBIT : e.key === 'ArrowDown' ? ORBIT : 0;
                    renderer.handleMouseMove(dx, dy);
                    wakeRef.current();
                    return;
                }
                const move =
                    e.key === 'ArrowDown' ? nextSibling(nav, at)
                    : e.key === 'ArrowUp' ? previousSibling(nav, at)
                    : e.key === 'ArrowRight' ? expandOrDescend(nav, at)
                    : collapseOrAscend(nav, at);

                if (move.changed) {
                    renderer.markDirty();
                    setNavVersion(v => v + 1);
                    moveCursorTo(move.index);
                } else if (move.announce) {
                    setAnnouncement(move.announce);
                }
                return;
            }

            case 'Enter': {
                e.preventDefault();
                nav.navigateTo(at);
                renderer.markDirty();
                setNavVersion(v => v + 1);
                moveCursorTo(at, `Entered ${nameOf(at)}`);
                onNodeSelected?.(at, nameOf(at));
                return;
            }

            case 'Backspace': {
                e.preventDefault();
                nav.navigateUp();
                renderer.markDirty();
                setNavVersion(v => v + 1);
                const up = nav.getFocusIndex();
                moveCursorTo(up, `Up to ${nameOf(up)}`);
                return;
            }

            case 'Home': {
                e.preventDefault();
                const root = nav.getRootIndex();
                nav.navigateTo(root);
                renderer.markDirty();
                setNavVersion(v => v + 1);
                moveCursorTo(root, 'Framed everything');
                return;
            }

            case 'f':
            case 'F': {
                e.preventDefault();
                renderer.focusOnNode(at);
                wakeRef.current();
                setAnnouncement(`Framed ${nameOf(at)}`);
                return;
            }

            case 'Escape': {
                setCursor(null);
                setAnnouncement('Selection cleared');
                return;
            }

            case '+':
            case '=':
                e.preventDefault();
                renderer.handleZoom(-1);
                wakeRef.current();
                return;

            case '-':
            case '_':
                e.preventDefault();
                renderer.handleZoom(1);
                wakeRef.current();
                return;

            default:
                return;
        }
    }, [moveCursorTo, nameOf, onNodeSelected]);

    const handleKeyUp = useCallback((e: React.KeyboardEvent<HTMLElement>) => {
        heldKeys.current.delete(e.key.toLowerCase());
        if (e.key === 'Shift') heldKeys.current.delete('shift');
    }, []);

    // A key held while focus leaves would otherwise stay held forever, flying
    // the camera off on its own.
    const handleBlur = useCallback(() => { heldKeys.current.clear(); }, []);

    /**
     * The accessible mirror of the visible hierarchy.
     *
     * Only expanded nodes are materialised, so this tracks what the scene
     * actually shows and a 5000-file corpus does not become 5000 DOM nodes.
     */
    const a11yNodes = useMemo<A11yNode[]>(() => {
        void navVersion;
        const renderer = rendererRef.current;
        if (!renderer || renderer.nav.nodes.length === 0) return [];
        const nav = renderer.nav;

        const build = (index: number, depth: number): A11yNode => {
            const node = nav.getGraphNode(index);
            const folder = navIsFolder(nav, index);
            const expanded = folder && nav.expandedNodes.has(index);
            const meta = node ? metaRef.current[String(node.typeHash)] : undefined;
            return {
                id: String(index),
                name: nameOf(index),
                isFolder: folder,
                expanded: folder ? expanded : undefined,
                detail: folder && node
                    ? `${node.children.length} items`
                    : meta?.size !== undefined ? formatBytes(meta.size) : undefined,
                children: expanded && node && depth < 12
                    ? node.children.map(c => build(c, depth + 1))
                    : undefined,
            };
        };

        const focus = nav.getFocusIndex();
        const root = nav.getGraphNode(focus);
        return root ? root.children.map(c => build(c, 1)) : [];
    }, [navVersion, nameOf, phase]);

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

    return {
        rendererRef, onMouseDown, onMouseMove, onMouseUp, hover, setHover, phase,
        handleKeyDown, handleKeyUp, handleBlur,
        cursor, setCursor: moveCursorTo, a11yNodes, announcement, nameOf,
    };
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
    // doesn't reinit on every render. Async because the tier-2 module is
    // code-split; the WebGPU branch resolves without a network round-trip.
    const factory = useCallback(
        async (canvas: HTMLCanvasElement): Promise<RendererLike> => {
            if (tier === 'webgpu') {
                return new WebGPURenderer(canvas) as unknown as RendererLike;
            }
            const { WebGL2Renderer } = await import('../renderer/WebGL2Renderer');
            return new WebGL2Renderer(canvas) as unknown as RendererLike;
        },
        [tier],
    );

    const {
        rendererRef, onMouseDown, onMouseMove, onMouseUp, hover, setHover, phase,
        handleKeyDown, handleKeyUp, handleBlur,
        cursor, setCursor, a11yNodes, announcement,
    } = useDreamscapeCanvas(canvasRef, wrapperRef, factory, activeFilter, onError,
            (idx, name) => setSelection({ index: idx, name }),
            { exposure, showOutlines });

    const [shortcutsOpen, setShortcutsOpen] = useState(false);

    // `?` and F1 open the reference from anywhere in the view. Kept out of
    // handleKeyDown so the tree gets it too without duplicating the binding.
    const onViewKeyDown = useCallback((e: React.KeyboardEvent<HTMLElement>) => {
        if (e.key === '?' || e.key === 'F1') {
            e.preventDefault();
            setShortcutsOpen(true);
            return;
        }
        handleKeyDown(e);
    }, [handleKeyDown]);

    const breadcrumbs = rendererRef.current?.nav.breadcrumbs ?? [];

    const navigateUp = () => {
        const r = rendererRef.current;
        if (!r) return;
        r.nav.navigateUp();
        const last = r.nav.breadcrumbs[r.nav.breadcrumbs.length - 1];
        if (last) r.focusOnNode(last.index);
        r.markDirty();
        setSelection(null);
    };

    // Fixed light values, not theme tokens, and no glow. `bg-accent` was wrong
    // here for the same reason white text is right: this chrome sits on a
    // permanently dark canvas, so Paper's brass (#5E4724) would have all but
    // disappeared. The violet glow on the WebGPU tier was a survivor of the
    // pre-redesign palette, which §2 bans in any role.
    const tierBadge = tier === 'webgpu'
        ? { color: 'bg-white/90', label: 'WebGPU' }
        : { color: 'bg-amber-400', label: 'WebGL2 Fallback' };

    return (
        // THE WHITE-ON-DARK CHROME BELOW IS DELIBERATE - DO NOT TOKENISE IT.
        //
        // `renderer/palette.ts` reads no CSS variable and no theme: the vitrine
        // is a fixed dark grade (skyHorizon #0A0806) in BOTH cabinet and paper,
        // and this wrapper is a fixed #02030a. So the ground behind every
        // overlay here is dark regardless of the user's theme, and swapping
        // `text-white/*` for `text-text-primary` would render ink on ink the
        // moment someone switches to Paper - the opposite of a fix.
        //
        // What DID need fixing was the alpha. Composited against the real
        // ground, `text-white/40` measured 3.70 at 10px and 3.69 at 9px, both
        // under AA; they are /70 now (9.85 and 9.87). The `checking` and
        // `unsupported` branches above are different - they render on the
        // themed page, not the canvas, so those use tokens.
        <div ref={wrapperRef} className="w-full h-full min-h-[400px] relative bg-[#02030a] rounded-xl overflow-hidden border border-white/10 shadow-inner">
            {/* Title */}
            <div className="absolute top-6 left-8 z-10 pointer-events-none">
                <h2 className="text-2xl font-bold text-white flex items-center gap-3">
                    <span aria-hidden className={`w-3 h-3 rounded-full ${tierBadge.color}`} />
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
                    <span aria-hidden className="w-px h-3 bg-white/30 shrink-0" />
                    <span className="text-white/70 text-xs font-mono truncate max-w-[24ch]">
                        {breadcrumbs.map(b => b.name).join(' / ')}
                    </span>
                </div>
            )}

            {/* Tooltip */}
            {selection && (
                <div className="absolute bottom-6 left-8 z-10 bg-black/50 backdrop-blur-sm rounded-lg px-4 py-2 border border-white/10">
                    <p className="text-white/90 text-sm font-mono">{selection.name}</p>
                    <p className="text-white/70 text-[10px] uppercase tracking-widest">Node #{selection.index}</p>
                </div>
            )}

            {/* Hover card — follows the cursor */}
            {hover && (
                <div
                    className="absolute z-20 pointer-events-none bg-black/60 backdrop-blur-md rounded-lg px-3 py-2 border border-white/15 shadow-xl"
                    style={{ left: hover.x + 14, top: hover.y + 14 }}
                >
                    <p className="text-white/90 text-xs font-mono truncate max-w-[36ch]">{hover.name}</p>
                    <p className="text-white/70 text-[10px] uppercase tracking-widest">
                        {hover.kind}
                        {hover.fileCount !== undefined && ` · ${hover.fileCount} files`}
                    </p>
                    <p className="text-white/60 text-[10px] font-mono mt-1">
                        {hover.size === undefined ? '—' : formatBytes(hover.size)} · {hover.hits ?? 0} hits
                    </p>
                </div>
            )}

            {/* Loading overlay. Same spinner as the capability-probe branch, but
                white-on-dark rather than themed: it sits on the fixed #02030a
                ground, where `text-text-secondary` would be ink on ink in Paper.
                See the chrome note at the top of this return. */}
            {phase !== 'ready' && (
                <div
                    className="absolute inset-0 z-30 flex flex-col items-center justify-center bg-[#02030a]/80 backdrop-blur-sm"
                    role="status"
                    aria-live="polite"
                >
                    <div className="w-10 h-10 border-2 border-white/20 border-t-white/90 rounded-full animate-spin" />
                    <p className="mt-4 text-white/70 font-mono text-sm">{PHASE_LABEL[phase]}</p>
                </div>
            )}

            {/* No minHeight on the canvas on purpose. A floor here re-introduces an
                intrinsic size that can exceed the wrapper, which desyncs the render
                viewport (sized from the wrapper) from the hit-test box (read off the
                canvas in onMouseMove). The floor belongs on the wrapper alone. */}
            {/* The AT surface. First in DOM order so a screen reader meets
                the readable hierarchy before the opaque canvas. */}
            <AccessibleTree
                label="Corpus hierarchy"
                nodes={a11yNodes}
                selectedId={cursor === null ? null : String(cursor)}
                onSelect={id => setCursor(Number(id))}
                onActivate={id => {
                    const r = rendererRef.current;
                    if (!r) return;
                    r.nav.navigateTo(Number(id));
                    r.markDirty();
                    setCursor(Number(id));
                }}
                onUnhandledKey={onViewKeyDown}
            />

            {/* Transient results the tree cannot express — "Framed X",
                "At root", "Empty folder". Always mounted: a live region that
                appears together with its text announces nothing. */}
            <span className="sr-only" aria-live="polite">{announcement}</span>

            <canvas
                ref={canvasRef}
                // role="application" tells a screen reader to pass keys
                // through instead of capturing them for browse mode, which is
                // what a custom keymap needs. The hierarchy itself is readable
                // through the tree above, not through this element.
                role="application"
                tabIndex={0}
                aria-label="Crystal Dreamscape 3D viewport"
                aria-describedby="dreamscape-keyhint"
                className="w-full h-full cursor-grab active:cursor-grabbing block touch-none focus-visible:outline-2 focus-visible:outline-offset-[-2px]"
                style={{ height: '100%', width: '100%', touchAction: 'none' }}
                onMouseDown={onMouseDown}
                onMouseMove={onMouseMove}
                onMouseUp={onMouseUp}
                onMouseLeave={(e) => { setHover(null); onMouseUp(e); }}
                onKeyDown={onViewKeyDown}
                onKeyUp={handleKeyUp}
                onBlur={handleBlur}
            />

            {/* Was absent entirely on this view: the 3D scene documented none
                of its controls. Short, with the full table one key away. */}
            <div
                id="dreamscape-keyhint"
                className="absolute bottom-4 right-6 z-10 pointer-events-none text-[10px] font-mono uppercase tracking-wider text-white/70"
            >
                WASD fly · F frame · ↑↓←→ browse · ? keys
            </div>

            <ShortcutOverlay
                open={shortcutsOpen}
                onClose={() => setShortcutsOpen(false)}
                groups={['viewport', 'outliner']}
                title="Dreamscape keyboard reference"
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
            <div className="w-full h-full min-h-[400px] bg-surface flex items-center justify-center rounded-xl border border-rule">
                <div className="flex flex-col items-center">
                    <div className="w-10 h-10 border-2 border-rule border-t-primary rounded-full animate-spin" />
                    <p className="mt-4 text-text-secondary font-mono text-sm">Checking graphics support…</p>
                </div>
            </div>
        );
    }

    if (status === 'unsupported') {
        return (
            <div className="w-full h-full flex flex-col">
                <div className="bg-surface border-l-2 border-warning rounded-sm p-4 mb-4">
                    <p className="text-text-primary text-sm m-0">
                        <span className="font-medium">2D view:</span> {reason ?? '3D not available on this device.'}
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
