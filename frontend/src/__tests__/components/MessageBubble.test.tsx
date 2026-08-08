import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { MessageBubble } from '../../components/chat/MessageBubble';
import { renderWithProviders } from '../test-utils';
import { type Message } from '../../hooks/useChatStream';

describe('MessageBubble Component', () => {
  it('renders user message', () => {
    const userMsg: Message = {
      id: '1',
      role: 'user',
      content: 'What is the database size limit?',
    };

    renderWithProviders(
      <MessageBubble 
        message={userMsg}
        onNearMissClick={vi.fn()}
      />
    );

    expect(screen.getByText('What is the database size limit?')).toBeDefined();
  });

  it('renders assistant message with sources', () => {
    const mockSources = [
      { 
        id: '1', 
        file_path: 'C:/docs/limit.md', 
        score: 0.9,
        text: 'The database limit is 2GB.',
        sentence_offsets: '[[0, 26]]',
        _challenge_source: false
      }
    ];

    const assistantMsg: Message = {
      id: '2',
      role: 'assistant',
      content: 'The database limit is 2GB.',
      sources: mockSources,
    };

    renderWithProviders(
      <MessageBubble 
        message={assistantMsg}
        onNearMissClick={vi.fn()}
      />
    );

    expect(screen.getByText('The database limit is 2GB.')).toBeDefined();
    // Verify source path display (MessageBubble splits path and shows filename)
    expect(screen.getByText('limit.md')).toBeDefined();
  });

  it('leads the retrieval trace with what was searched for and not found', () => {
    // The not-found list is the credibility feature - saying "nothing in your
    // research notes on this" is what a chatbot with search cannot do. It must
    // be visible without expanding anything.
    const assistantMsg: Message = {
      id: '3',
      role: 'assistant',
      content: 'Here is what I found.',
      trace: [
        { kind: 'start', detail: 'Budget: 8000 tokens.' },
        {
          kind: 'decompose',
          detail: 'Split into 2 sub-question(s).',
          subqueries: ['how is the cache keyed', 'what is a wombat pipeline'],
        },
        { kind: 'retrieve', detail: 'how is the cache keyed - 4 result(s) from code', count: 4 },
        {
          kind: 'not_found',
          detail: 'Searched for but found nothing on: what is a wombat pipeline',
          subqueries: ['what is a wombat pipeline'],
        },
        { kind: 'done', detail: 'Finished after 1 iteration(s) (fixpoint).' },
      ],
    };

    renderWithProviders(
      <MessageBubble message={assistantMsg} onNearMissClick={vi.fn()} />
    );

    expect(screen.getByText('Searched for, but not found in your files')).toBeDefined();
    expect(screen.getByText('what is a wombat pipeline')).toBeDefined();
    // The step-by-step breakdown is supporting detail and stays collapsed.
    expect(screen.getByText('How this answer was assembled')).toBeDefined();
    expect(screen.queryByText(/Split into 2 sub-question/)).toBeNull();
  });

  it('renders nothing for a trace with no steps and no gaps', () => {
    const assistantMsg: Message = {
      id: '4',
      role: 'assistant',
      content: 'Answer.',
      trace: [{ kind: 'start', detail: 'Budget: 8000 tokens.' }],
    };

    renderWithProviders(
      <MessageBubble message={assistantMsg} onNearMissClick={vi.fn()} />
    );

    expect(screen.queryByText('How this answer was assembled')).toBeNull();
    expect(screen.queryByText('Searched for, but not found in your files')).toBeNull();
  });

  it('hides the open-file action outside the desktop shell', () => {
    // A browser tab cannot open a local file, so offering the button there
    // would be an affordance that silently does nothing.
    const assistantMsg: Message = {
      id: '5',
      role: 'assistant',
      content: 'Answer.',
      sources: [
        {
          file_path: 'C:/docs/limit.md',
          score: 0.9,
          text: 'The database limit is 2GB.',
        },
      ],
    };

    renderWithProviders(
      <MessageBubble message={assistantMsg} onNearMissClick={vi.fn()} />
    );

    expect(screen.queryByText('Open file')).toBeNull();
  });
});
