import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { FileTypeTreemap } from '../../components/FileTypeTreemap';
import { renderWithProviders } from '../test-utils';

describe('FileTypeTreemap Component', () => {
  const mockFiles = {
    'C:/projects/test': [
      { path: 'C:/projects/test/file1.txt', size: 100, type: 'txt', usage_count: 0 },
      { path: 'C:/projects/test/file2.py', size: 200, type: 'py', usage_count: 2 },
    ]
  };

  it('renders treemap view with options', () => {
    const onFilterChange = vi.fn();
    renderWithProviders(
      <FileTypeTreemap 
        allFiles={mockFiles}
        onFilterChange={onFilterChange}
        initialMode="folder"
      />
    );

    // Verify it renders the controls and echarts container
    expect(screen.getByTestId('mock-echarts-core')).toBeDefined();
    
    // Check mode toggle buttons
    expect(screen.getByText('BY FOLDERS')).toBeDefined();
    expect(screen.getByText('BY FILE TYPE')).toBeDefined();
  });
});
