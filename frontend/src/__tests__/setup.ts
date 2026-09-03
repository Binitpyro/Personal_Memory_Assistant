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
afterEach(() => {
  cleanup();
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
