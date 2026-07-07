import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { AppShell } from '../../components/AppShell';
import { renderWithProviders } from '../test-utils';

// Mock useApi directly using cacheKey
vi.mock('../../useApi', () => ({
  useApi: vi.fn((_, opts) => {
    if (opts?.cacheKey === 'app-config') {
      return { data: { watch_dirs: [], google_drive_sync: false }, loading: false, error: null, refetch: vi.fn() };
    }
    if (opts?.cacheKey === 'health') {
      return { data: { version: '0.0.70', status: 'ok', db: 'connected', split_brain_sync_status: 'idle' }, loading: false, error: null, refetch: vi.fn() };
    }
    return { data: undefined, loading: false, error: null, refetch: vi.fn() };
  }),
  invalidateCache: vi.fn(),
}));

// Mock api endpoints
vi.mock('../../api', () => {
  return {
    getAppConfig: vi.fn(),
    getHealth: vi.fn(),
  };
});

describe('AppShell Component', () => {
  it('renders AppShell with navigation items', () => {
    renderWithProviders(<AppShell />);

    // Check for navigation links
    expect(screen.getByText('Library')).toBeDefined();
    expect(screen.getByText('Search')).toBeDefined();
    expect(screen.getByText('Explorer')).toBeDefined();
    expect(screen.getByText('Insights')).toBeDefined();
    expect(screen.getByText('Settings')).toBeDefined();
  });
});
