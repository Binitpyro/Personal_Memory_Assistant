import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { DiagnosticsPage } from '../../pages/DiagnosticsPage';
import { renderWithProviders } from '../test-utils';

// Mutable so each case can vary a payload. The mock factory is hoisted, so it
// has to read these lazily rather than close over a value.
let health: Record<string, unknown> = {};
let metrics: Record<string, unknown> | undefined;
let compact: Record<string, unknown> | undefined;
let ocr: Record<string, unknown> | undefined;

vi.mock('../../useApi', () => ({
  useApi: vi.fn((_fn, opts) => {
    const byKey: Record<string, unknown> = {
      health,
      'system-metrics': metrics,
      'compact-status': compact,
      'ocr-status': ocr,
    };
    return {
      data: byKey[opts?.cacheKey as string],
      loading: false,
      error: null,
      refetch: vi.fn(),
    };
  }),
  invalidateCache: vi.fn(),
}));

// Must export everything DiagnosticsPage imports from '../../api'. A factory
// missing a newly added export is a recorded way to break this suite.
vi.mock('../../api', () => ({
  getHealth: vi.fn(),
  getMetrics: vi.fn(),
  getCompactStatus: vi.fn(),
  compactDatabase: vi.fn(),
  getOcrStatus: vi.fn(),
  reembedVectors: vi.fn(),
}));

const baseHealth = { version: '0.0.72', status: 'ok', db: 'connected', indexing: 'idle' };

describe('DiagnosticsPage', () => {
  beforeEach(() => {
    health = { ...baseHealth };
    metrics = {};
    compact = { is_running: false, last_run: null, error: null };
    ocr = undefined;
  });

  it('renders the subsystem detail string the backend records', () => {
    // The whole point of the screen. `state.subsystems` has carried a 200-char
    // `detail` since it was introduced and it was rendered nowhere - the
    // sidebar pip showed only the name and "check the backend logs".
    health = {
      ...baseHealth,
      subsystems: {
        ocr: { state: 'down', detail: 'uv is not installed on PATH' },
      },
    };

    renderWithProviders(<DiagnosticsPage />);

    expect(screen.getByText('uv is not installed on PATH')).toBeTruthy();
  });

  it('does not render disabled or unknown as faults', () => {
    health = {
      ...baseHealth,
      subsystems: {
        ocr: { state: 'disabled', detail: '' },
        watcher: { state: 'unknown', detail: '' },
      },
    };

    renderWithProviders(<DiagnosticsPage />);

    expect(screen.getByText('Turned off')).toBeTruthy();
    expect(screen.getByText('Not started')).toBeTruthy();
    expect(screen.queryByText('Not running')).toBeNull();
  });

  it('warns when the index was built with a different embedding model', () => {
    health = {
      ...baseHealth,
      embedding_signature: {
        stored: 'old-model',
        current: 'new-model',
        mismatch: true,
        reembed: 'idle',
      },
    };

    renderWithProviders(<DiagnosticsPage />);

    expect(screen.getByText(/built with a different model/i)).toBeTruthy();
    expect(screen.getByText('old-model')).toBeTruthy();
    expect(screen.getByText('new-model')).toBeTruthy();
  });

  it('offers the rebuild only when there is something to repair', () => {
    // The action must never be one unguarded click, and must not appear at all
    // on a healthy index.
    health = {
      ...baseHealth,
      embedding_signature: { stored: 'a', current: 'b', mismatch: true, reembed: 'idle' },
    };
    const { unmount } = renderWithProviders(<DiagnosticsPage />);
    expect(screen.getByRole('button', { name: /Rebuild embeddings/i })).toBeTruthy();
    unmount();

    health = {
      ...baseHealth,
      embedding_signature: { stored: 'a', current: 'a', mismatch: false, reembed: 'idle' },
    };
    renderWithProviders(<DiagnosticsPage />);
    expect(screen.queryByRole('button', { name: /Rebuild embeddings/i })).toBeNull();
  });

  it('disables the rebuild while one is already running', () => {
    health = {
      ...baseHealth,
      embedding_signature: { stored: 'a', current: 'b', mismatch: true, reembed: 'running' },
    };

    renderWithProviders(<DiagnosticsPage />);

    const btn = screen.getByRole('button', { name: /Rebuilding/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it('stays quiet when the signature matches', () => {
    health = {
      ...baseHealth,
      embedding_signature: { stored: 'same', current: 'same', mismatch: false, reembed: 'idle' },
    };

    renderWithProviders(<DiagnosticsPage />);

    expect(screen.queryByText(/built with a different model/i)).toBeNull();
  });

  it('renders latency percentiles that previously had no consumer at all', () => {
    metrics = {
      retrieval: { avg: 12, p50: 10, p95: 30, p99: 44, max: 51, count: 7 },
    };

    renderWithProviders(<DiagnosticsPage />);

    expect(screen.getByText('Retrieval')).toBeTruthy();
    expect(screen.getByText('30')).toBeTruthy();
    expect(screen.getByText('44')).toBeTruthy();
  });

  it('shows an empty state rather than crashing before any query has run', () => {
    // get_stats() omits stages with no history, so {} is the normal cold start.
    metrics = {};

    renderWithProviders(<DiagnosticsPage />);

    expect(screen.getByText(/Nothing measured yet/i)).toBeTruthy();
  });

  it('survives a backend that omits every optional field', () => {
    health = { ...baseHealth };
    metrics = undefined;
    compact = undefined;

    renderWithProviders(<DiagnosticsPage />);

    expect(screen.getByText('Diagnostics')).toBeTruthy();
  });

  it('reports the OCR engine actually in use once a tier is installed', () => {
    // ep and model_version were stamped at install and returned by the status
    // route, but were absent from the OcrStatus type and so unreachable.
    ocr = {
      tier: 'cpu',
      installed: true,
      enabled: true,
      model_version: 'ppocrv4-mobile',
      ep: 'CPUExecutionProvider',
      installed_at: '2026-08-06',
      pages_pending: 0,
      queue: {},
    };

    renderWithProviders(<DiagnosticsPage />);

    expect(screen.getByText('ppocrv4-mobile')).toBeTruthy();
    expect(screen.getByText('CPUExecutionProvider')).toBeTruthy();
  });
});
