import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { MessageBubble } from '../components/chat/MessageBubble';
import type { Message } from '../hooks/useChatStream';

const parsed = vi.hoisted(() => ({ texts: [] as string[] }));
vi.mock('react-markdown', () => ({
  default: ({ children }: { children: string }) => {
    parsed.texts.push(children);
    return <p>{children}</p>;
  },
}));

describe('A7-12: only the streaming answer is re-parsed on a flush', () => {
  it('does not re-render earlier answers when a later message changes', () => {
    const older: Message = { id: 'a', role: 'assistant', content: 'old answer' };
    const list = (live: string) => (
      <MemoryRouter>
        <MessageBubble message={older} />
        <MessageBubble message={{ id: 'b', role: 'assistant', content: live, isStreaming: true }} />
      </MemoryRouter>
    );
    const { rerender } = render(list('t'));
    parsed.texts.length = 0;
    rerender(list('to'));
    rerender(list('tok'));
    expect(parsed.texts).toEqual(['to', 'tok']);
  });
});
