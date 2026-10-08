import { describe, it, expect, vi, beforeAll } from 'vitest';
import {
    NavigationController,
    NODE_STRIDE,
    NODE_OFF_PARENT_IDX,
    NODE_OFF_FLAGS,
    NO_PARENT,
} from '../../interaction/NavigationController';

/** A8-13: loadData's depth BFS dequeued with Array.shift(), O(n) per call on a large array. */
describe('NavigationController.loadData BFS', () => {
    function buffer(parents: number[]): ArrayBuffer {
        const buf = new ArrayBuffer(parents.length * NODE_STRIDE);
        const dv = new DataView(buf);
        parents.forEach((p, i) => {
            dv.setUint32(i * NODE_STRIDE + NODE_OFF_PARENT_IDX, p, true);
            dv.setUint32(i * NODE_STRIDE + NODE_OFF_FLAGS, i === 0 ? 1 : 0, true);
        });
        return buf;
    }

    it('computes depth correctly', () => {
        // 0 root; 1,2 under 0; 3 under 1; 4 under 3
        const nav = new NavigationController();
        nav.loadData(buffer([NO_PARENT, 0, 0, 1, 3]));
        expect(nav.nodes.map(n => n.depth)).toEqual([0, 1, 1, 2, 3]);
    });

    // shift() on a large array is O(n): 200k wide nodes took ~4s. The bound is
    // loose on purpose (the index-pointer version takes tens of ms).
    it('stays linear on a wide tree', () => {
        const n = 200_000;
        const parents = [NO_PARENT, ...Array.from({ length: n - 1 }, () => 0)];
        const buf = buffer(parents);
        const nav = new NavigationController();
        const t0 = performance.now();
        nav.loadData(buf);
        expect(performance.now() - t0).toBeLessThan(1500);
        expect(nav.nodes[n - 1].depth).toBe(1);
    }, 20_000);
});

/**
 * A8-08: device.destroy() resolves device.lost with reason 'destroyed'. That is
 * our own teardown; reporting it as a GPU loss demoted the tier on every
 * filter change.
 */
describe('WebGPURenderer teardown vs. device loss', () => {
    let Real: typeof import('../../renderer/WebGPURenderer').WebGPURenderer;
    beforeAll(async () => {
        (globalThis as Record<string, unknown>).GPUTextureUsage ??= {
            COPY_SRC: 1, COPY_DST: 2, TEXTURE_BINDING: 4, STORAGE_BINDING: 8, RENDER_ATTACHMENT: 16,
        };
        Real = (await vi.importActual<typeof import('../../renderer/WebGPURenderer')>(
            '../../renderer/WebGPURenderer',
        )).WebGPURenderer;
    });

    // init() registers the lost handler right after requestDevice and then
    // throws on the missing webgpu context, which is all this needs.
    async function build() {
        let resolveLost!: (info: { reason: string; message: string }) => void;
        const lost = new Promise<{ reason: string; message: string }>(r => { resolveLost = r; });
        const device = { lost, destroy: vi.fn(() => resolveLost({ reason: 'destroyed', message: '' })) };
        vi.stubGlobal('navigator', {
            gpu: { requestAdapter: async () => ({ requestDevice: async () => device }) },
        });
        const r = new Real(document.createElement('canvas'));
        const onLost = vi.fn();
        r.onDeviceLost = onLost;
        await r.init().catch(() => {});
        return { r, onLost, resolveLost };
    }

    it('does not report destroy() as a device loss', async () => {
        const { r, onLost } = await build();
        r.destroy();
        await new Promise(res => setTimeout(res, 0));
        expect(onLost).not.toHaveBeenCalled();
        vi.unstubAllGlobals();
    });

    it('still reports a genuine loss', async () => {
        const { onLost, resolveLost } = await build();
        resolveLost({ reason: 'unknown', message: 'gpu hung' });
        await new Promise(res => setTimeout(res, 0));
        expect(onLost).toHaveBeenCalledTimes(1);
        vi.unstubAllGlobals();
    });
});

/**
 * A8-12: every pick copies into and maps one shared MAP_READ buffer. A click
 * pick issued during a hover pick's readback hit a pending map and rejected.
 */
describe('WebGPURenderer.pick overlap', () => {
    async function fake() {
        const Real = (await vi.importActual<typeof import('../../renderer/WebGPURenderer')>(
            '../../renderer/WebGPURenderer',
        )).WebGPURenderer;
        const r = new Real(document.createElement('canvas')) as unknown as Record<string, unknown> & {
            pick(x: number, y: number): Promise<number | null>;
            destroy(): void;
        };
        (globalThis as Record<string, unknown>).GPUMapMode ??= { READ: 1 };
        let pending = false;
        const state = { destroyed: false };
        const pickBuffer = {
            mapAsync: async () => {
                if (state.destroyed) throw new Error('OperationError: buffer destroyed');
                if (pending) throw new Error('OperationError: map already pending');
                pending = true;
                await new Promise(res => setTimeout(res, 5));
            },
            getMappedRange: () => new ArrayBuffer(4),
            unmap: () => { pending = false; },
            destroy: () => { state.destroyed = true; },
        };
        const pass = {
            setPipeline() {}, setScissorRect() {}, setBindGroup() {}, setVertexBuffer() {},
            setIndexBuffer() {}, drawIndexed() {}, end() {},
        };
        Object.assign(r, {
            instanceBuffer: { destroy() {} }, deviceLost: false, visibleDirty: false,
            crystalCount: 1, bubbleCount: 0, crystalIndices: [7],
            pickTex: { createView: () => ({}), destroy() {} }, pickDepthTex: { createView: () => ({}), destroy() {} },
            cameraBindGroups: [{}], pickBuffer,
            device: {
                createCommandEncoder: () => ({
                    beginRenderPass: () => pass,
                    copyTextureToBuffer() {},
                    finish: () => ({}),
                }),
                queue: { submit() {} },
                destroy() {},
            },
        });
        return r;
    }

    it('serialises overlapping picks instead of double-mapping the buffer', async () => {
        const r = await fake();
        const results = await Promise.all([r.pick(1, 1), r.pick(2, 2)]);
        expect(results).toEqual([7, 7]);
    });

    it('a pick queued behind another resolves null after destroy()', async () => {
        const r = await fake();
        const first = r.pick(1, 1);
        const queued = r.pick(2, 2);
        r.destroy();
        await first.catch(() => {});
        await expect(queued).resolves.toBeNull();
    });
});
