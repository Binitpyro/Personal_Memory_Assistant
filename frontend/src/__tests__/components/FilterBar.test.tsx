import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { FilterBar } from '../../components/chat/FilterBar';
import { renderWithProviders } from '../test-utils';

describe('FilterBar Component', () => {
  it('renders filter and triggers changes', () => {
    const setSelectedFileType = vi.fn();
    const setSelectedFolderTag = vi.fn();
    const setSelectedMode = vi.fn();

    renderWithProviders(
      <FilterBar 
        selectedFileType=""
        setSelectedFileType={setSelectedFileType}
        selectedFolderTag=""
        setSelectedFolderTag={setSelectedFolderTag}
        selectedMode="full_rag"
        setSelectedMode={setSelectedMode}
        selectedLength=""
        setSelectedLength={vi.fn()}
        fileTypeOptions={['.txt', '.py']}
        folderOptions={['docs', 'code']}
        disabled={false}
      />
    );

    // Verify type select is rendered
    const select = screen.getByDisplayValue('All file types');
    expect(select).toBeDefined();

    // Trigger file type change
    fireEvent.change(select, { target: { value: '.txt' } });
    expect(setSelectedFileType).toHaveBeenCalledWith('.txt');
  });

  it('does not offer Challenge mode', () => {
    // Its extra chunks are cut by the context budget, so the model never sees
    // them while the UI lists them as sources.
    renderWithProviders(
      <FilterBar
        selectedFileType=""
        setSelectedFileType={vi.fn()}
        selectedFolderTag=""
        setSelectedFolderTag={vi.fn()}
        selectedMode=""
        setSelectedMode={vi.fn()}
        selectedLength=""
        setSelectedLength={vi.fn()}
        fileTypeOptions={[]}
        folderOptions={[]}
        disabled={false}
      />
    );

    const style = screen.getByLabelText('Answer style') as HTMLSelectElement;
    const values = Array.from(style.options).map((o) => o.value);
    expect(values).toEqual(['', 'explain', 'verify', 'explore', 'distill']);
    expect(screen.queryByRole('option', { name: 'Challenge' })).toBeNull();
  });

  it('offers an answer length, and Clear filters resets it', () => {
    const setSelectedLength = vi.fn();
    renderWithProviders(
      <FilterBar
        selectedFileType=""
        setSelectedFileType={vi.fn()}
        selectedFolderTag=""
        setSelectedFolderTag={vi.fn()}
        selectedMode=""
        setSelectedMode={vi.fn()}
        selectedLength="detailed"
        setSelectedLength={setSelectedLength}
        fileTypeOptions={[]}
        folderOptions={[]}
        disabled={false}
      />
    );

    const length = screen.getByLabelText('Answer length') as HTMLSelectElement;
    expect(Array.from(length.options).map((o) => o.value)).toEqual(['', 'short', 'detailed']);
    fireEvent.change(length, { target: { value: 'short' } });
    expect(setSelectedLength).toHaveBeenCalledWith('short');

    // A length alone must surface Clear filters, and clearing must reset it.
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(setSelectedLength).toHaveBeenLastCalledWith('');
  });
});
