import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import {
  WebGPUFallback,
  resizeTarget,
  canvasPixelSize,
  MAX_DPR,
} from '../../components/WebGPUFallback';
import { renderWithProviders } from '../test-utils';

// A renderer whose init() this test controls, so the window between "module
// loaded" and "first frame" can be held open and inspected. setup.ts's global
// stub resolves init() immediately, which is precisely the window that used to
// render as a blank canvas.
const initGate = { resolve: () => {} };
// The most recent renderer the component built, so a test can inspect what the
// view configured on it.
const built: { last: { smoothCamera: boolean } | null } = { last: null };
vi.mock('../../renderer/WebGPURenderer', () => ({
  WebGPURenderer: class {
    constructor() { built.last = this as unknown as { smoothCamera: boolean }; }
    nav = { nodes: [], breadcrumbs: [], loadNames: () => {}, getGraphNode: () => null };
    init() { return new Promise<void>(res => { initGate.resolve = res; }); }
    async loadData() {}
    render() {}
    resize() {}
    flyBy() {}
    markDirty() {}
    // Unset on purpose — the view must write it, and a default would
    // make the positive assertion vacuous.
    smoothCamera = undefined as unknown as boolean;
    async pick() { return null; }
    focusOnNode() {}
    handleMouseMove() {}
    handleZoom() {}
    destroy() {}
  },
}));

vi.mock('../../api', async importOriginal => ({
  ...(await importOriginal<typeof import('../../api')>()),
  getVisualizerStream: vi.fn(async () => new ArrayBuffer(64)),
  getVisualizerMeta: vi.fn(async () => ({})),
}));

describe('resizeTarget', () => {
  // Regression guard for the Insights page growing without bound. The renderer
  // writes canvas.width/height, which are the canvas's intrinsic layout size, so
  // observing the canvas made the ResizeObserver feed its own output back in and
  // multiply by DPR every cycle. jsdom has no layout engine and cannot reproduce
  // the loop, so assert the invariant that prevents it instead.
  it('never returns the canvas itself', () => {
    const wrapper = document.createElement('div');
    const canvas = document.createElement('canvas');
    wrapper.appendChild(canvas);

    expect(resizeTarget(canvas, wrapper)).toBe(wrapper);
    expect(resizeTarget(canvas, null)).toBe(wrapper);
    // A caller that mistakenly passes the canvas as its own wrapper must get
    // nothing back rather than re-arming the loop.
    expect(resizeTarget(canvas, canvas)).toBeNull();
  });

  it('returns null rather than the canvas when it has no parent', () => {
    const orphan = document.createElement('canvas');
    expect(resizeTarget(orphan, null)).toBeNull();
  });
});

describe('WebGPUFallback Component', () => {
  it('renders status message and canvas container', async () => {
    renderWithProviders(
      <WebGPUFallback 
        allFiles={{}}
        activeFilter=""
        onFilterChange={() => {}}
        initialMode="folder"
      />
    );

    // Since navigator.gpu is not available in JSDOM, it transitions to unsupported state
    const fallbackText = await screen.findByText(/Neither WebGPU nor WebGL2 available./i);
    expect(fallbackText).toBeDefined();
  });
});

/** One ResizeObserver entry. Only the two fields canvasPixelSize reads. */
function entry(css: { w: number; h: number }, devicePx?: { w: number; h: number }) {
  return {
    contentRect: { width: css.w, height: css.h },
    ...(devicePx
      ? { devicePixelContentBoxSize: [{ inlineSize: devicePx.w, blockSize: devicePx.h }] }
      : {}),
  } as unknown as ResizeObserverEntry;
}

describe('canvasPixelSize', () => {
  // The cap was written but unreachable: it was applied only on the contentRect
  // branch, and Chromium has had devicePixelContentBoxSize since 84 — the Tauri
  // webview and the primary browser path both take the other branch. A DPR-3
  // display therefore rendered at 3x, i.e. 2.25x the fragment work and 2.25x the
  // footprint of all twenty render targets.
  it('caps the device-pixel box on a display above the cap', () => {
    // 800x600 CSS on a DPR-3 display reports 2400x1800 real device pixels.
    expect(canvasPixelSize(entry({ w: 800, h: 600 }, { w: 2400, h: 1800 }), 3))
      .toEqual({ w: 1600, h: 1200 });
  });

  it('passes the device-pixel box through untouched at or below the cap', () => {
    expect(canvasPixelSize(entry({ w: 800, h: 600 }, { w: 1600, h: 1200 }), 2))
      .toEqual({ w: 1600, h: 1200 });
    expect(canvasPixelSize(entry({ w: 800, h: 600 }, { w: 800, h: 600 }), 1))
      .toEqual({ w: 800, h: 600 });
  });

  it('applies the cap on the contentRect branch too', () => {
    // contentRect is CSS pixels, so here the ratio is multiplied in, not out.
    expect(canvasPixelSize(entry({ w: 800, h: 600 }), 3))
      .toEqual({ w: 1600, h: 1200 });
    expect(canvasPixelSize(entry({ w: 800, h: 600 }), 1.5))
      .toEqual({ w: 1200, h: 900 });
  });

  it('never exceeds the cap on either branch, for any ratio', () => {
    for (const dpr of [0, 1, 1.25, 2, 2.5, 3, 4]) {
      const effective = Math.min(dpr || 1, MAX_DPR);
      const expected = { w: Math.round(1000 * effective), h: Math.round(500 * effective) };
      expect(canvasPixelSize(entry({ w: 1000, h: 500 }), dpr)).toEqual(expected);
      expect(
        canvasPixelSize(
          entry({ w: 1000, h: 500 }, { w: 1000 * (dpr || 1), h: 500 * (dpr || 1) }),
          dpr,
        ),
      ).toEqual(expected);
    }
  });

  it('falls back to 1 when the ratio is missing', () => {
    expect(canvasPixelSize(entry({ w: 300, h: 200 }), undefined))
      .toEqual({ w: 300, h: 200 });
  });
});

describe('3D loading indicator', () => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'gpu', {
      value: { requestAdapter: async () => ({}) },
      configurable: true,
    });
  });
  afterEach(() => {
    delete (navigator as unknown as Record<string, unknown>).gpu;
  });

  it('covers renderer init, then clears once a frame can be drawn', async () => {
    renderWithProviders(
      <WebGPUFallback allFiles={{}} activeFilter="" onFilterChange={() => {}} initialMode="folder" />
    );

    // Suspense in InsightsPage only covers the module download. This overlay is
    // what stands in for the device request, ~15 pipelines and WGSL compilation.
    const status = await screen.findByRole('status');
    expect(status.textContent).toMatch(/Starting GPU renderer/i);

    initGate.resolve();

    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
  });
});

/**
 * The keyboard/AT contract for the 3D view.
 *
 * The cursor arithmetic and the camera basis are covered as pure functions in
 * __tests__/interaction; what can only be checked here is that the view
 * actually EXPOSES those capabilities — a canvas that never became focusable,
 * or a tree that never rendered, would leave both of those passing while the
 * view stayed as mouse-only as it was before.
 */
describe('3D viewport accessibility', () => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'gpu', {
      value: { requestAdapter: async () => ({}) },
      configurable: true,
    });
  });
  afterEach(() => {
    delete (navigator as unknown as Record<string, unknown>).gpu;
  });

  it('exposes the canvas as a focusable, named, described viewport', async () => {
    renderWithProviders(
      <WebGPUFallback allFiles={{}} activeFilter="" onFilterChange={() => {}} initialMode="folder" />
    );
    initGate.resolve();

    const viewport = await screen.findByRole('application', { name: /Crystal Dreamscape/i });
    expect(viewport.tagName).toBe('CANVAS');
    expect(viewport.getAttribute('tabindex')).toBe('0');
    // The hint strip is the description, so the keys are discoverable without
    // opening the reference first.
    expect(viewport.getAttribute('aria-describedby')).toBe('dreamscape-keyhint');
    expect(document.getElementById('dreamscape-keyhint')?.textContent).toMatch(/WASD/);
  });

  it('carries a parallel tree and a live region for assistive tech', async () => {
    renderWithProviders(
      <WebGPUFallback allFiles={{}} activeFilter="" onFilterChange={() => {}} initialMode="folder" />
    );
    initGate.resolve();

    await screen.findByRole('application', { name: /Crystal Dreamscape/i });
    expect(screen.getByRole('tree', { name: 'Corpus hierarchy' })).toBeDefined();
    expect(document.querySelector('[aria-live="polite"]')).not.toBeNull();
  });

  it('does not throw on camera keys when the scene is empty', async () => {
    renderWithProviders(
      <WebGPUFallback allFiles={{}} activeFilter="" onFilterChange={() => {}} initialMode="folder" />
    );
    initGate.resolve();

    const viewport = await screen.findByRole('application', { name: /Crystal Dreamscape/i });
    // An empty corpus is the state this view spends its first seconds in.
    expect(() => {
      fireEvent.keyDown(viewport, { key: 'w' });
      fireEvent.keyUp(viewport, { key: 'w' });
      fireEvent.keyDown(viewport, { key: 'ArrowDown' });
      fireEvent.keyDown(viewport, { key: 'F' });
      fireEvent.blur(viewport);
    }).not.toThrow();
  });

  it('opens the reference on ? and lists both keymaps', async () => {
    renderWithProviders(
      <WebGPUFallback allFiles={{}} activeFilter="" onFilterChange={() => {}} initialMode="folder" />
    );
    initGate.resolve();

    const viewport = await screen.findByRole('application', { name: /Crystal Dreamscape/i });
    fireEvent.keyDown(viewport, { key: '?' });

    const dialog = document.querySelector('dialog');
    expect(dialog).not.toBeNull();
    // Unlike the treemap, the 3D view has a camera, so both halves apply.
    expect(dialog!.textContent).toContain('Viewport');
    expect(dialog!.textContent).toContain('Hierarchy');
  });
});

/**
 * The camera glide and `prefers-reduced-motion`.
 *
 * `approachEye` is unit-tested; what can only be checked here is that the view
 * actually turns smoothing off, which is the half that silently regresses. It
 * is not only a preference: the render loop parks after one frame when motion
 * is reduced and no key is held, so a glide would strand the camera a tenth of
 * the way to the node the user just selected.
 */
describe('reduced motion and the camera glide', () => {
  const setReducedMotion = (matches: boolean) => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('prefers-reduced-motion') ? matches : false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
  };

  beforeEach(() => {
    built.last = null;
    Object.defineProperty(navigator, 'gpu', {
      value: { requestAdapter: async () => ({}) },
      configurable: true,
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete (navigator as unknown as Record<string, unknown>).gpu;
  });

  // The gate can only be resolved once the renderer exists and init() has
  // actually been called — `initGate.resolve` is still the placeholder until
  // then, so resolving early leaves init pending forever and nothing after the
  // await ever runs.
  const renderAndInit = async () => {
    renderWithProviders(
      <WebGPUFallback allFiles={{}} activeFilter="" onFilterChange={() => {}} initialMode="folder" />
    );
    await waitFor(() => expect(built.last).not.toBeNull());
    initGate.resolve();
  };

  it('cuts instead of gliding when the user asks for reduced motion', async () => {
    setReducedMotion(true);
    await renderAndInit();
    await waitFor(() => expect(built.last!.smoothCamera).toBe(false));
  });

  it('keeps the glide when motion is not reduced', async () => {
    setReducedMotion(false);
    await renderAndInit();
    await waitFor(() => expect(built.last!.smoothCamera).toBe(true));
  });
});
