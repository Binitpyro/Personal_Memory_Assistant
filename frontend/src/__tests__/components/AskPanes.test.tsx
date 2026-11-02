import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { FramesPanel } from '../../components/chat/FramesPanel';
import { Receipt } from '../../components/chat/Receipt';
import { renderWithProviders } from '../test-utils';
import { type Message } from '../../hooks/useChatStream';

// The Ask screen's side panes. These tests moved here from
// MessageBubble.test.tsx when the frames and the receipt left the answer for
// their own panes; the fixtures and assertions are the ones they had there.

const src = (n: number, path: string, extra: Record<string, unknown> = {}) => ({
  id: String(n),
  chunk_id: n,
  file_path: path,
  score: 0.9,
  text: `chunk ${n}`,
  _challenge_source: false,
  ...extra,
});

const answer = (extra: Partial<Message> = {}): Message => ({
  id: 'a',
  role: 'assistant',
  content: 'An answer.',
  mode: 'full_rag',
  ...extra,
});

describe('FramesPanel', () => {
  it('shows each source by file name', () => {
    renderWithProviders(
      <FramesPanel message={answer({ sources: [src(1, 'C:/docs/limit.md')] })} onForceInclude={vi.fn()} />,
    );
    expect(screen.getByText('limit.md')).toBeDefined();
  });

  it('lists every source, not the first three', () => {
    // The margin list once capped at three behind a '+N more' toggle. The
    // Frames pane is a column of its own and shows the whole set.
    const sources = [1, 2, 3, 4, 5].map(n => src(n, `C:/docs/file${n}.md`));
    renderWithProviders(<FramesPanel message={answer({ sources })} onForceInclude={vi.fn()} />);
    expect(screen.getByText('file5.md')).toBeDefined();
  });

  it('lists near misses under the rule, each with its way back in', () => {
    const onForceInclude = vi.fn();
    renderWithProviders(
      <FramesPanel
        message={answer({ sources: [src(1, 'C:/docs/kept.md')], near_misses: [src(9, 'C:/docs/near.md')] })}
        onForceInclude={onForceInclude}
      />,
    );
    expect(screen.getByText('Not used')).toBeDefined();
    fireEvent.click(screen.getByRole('button', { name: /force include/i }));
    expect(onForceInclude).toHaveBeenCalledWith(9);
  });

  it('hides the open-file action outside the desktop shell', () => {
    // A browser tab cannot open a local file, so offering the button there
    // would be an affordance that silently does nothing.
    renderWithProviders(
      <FramesPanel message={answer({ sources: [src(1, 'C:/docs/limit.md', { text: 'The database limit is 2GB.' })] })} onForceInclude={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /limit\.md/ }));
    expect(screen.queryByText('Open file')).toBeNull();
  });
});

describe('FramesPanel passage', () => {
  // Regression cover for the empty-source-panel defect. Every chunk ships
  // sentence_offsets as the literal string "[]": the offsets are only computed
  // when PMA_SENTENCE_OFFSETS is set and it defaults to "0", so this is not an
  // edge case, it is what every source looks like on a default install.
  const source = (extra: Record<string, unknown> = {}) =>
    src(1, 'C:/docs/limit.md', { text: 'The database size limit is set at ingest.', ...extra });

  const renderFrames = (s: ReturnType<typeof source>) =>
    renderWithProviders(<FramesPanel message={answer({ sources: [s] } as Partial<Message>)} onForceInclude={vi.fn()} />);

  const expand = () => {
    // The disclosure is the row, named by the file.
    fireEvent.click(screen.getByRole('button', { name: /limit\.md/ }));
  };

  it('shows the passage when sentence_offsets is the default "[]"', () => {
    renderFrames(source({ sentence_offsets: '[]' }));
    expand();
    expect(screen.getByText(/The database size limit is set at ingest\./)).toBeDefined();
  });

  it('shows the passage when sentence_offsets is absent entirely', () => {
    renderFrames(source());
    expand();
    expect(screen.getByText(/The database size limit is set at ingest\./)).toBeDefined();
  });

  it('still highlights sentences when real offsets are present', () => {
    renderFrames(source({ sentence_offsets: '[[0,12],[13,40]]' }));
    expand();
    // The highlight path splits the text into spans, so the whole string is no
    // longer one text node - assert the pieces instead. This is what stops the
    // fix from being "delete the feature".
    expect(screen.getByText('The database')).toBeDefined();
    expect(screen.getAllByText('Precision match').length).toBeGreaterThan(0);
  });

  it('falls back to the raw passage when the offsets do not parse', () => {
    renderFrames(source({ sentence_offsets: 'not json' }));
    expand();
    expect(screen.getByText(/The database size limit is set at ingest\./)).toBeDefined();
  });
});

describe('Receipt', () => {
  it('labels the answering style the user chose', () => {
    // `mode` on the response is the retrieval path; the user's prompt mode was
    // overwritten by it and never reached the UI.
    renderWithProviders(<Receipt msg={answer({ query_mode: 'challenge' })} />);
    expect(screen.getByText(/challenge/i)).toBeDefined();
    expect(screen.getByText(/RAG Answer/i)).toBeDefined();
  });

  it('shows no style line when the backend did not echo one', () => {
    // A cached answer genuinely does not know which mode produced it.
    renderWithProviders(<Receipt msg={answer()} />);
    expect(screen.queryByText('Style')).toBeNull();
  });

  it('prints in four-place dollars, marking estimated figures', () => {
    // Without a usage packet useChatStream estimates tokens and cost; the
    // receipt must say so rather than print a guess as a measurement. File
    // names stay out of it: they live in the Frames pane, and the model's [n]
    // does not index `sources`, so the receipt cannot pair them honestly.
    renderWithProviders(
      <Receipt
        msg={answer({
          sources: [src(1, 'C:/docs/kept.md')],
          near_misses: [src(9, 'C:/docs/near.md')],
          prompt_tokens: 1200,
          completion_tokens: 34,
          cost: 0,
          isEstimatedCost: true,
        })}
      />,
    );

    const receipt = screen.getByRole('group', { name: 'Receipt' }).textContent ?? '';
    expect(receipt).toContain('≈ $0.0000');
    expect(receipt).toContain('≈ 1,234');
    expect(receipt).toContain('Near misses, not used1');
    expect(receipt).not.toContain('kept.md');
  });
});
