/**
 * A8-11: under prefers-reduced-motion the rAF loop parks after one frame, and
 * only keyboard paths woke it, so mouse orbit, wheel zoom and click drill-in
 * changed renderer state without ever drawing it.
 * A8-06: clicking a file put the visualizer NODE index into the chat context
 * as a chunk id, force-including an unrelated document's chunk.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, fireEvent, act } from '@testing-library/react';
import { WebGPUFallback } from '../../components/WebGPUFallback';
import { useDreamscapeStore } from '../../store/dreamscapeStore';
import { getVisualizerStream } from '../../api';
import { renderWithProviders } from '../test-utils';

const stats = { renders: 0, picked: 3 as number | null, pickThrows: false };
vi.mock('../../renderer/WebGPURenderer', () => ({
    WebGPURenderer: class {
        nav = {
            nodes: [],
            breadcrumbs: [],
            loadNames: () => {},
            navigateTo: () => {},
            getGraphNode: () => ({ flags: 0, children: [] }),
        };
        smoothCamera = true;
        async init() {}
        async loadData() {}
        render() { stats.renders++; }
        resize() {}
        flyBy() {}
        markDirty() {}
        focusOnNode() {}
        handleMouseMove() {}
        handleZoom() {}
        async pick() { if (stats.pickThrows) throw new Error('torn down'); return stats.picked; }
        destroy() {}
    },
}));

vi.mock('../../api', async importOriginal => ({
    ...(await importOriginal<typeof import('../../api')>()),
    getVisualizerStream: vi.fn(async () => new ArrayBuffer(64)),
    getVisualizerMeta: vi.fn(async () => ({})),
}));

const frames = () => act(() => new Promise<void>(res => setTimeout(res, 80)));

describe('3D view under prefers-reduced-motion', () => {
    beforeEach(() => {
        stats.renders = 0;
        stats.picked = 3;
        stats.pickThrows = false;
        useDreamscapeStore.getState().clearChunks();
        vi.stubGlobal('matchMedia', (query: string) => ({
            matches: query.includes('prefers-reduced-motion'),
            media: query,
            addEventListener: () => {},
            removeEventListener: () => {},
        }));
        Object.defineProperty(navigator, 'gpu', {
            value: { requestAdapter: async () => ({}) },
            configurable: true,
        });
    });
    afterEach(() => {
        vi.unstubAllGlobals();
        delete (navigator as unknown as Record<string, unknown>).gpu;
    });

    const mount = async () => {
        renderWithProviders(
            <WebGPUFallback allFiles={{}} activeFilter="" onFilterChange={() => {}} initialMode="folder" />,
        );
        const canvas = await screen.findByRole('application', { name: /Crystal Dreamscape/i });
        await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
        await frames(); // loop renders once, then parks
        return canvas;
    };

    it('redraws after a wheel zoom', async () => {
        const canvas = await mount();
        const before = stats.renders;
        fireEvent.wheel(canvas, { deltaY: 100 });
        await frames();
        expect(stats.renders).toBeGreaterThan(before);
    });

    it('redraws after a mouse drag', async () => {
        const canvas = await mount();
        const before = stats.renders;
        fireEvent.mouseDown(canvas, { clientX: 10, clientY: 10 });
        fireEvent.mouseMove(canvas, { clientX: 40, clientY: 30 });
        await frames();
        expect(stats.renders).toBeGreaterThan(before);
    });

    it('swallows a pick that rejects mid-click (renderer torn down)', async () => {
        const canvas = await mount();
        stats.pickThrows = true;
        const unhandled = vi.fn();
        process.on('unhandledRejection', unhandled);
        fireEvent.mouseDown(canvas, { clientX: 10, clientY: 10 });
        fireEvent.mouseUp(canvas, { clientX: 10, clientY: 10 });
        await frames();
        process.off('unhandledRejection', unhandled);
        expect(unhandled).not.toHaveBeenCalled();
    });

    it('redraws after a click, and does not turn a node index into a chunk id', async () => {
        const canvas = await mount();
        const before = stats.renders;
        fireEvent.mouseDown(canvas, { clientX: 10, clientY: 10 });
        fireEvent.mouseUp(canvas, { clientX: 10, clientY: 10 });
        await frames();
        expect(stats.renders).toBeGreaterThan(before);
        expect(useDreamscapeStore.getState().selectedChunks).toEqual([]);
    });
});

/**
 * A8-16: a data/network failure while loading the graph stepped the render
 * tier down (webgpu -> webgl2 -> 2D) for good. Only GPU failures may.
 * A8-21: the module-level stream cache outlived the corpus it was built from.
 */
describe('3D data loading', () => {
    beforeEach(() => {
        vi.mocked(getVisualizerStream).mockClear();
        Object.defineProperty(navigator, 'gpu', {
            value: { requestAdapter: async () => ({}) },
            configurable: true,
        });
    });
    afterEach(() => {
        vi.unstubAllGlobals();
        delete (navigator as unknown as Record<string, unknown>).gpu;
    });

    const view = (allFiles: object, filter = '') => (
        <WebGPUFallback allFiles={allFiles as never} activeFilter={filter} onFilterChange={() => {}} initialMode="folder" />
    );

    it('keeps the WebGPU tier, shows the reason, and recovers on Retry', async () => {
        vi.mocked(getVisualizerStream).mockRejectedValueOnce(new Error('HTTP 503'));
        renderWithProviders(view({}, 'fail'));
        await screen.findByText('Could not load the 3D graph.');
        expect(screen.getByText('HTTP 503')).toBeTruthy();
        expect(screen.getByText('WebGPU')).toBeTruthy();
        expect(screen.queryByText('WebGL2 Fallback')).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
        await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
        expect(screen.getByRole('application', { name: /Crystal Dreamscape/i })).toBeTruthy();
    });

    it('shows an empty dataset as a no-data state, not a failure', async () => {
        vi.mocked(getVisualizerStream).mockResolvedValueOnce(new ArrayBuffer(0));
        renderWithProviders(view({}, 'empty'));
        await screen.findByText('No 3D data to show yet.');
        expect(screen.queryByText('Could not load the 3D graph.')).toBeNull();
        expect(screen.getByText('WebGPU')).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
    });

    it('refetches after the file tree changes, but reuses the cache for the same tree', async () => {
        const a = {};
        const b = {};
        const mountOnce = async (files: object) => {
            const { unmount } = renderWithProviders(view(files, 'cache'));
            await screen.findByRole('application', { name: /Crystal Dreamscape/i });
            await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
            unmount();
        };
        await mountOnce(a);
        await mountOnce(a);
        expect(getVisualizerStream).toHaveBeenCalledTimes(1);
        await mountOnce(b);
        expect(getVisualizerStream).toHaveBeenCalledTimes(2);
    });
});
