import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent, within } from '@testing-library/react';
import { SearchPage } from '../pages/SearchPage';
import { renderWithProviders } from './test-utils';

// The shape /api/query/history really returns (app/api/search.py query_history).
const history = [
  { id: 2, question: 'what is in my notes', timestamp: '2026-10-08 10:00:00' },
  { id: 1, question: 'what is in my notes', timestamp: '2026-10-08 09:00:00' },
];

vi.mock('../useApi', () => ({
  useApi: vi.fn((_, opts) => ({
    data: opts?.cacheKey === 'query-history' ? { history } : undefined,
    loading: false,
    error: null,
    refetch: vi.fn(),
  })),
  invalidateCache: vi.fn(),
}));

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  getQueryHistory: vi.fn(),
  getFileTree: vi.fn(),
  subscribeProgress: vi.fn(() => () => {}),
  getCurrentProvider: vi.fn().mockResolvedValue({ provider: 'ollama', model: 'm', source: 'default' }),
}));

Element.prototype.scrollIntoView = vi.fn();

describe('A7-14: recent-question rows have unique keys', () => {
  it('asking the same question twice does not produce duplicate React keys', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    renderWithProviders(<SearchPage />);
    fireEvent.click(screen.getByRole('button', { name: /recent/ }));
    expect(within(screen.getByRole('listbox', { name: 'Recent questions' })).getAllByRole('option')).toHaveLength(2);
    const dup = err.mock.calls.filter(c => String(c[0]).includes('same key'));
    err.mockRestore();
    expect(dup).toEqual([]);
  });
});
