import { describe, it, expect, vi } from 'vitest';
import { renderHook, act, screen } from '@testing-library/react';
import { useChatStream, type Message } from '../hooks/useChatStream';
import { MessageBubble } from '../components/chat/MessageBubble';
import { subscribeQuery } from '../api';
import { renderWithProviders } from './test-utils';

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  subscribeQuery: vi.fn(),
}));

// The shape retrieval._extractive_answer emits when no model is configured.
const EXTRACTIVE =
  'No language model is connected, so here are the closest passages in your files:\n\n' +
  '1. <claim sources="[1]">The greenhouse vents open at 27 degrees.</claim> (greenhouse.md) [1]';

const stream = (frames: unknown[]) =>
  vi.mocked(subscribeQuery).mockImplementation(((_p: unknown, cb: (c: unknown) => void) => {
    queueMicrotask(() => frames.forEach(cb));
    return () => {};
  }) as never);

async function ask() {
  const { result } = renderHook(() => useChatStream(() => {}));
  await act(async () => {
    await result.current.executeSearch('q', {});
  });
  return result.current.messages.at(-1)!;
}

describe('no-LLM answer (release blocker B3)', () => {
  it('marks the turn when the content frame says no model is available', async () => {
    stream([
      { type: 'sources', sources: [{ file_path: 'greenhouse.md', text: 'x' }], latency_ms: 1 },
      { type: 'content', text: EXTRACTIVE, llm_available: false },
      { type: 'done' },
    ]);
    const msg = await ask();

    expect(msg.llm_available).toBe(false);
    expect(msg.content).toBe(EXTRACTIVE);
    expect(msg.error).toBeUndefined();
    expect(msg.sources).toHaveLength(1);
  });

  it('leaves an ordinary answer unmarked', async () => {
    stream([{ type: 'content', text: 'A written answer.' }, { type: 'done' }]);
    const msg = await ask();

    expect(msg.llm_available).toBeUndefined();
  });

  it('renders the passages as citations, with an Add a model link', () => {
    const msg: Message = { id: 'a', role: 'assistant', content: EXTRACTIVE, llm_available: false };
    renderWithProviders(<MessageBubble message={msg} />);

    expect(screen.getByText('The greenhouse vents open at 27 degrees.')).toBeDefined();
    // The existing <claim> citation UI, not literal tags.
    expect(screen.getByText(/Cited 1 source: \[1\]/)).toBeDefined();
    expect(screen.queryByText(/<claim/)).toBeNull();
    const link = screen.getByRole('link', { name: 'Add a model' });
    expect(link.getAttribute('href')).toBe('/settings/providers');
    expect(screen.getByText(/not a written answer/)).toBeDefined();
  });

  it('shows no model prompt on an ordinary answer', () => {
    const msg: Message = { id: 'b', role: 'assistant', content: 'A written answer.' };
    renderWithProviders(<MessageBubble message={msg} />);

    expect(screen.queryByRole('link', { name: 'Add a model' })).toBeNull();
  });
});
