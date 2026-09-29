import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { MessageBubble } from '../../components/chat/MessageBubble';
import { renderWithProviders } from '../test-utils';
import { type Message } from '../../hooks/useChatStream';

// A turn in the Answer pane. Its frames and receipt have their own panes and
// their own tests (AskPanes.test.tsx).

describe('MessageBubble Component', () => {
  it('renders user message', () => {
    const userMsg: Message = {
      id: '1',
      role: 'user',
      content: 'What is the database size limit?',
    };

    renderWithProviders(<MessageBubble message={userMsg} />);

    expect(screen.getByText('What is the database size limit?')).toBeDefined();
  });

  it('renders the assistant answer', () => {
    const assistantMsg: Message = {
      id: '2',
      role: 'assistant',
      content: 'The database limit is 2GB.',
    };

    renderWithProviders(<MessageBubble message={assistantMsg} />);

    expect(screen.getByText('The database limit is 2GB.')).toBeDefined();
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

    renderWithProviders(<MessageBubble message={assistantMsg} />);

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

    renderWithProviders(<MessageBubble message={assistantMsg} />);

    expect(screen.queryByText('How this answer was assembled')).toBeNull();
    expect(screen.queryByText('Searched for, but not found in your files')).toBeNull();
  });
});

describe('MessageBubble provenance', () => {
  const src = (n: number, path: string) => ({
    id: String(n),
    chunk_id: n,
    file_path: path,
    score: 0.9,
    text: `chunk ${n}`,
    _challenge_source: false,
  });

  const answer = (extra: Partial<Message> = {}): Message => ({
    id: 'a',
    role: 'assistant',
    content: 'An answer.',
    mode: 'full_rag',
    ...extra,
  });

  it('names the files a conflict was detected in', () => {
    // The banner asserted a conflict and never said where, so the reader could
    // neither verify nor dismiss it.
    // Backslash paths on purpose: the file-name split has to handle Windows
    // separators, and a forward-slash-only fixture would not notice if it did not.
    const sources = [src(1, 'C:\\docs\\alpha.md'), src(2, 'C:\\docs\\beta.md')];

    renderWithProviders(
      <MessageBubble message={answer({ sources, contradictions_found: true, contradiction_sources: [2] })} />,
    );

    expect(screen.getByText(/Possible disagreement in beta\.md/)).toBeDefined();
  });

  it('does not claim high confidence from a citation count', () => {
    // The only input was how many [n] tokens the model emitted - not relevance,
    // not a reranker score, and no check that the cited chunk supports the
    // sentence. Painting that green as "High Confidence" is the
    // hallucination-with-a-citation failure mode.
    renderWithProviders(
      <MessageBubble message={answer({ content: '<claim sources="[1][2][3]">A grounded sentence.</claim>' })} />,
    );

    const claim = screen.getByText('A grounded sentence.');
    expect(claim.getAttribute('title')).toContain('Cited 3 sources');
    expect(claim.getAttribute('title')).not.toContain('High Confidence');
  });
});
