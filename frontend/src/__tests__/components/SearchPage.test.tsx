import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { SearchPage } from '../../pages/SearchPage';
import { renderWithProviders } from '../test-utils';
import { useChatStream } from '../../hooks/useChatStream';

// Mock useApi directly using cacheKey
vi.mock('../../useApi', () => ({
  useApi: vi.fn((_, opts) => {
    if (opts?.cacheKey === 'query-history') {
      return { data: { history: [] }, loading: false, error: null, refetch: vi.fn() };
    }
    if (opts?.cacheKey === 'file-tree') {
      return { data: { folders: {}, total_files: 0, total_size: 0 }, loading: false, error: null, refetch: vi.fn() };
    }
    if (opts?.cacheKey === 'app-config') {
      return { data: { watch_dirs: [], google_drive_sync: false }, loading: false, error: null, refetch: vi.fn() };
    }
    return { data: undefined, loading: false, error: null, refetch: vi.fn() };
  }),
  invalidateCache: vi.fn(),
}));

// Mock api endpoints
vi.mock('../../api', () => {
  return {
    getQueryHistory: vi.fn(),
    clearQueryHistory: vi.fn(),
    getFileTree: vi.fn(),
    getAppConfig: vi.fn(),
    getProviders: vi.fn(),
    // The file tree refreshes off index-progress events now instead of a 15s
    // poll. Returns the unsubscribe the component calls on unmount.
    subscribeProgress: vi.fn(() => vi.fn()),
  };
});

// Mock the useChatStream hook
vi.mock('../../hooks/useChatStream', () => {
  return {
    useChatStream: vi.fn(),
  };
});

describe('SearchPage Component', () => {
  const mockExecuteSearch = vi.fn();
  const mockResetChat = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();

    vi.mocked(useChatStream).mockReturnValue({
      messages: [],
      executeSearch: mockExecuteSearch,
      resetChat: mockResetChat,
    } as any);
  });

  it('renders SearchPage with chat input', () => {
    renderWithProviders(<SearchPage />);

    expect(screen.getByPlaceholderText('Ask a follow-up or a new question...')).toBeDefined();
  });

  it('submits query when search button is clicked', () => {
    renderWithProviders(<SearchPage />);

    const input = screen.getByPlaceholderText('Ask a follow-up or a new question...');
    fireEvent.change(input, { target: { value: 'How does LinearBVH work?' } });

    // Located by accessible name, not by presentation. This previously read
    // `container.querySelector('.relative.flex.items-center.glass.rounded-2xl button')`,
    // which coupled the test to the composer's Tailwind classes and broke the
    // moment the glass styling was replaced — while still passing if the button
    // lost its label entirely. The name is the contract that matters.
    const sendButton = screen.getByRole('button', { name: /send question/i });

    fireEvent.click(sendButton);

    expect(mockExecuteSearch).toHaveBeenCalledWith('How does LinearBVH work?', expect.any(Object));
  });
});
