import { vi, afterEach } from 'vitest';
import React from 'react';
import { cleanup } from '@testing-library/react';

// Mock HTMLCanvasElement getContext for JSDOM
if (typeof HTMLCanvasElement !== 'undefined') {
  HTMLCanvasElement.prototype.getContext = vi.fn().mockReturnValue(null) as any;
}

// Mock ResizeObserver
class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = ResizeObserverMock as any;

// Mock localStorage and sessionStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] || null,
    setItem: (key: string, value: string) => { store[key] = value.toString(); },
    removeItem: (key: string) => { delete store[key]; },
    clear: () => { store = {}; },
    length: 0,
    key: (_index: number) => null,
  };
})();
Object.defineProperty(globalThis, 'localStorage', { value: localStorageMock });
Object.defineProperty(globalThis, 'sessionStorage', { value: localStorageMock });

// Clean up DOM after each test manually since globals are disabled in Vitest config
afterEach(async () => {
  // Gate read BEFORE cleanup(), because cleanup is what removes the container.
  // Present only when the test mounted a <Toaster/>, which is the only way to
  // reach the leak below - 7 of 214 tests, so the wait costs ~1.75s, not ~53s.
  const mountedToaster = document.querySelector('[data-sonner-toaster]') !== null;

  cleanup();

  // sonner's `deleteToast` schedules `removeToast` TIME_BEFORE_UNMOUNT (200ms)
  // later for the exit animation and NEVER clears it on unmount
  // (node_modules/sonner/dist/index.mjs:567-577). If jsdom is torn down inside
  // that window the callback runs against a dead environment and throws
  // `ReferenceError: window is not defined` out of React's
  // `resolveUpdatePriority`. Vitest reports that in "Unhandled Errors" and exits
  // NON-ZERO even though every test passed - which is exactly how it presented:
  // 29 files / 214 tests green, `Errors 1 error`, stage FAIL.
  //
  // Draining is the fix rather than `toast.dismiss()`, which routes through the
  // same `deleteToast` and would schedule another one.
  //
  // Same class as the react-query race already documented at the foot of
  // ExplorerPage.test.tsx, different source. It is load-dependent: the file
  // passes in isolation and only fails in the full suite.
  if (mountedToaster) {
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
});

// Mock @tauri-apps APIs
vi.mock('@tauri-apps/api/core', () => ({
  invoke: vi.fn().mockResolvedValue([8000, 'mock-token']),
}));

vi.mock('@tauri-apps/plugin-dialog', () => ({
  open: vi.fn().mockResolvedValue('/mocked/path'),
}));

vi.mock('@tauri-apps/plugin-shell', () => ({
  open: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: vi.fn().mockReturnValue({
    listen: vi.fn().mockResolvedValue(() => {}),
  }),
}));

// Mock echarts-for-react without spreading complex option objects to DOM element
vi.mock('echarts-for-react', () => {
  return {
    default: function MockReactECharts({ option }: any) {
      return React.createElement('div', { 'data-testid': 'mock-echarts', 'data-option': JSON.stringify(option ? {} : {}) });
    }
  };
});

vi.mock('echarts-for-react/lib/core', () => {
  return {
    default: function MockReactEChartsCore({ option }: any) {
      return React.createElement('div', { 'data-testid': 'mock-echarts-core', 'data-option': JSON.stringify(option ? {} : {}) });
    }
  };
});

// Mock WebGPURenderer
vi.mock('../renderer/WebGPURenderer', () => {
  return {
    WebGPURenderer: class MockWebGPURenderer {
      constructor() {}
      async init() {}
      async loadData() {}
      render() {}
      resize() {}
      flyBy() {}
      markDirty() {}
      smoothCamera = true;
    }
  };
});

// jsdom implements <dialog> as an inert element: showModal/close are absent, so
// any component using the native modal throws on mount. Two components rely on
// it now (ModelPicker, ShortcutOverlay), so the shim lives here rather than in
// each test file. It records state only — there is no layout or top layer to
// emulate, and the close EVENT is what the components actually listen to.
if (typeof HTMLDialogElement !== 'undefined') {
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) {
    this.open = false;
    this.dispatchEvent(new Event('close'));
  };
}
