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
    getCurrentProvider: vi.fn().mockResolvedValue({ provider: 'ollama', model: 'gemma4-local:latest', source: 'default' }),
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

// jsdom does not implement scrollIntoView; the page scrolls to the newest message.
Element.prototype.scrollIntoView = vi.fn();

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
  describe('failed answer, rendered inside its bubble', () => {
    // The error now lives on the assistant message (useChatStream stores it),
    // so these render the page with such a message rather than rejecting
    // executeSearch. There is no separate banner to find.
    function renderFailed(code: string | undefined, content: string) {
      vi.mocked(useChatStream).mockReturnValue({
        messages: [
          { id: 'u', role: 'user', content: 'q' },
          { id: 'a', role: 'assistant', content, error: { text: 'raw provider text', code } },
        ],
        executeSearch: mockExecuteSearch,
        resetChat: mockResetChat,
      } as any);
      renderWithProviders(<SearchPage />);
      return screen.getByRole('alert');
    }

    it('shows the error in the conversation log, not in a banner above it', () => {
      const alert = renderFailed('empty_answer', '');
      expect(screen.getByRole('log', { name: 'Conversation' }).contains(alert)).toBe(true);
      expect(screen.getAllByRole('alert')).toHaveLength(1);
    });

    it.each([
      ['context_overflow', "The question plus its sources is too long for this model's context. Narrow the folder/type filter, or load the model with a larger context."],
      ['empty_answer', 'The model returned an empty reply. Try again or pick another model.'],
      ['local_provider_down', "The local model server isn't running. Start Ollama/LM Studio and try again."],
    ])('maps %s to one plain sentence', (code, sentence) => {
      const alert = renderFailed(code, '');
      expect(alert.textContent).toContain(sentence);
      expect(alert.textContent).not.toContain('raw provider text');
    });

    it('titles a failed turn with no content "No answer written"', () => {
      const alert = renderFailed(undefined, '   ');
      expect(alert.textContent).toContain('No answer written');
      expect(alert.textContent).toContain('raw provider text');
    });

    it('titles a failed turn that already has content "Answer cut short"', () => {
      const alert = renderFailed('empty_answer', 'partial answer');
      expect(alert.textContent).toContain('Answer cut short');
      expect(alert.textContent).not.toContain('No answer written');
      expect(screen.getByText('partial answer')).toBeDefined();
    });
  });
});
