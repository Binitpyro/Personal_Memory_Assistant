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
});
