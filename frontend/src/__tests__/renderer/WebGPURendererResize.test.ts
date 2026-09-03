import { describe, it, expect, vi, beforeAll } from 'vitest';

/**
 * Texture lifecycle across resize.
 *
 * `resize()` used to destroy nine of the ten size-dependent textures
 * `setupTextures()` creates — `pickDepthTex`, a full-resolution depth32float,
 * was missing from the list. `destroy()` freed it correctly at teardown, so it
 * only leaked on resize: 8.3 MB per distinct size at 1080p, and a ResizeObserver
 * during a window drag emits dozens of distinct sizes. Against the ~4GB VRAM
 * target that is the most expensive defect in this file.
 *
 * `setup.ts` replaces WebGPURenderer with a stub whose `resize()` is empty for
 * every test in the suite, which is exactly why nothing here could ever have
 * caught this. `importActual` is load-bearing: drop it and these tests pass
 * against the stub no matter what the real class does.
 *
 * The assertion is deliberately a set comparison rather than a hardcoded list of
 * texture names, so a texture added to `setupTextures()` later is covered here
 * without anyone remembering to update this test.
 *
 * init() is not reachable in jsdom (no adapter, ~15 pipelines, WGSL
 * compilation), so the instance is built from the prototype with only the fields
 * setupTextures/resize actually touch. rebuildBindGroups is stubbed for the same
 * reason: it needs bind group layouts, samplers and buffers that init() builds.
 */

interface FakeTexture {
    readonly id: number;
    readonly destroy: ReturnType<typeof vi.fn>;
}

let RealWebGPURenderer: typeof import('../../renderer/WebGPURenderer').WebGPURenderer;

beforeAll(async () => {
    // jsdom has no WebGPU globals, and setupTextures() ORs these usage flags
    // into every createTexture call. Real bit values so the flags stay
    // meaningful if a future assertion inspects them.
    (globalThis as Record<string, unknown>).GPUTextureUsage ??= {
        COPY_SRC: 1, COPY_DST: 2, TEXTURE_BINDING: 4,
        STORAGE_BINDING: 8, RENDER_ATTACHMENT: 16,
    };

    const mod = await vi.importActual<typeof import('../../renderer/WebGPURenderer')>(
        '../../renderer/WebGPURenderer',
    );
    RealWebGPURenderer = mod.WebGPURenderer;
});

function makeRenderer() {
    const created: FakeTexture[] = [];
    let nextId = 0;

    const device = {
        createTexture: vi.fn(() => {
            const t: FakeTexture = { id: nextId++, destroy: vi.fn() };
            created.push(t);
            return t;
        }),
    };

    const r = Object.create(RealWebGPURenderer.prototype);
    r.device = device;
    r.canvas = { width: 0, height: 0 };
    r.bloomMips = [];
    r.bloomUp = [];
    // Needs layouts/samplers/buffers that only init() builds; irrelevant here.
    r.rebuildBindGroups = () => {};

    return { r: r as { resize(w: number, h: number): void }, created };
}

describe('WebGPURenderer resize texture lifecycle', () => {
    it('actually loaded the real class, not the setup.ts stub', () => {
        // Guards the guard: the stub's resize() is a no-op, so without this the
        // rest of the file is vacuous.
        expect(RealWebGPURenderer.prototype).toHaveProperty('setupTextures');
    });

    it('destroys every texture the previous size allocated', () => {
        const { r, created } = makeRenderer();

        r.resize(320, 200);
        const firstPass = created.slice();
        expect(firstPass.length).toBeGreaterThan(0);

        r.resize(640, 400);

        const leaked = firstPass.filter(t => t.destroy.mock.calls.length === 0);
        expect(
            leaked.map(t => t.id),
            'textures allocated for the previous size were not freed',
        ).toEqual([]);
    });

    it('leaves exactly one generation of textures alive after several resizes', () => {
        const { r, created } = makeRenderer();

        r.resize(320, 200);
        const perGeneration = created.length;

        r.resize(640, 400);
        r.resize(800, 600);
        r.resize(1024, 768);

        const alive = created.filter(t => t.destroy.mock.calls.length === 0);
        // Only the most recent setupTextures() call's output should survive; a
        // missing destroy shows up here as a count that grows with resize count.
        expect(alive.length).toBe(perGeneration);
    });

    it('is a no-op when the size has not changed', () => {
        const { r, created } = makeRenderer();

        r.resize(320, 200);
        const afterFirst = created.length;
        r.resize(320, 200);

        expect(created.length).toBe(afterFirst);
    });
});
