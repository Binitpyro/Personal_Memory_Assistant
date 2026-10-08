/**
 * A8-06 restore: clicking a FILE in the 3D view pins that file's own chunks
 * (resolved by path on the backend), never the visualizer node index.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, fireEvent, act } from '@testing-library/react';
import { WebGPUFallback } from '../../components/WebGPUFallback';
import { useDreamscapeStore } from '../../store/dreamscapeStore';
import { getFileChunkIds } from '../../api';
import { renderWithProviders } from '../test-utils';

const pick = { index: 6 as number | null };
vi.mock('../../renderer/WebGPURenderer', () => ({
    WebGPURenderer: class {
        nav = {
            nodes: [] as { typeHash: number }[],
            breadcrumbs: [],
            loadNames: () => {},
            navigateTo: () => {},
            getGraphNode: () => ({ flags: 0, children: [] }),
            getFocusIndex: () => 0,
        };
        // Dense on purpose: the view iterates nav.nodes to build its name table.
        constructor() { this.nav.nodes = Array.from({ length: 8 }, (_, i) => ({ typeHash: i === 6 ? 42 : i === 7 ? 43 : 0 })); }
        smoothCamera = true;
        async init() {}
        async loadData() {}
        render() {}
        resize() {}
        flyBy() {}
        markDirty() {}
        focusOnNode() {}
        handleMouseMove() {}
        handleZoom() {}
        async pick() { return pick.index; }
        destroy() {}
    },
}));

vi.mock('../../api', async importOriginal => ({
    ...(await importOriginal<typeof import('../../api')>()),
    getVisualizerStream: vi.fn(async () => new ArrayBuffer(64)),
    getVisualizerMeta: vi.fn(async () => ({
        '42': { name: 'holiday_notes.txt', path: 'C:/Docs/holiday_notes.txt', size: 1, usage_count: 0, is_folder: false },
        '43': { name: 'Docs', path: 'C:/Docs', size: 1, usage_count: 0, is_folder: true },
    })),
    getFileChunkIds: vi.fn(async () => ({ path: 'C:/Docs/holiday_notes.txt', chunk_ids: [101, 102] })),
}));

const frames = () => act(() => new Promise<void>(res => setTimeout(res, 80)));

describe('A8-06: clicking a file pins that file by path', () => {
    beforeEach(() => {
        useDreamscapeStore.getState().clearChunks();
        vi.mocked(getFileChunkIds).mockClear();
        pick.index = 6;
        Object.defineProperty(navigator, 'gpu', { value: { requestAdapter: async () => ({}) }, configurable: true });
    });
    afterEach(() => {
        delete (navigator as unknown as Record<string, unknown>).gpu;
    });

    const click = async () => {
        renderWithProviders(
            <WebGPUFallback allFiles={{}} activeFilter="" onFilterChange={() => {}} initialMode="folder" />,
        );
        const canvas = await screen.findByRole('application', { name: /Crystal Dreamscape/i });
        await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
        await frames();
        fireEvent.mouseDown(canvas, { clientX: 10, clientY: 10 });
        fireEvent.mouseUp(canvas, { clientX: 10, clientY: 10 });
        await frames();

    };

    it('adds the chunk ids the backend returns for the clicked path, not the node index', async () => {
        await click();
        expect(getFileChunkIds).toHaveBeenCalledWith('C:/Docs/holiday_notes.txt');
        expect(useDreamscapeStore.getState().selectedChunks).toEqual([
            { id: 101, filename: 'holiday_notes.txt' },
            { id: 102, filename: 'holiday_notes.txt' },
        ]);
    });

    it('does not pin anything for a folder', async () => {
        pick.index = 7;
        await click();
        expect(getFileChunkIds).not.toHaveBeenCalled();
        expect(useDreamscapeStore.getState().selectedChunks).toEqual([]);
    });
});
