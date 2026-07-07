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
});
