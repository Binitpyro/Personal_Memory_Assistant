import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { AppShell } from '../../components/AppShell';
import { renderWithProviders } from '../test-utils';

// Mutable so each case can vary the payload. The mock factory is hoisted, so it
// has to read this lazily rather than close over a value.
let health: Record<string, unknown> = {};

vi.mock('../../useApi', () => ({
  useApi: vi.fn((_fn, opts) => {
    if (opts?.cacheKey === 'app-config') {
      return { data: { watch_dirs: [] }, loading: false, error: null, refetch: vi.fn() };
    }
    if (opts?.cacheKey === 'health') {
      return { data: health, loading: false, error: null, refetch: vi.fn() };
    }
    return { data: undefined, loading: false, error: null, refetch: vi.fn() };
  }),
  invalidateCache: vi.fn(),
}));

// Must export everything AppShell imports from '../../api'. A factory missing a
// newly added export is a recorded way to break this suite.
vi.mock('../../api', () => ({
  getAppConfig: vi.fn(),
  getHealth: vi.fn(),
}));

const base = { version: '0.0.71', status: 'ok', db: 'connected', split_brain_sync_status: 'idle' };

describe('AppShell subsystem health', () => {
  beforeEach(() => {
    health = { ...base };
  });

  it('says nothing when every subsystem is fine', () => {
    health = {
      ...base,
      subsystems: {
        ocr: { state: 'up', detail: '' },
        watcher: { state: 'up', detail: '' },
        reranker: { state: 'up', detail: '' },
      },
    };
    renderWithProviders(<AppShell />);
    expect(screen.queryByTestId('subsystem-warning')).toBeNull();
  });

  it('treats disabled and unknown as not-a-fault', () => {
    // ocr_enabled defaults to False, so a warning here would fire on a stock
    // install and generate bug reports about a feature nobody turned on.
    health = {
      ...base,
      subsystems: {
        ocr: { state: 'disabled', detail: '' },
        watcher: { state: 'unknown', detail: '' },
        reranker: { state: 'up', detail: '' },
      },
    };
    renderWithProviders(<AppShell />);
    expect(screen.queryByTestId('subsystem-warning')).toBeNull();
  });

  it('surfaces a genuinely failed subsystem', () => {
    health = {
      ...base,
      subsystems: {
        ocr: { state: 'down', detail: 'worker venv missing' },
        watcher: { state: 'up', detail: '' },
        reranker: { state: 'down', detail: 'model not found' },
      },
    };
    renderWithProviders(<AppShell />);
    const warning = screen.getByTestId('subsystem-warning');
    expect(warning).toBeTruthy();
    expect(warning.textContent).toContain('ocr');
    expect(warning.textContent).toContain('reranker');
    expect(warning.getAttribute('title')).toContain('Check the backend logs');
  });

  it('renders nothing when the backend omits the field entirely', () => {
    // An older backend, or a health call that failed - must not throw.
    renderWithProviders(<AppShell />);
    expect(screen.queryByTestId('subsystem-warning')).toBeNull();
  });
});
