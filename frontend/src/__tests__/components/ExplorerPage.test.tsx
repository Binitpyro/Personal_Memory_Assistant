import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { ExplorerPage } from '../../pages/ExplorerPage';
import { renderWithProviders } from '../test-utils';
import { removeFolderIndex } from '../../api';

// The keys of `folders` are indexed folder ROOT PATHS, matching what
// GET /api/files/tree returns. They used to be `folder_tag`, which holds only
// the basename -- and because this fixture already spelled the key as a path,
// it agreed with the component and disagreed with the server, which is why the
// broken tree it produced was never caught here.
const ROOT = 'C:/projects/test';

vi.mock('../../useApi', () => ({
  useApi: vi.fn((_, opts) => {
    if (opts?.cacheKey === 'file-tree') {
      return {
        data: {
          folders: {
            'C:/projects/test': [
              { path: 'C:/projects/test/file1.txt', size: 1024, type: 'txt', usage_count: 0 },
              { path: 'C:/projects/test/sub/file2.pdf', size: 2048, type: 'pdf', usage_count: 1 },
            ],
          },
          total_files: 2,
          total_size: 3072,
        },
        loading: false,
        error: null,
        refetch: vi.fn(),
      };
    }
    return { data: undefined, loading: false, error: null, refetch: vi.fn() };
  }),
  invalidateCache: vi.fn(),
}));

// Mock api endpoints
// Must list every export ExplorerPage.tsx imports from '../api'. vi.mock with a
// factory replaces the whole module, so an export missing here throws at render
// ("No <name> export is defined on the ... mock") rather than falling through to
// the real one.
vi.mock('../../api', () => ({
  getFileTree: vi.fn(),
  removeFolderIndex: vi.fn(() => Promise.resolve({ message: 'ok', chunks_removed: 0 })),
  getOcrStatus: vi.fn(),
  forceOcr: vi.fn(),
}));

describe('ExplorerPage Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    vi.spyOn(window, 'alert').mockImplementation(() => {});
  });

  it('renders ExplorerPage and labels the root with its full path', () => {
    renderWithProviders(<ExplorerPage />);

    expect(screen.getByText('Explorer')).toBeDefined();
    expect(screen.getByText(ROOT)).toBeDefined();
  });

  it('does not re-nest the absolute path under the root', () => {
    // The defect: with the root prefix left unstripped, the tree rendered
    // root > "C:" > "projects" > "test" > files -- the drive and the indexed
    // folder's own parent appearing as nodes inside it.
    renderWithProviders(<ExplorerPage />);

    expect(screen.queryByText('C:')).toBeNull();
    expect(screen.queryByText('projects')).toBeNull();
    // 'test' only ever appears as part of the full-path root label, never as a
    // node of its own.
    expect(screen.queryByText('test')).toBeNull();
  });

  it('renders only genuine subfolders below the root', () => {
    renderWithProviders(<ExplorerPage />);

    // 'sub' is the one real directory between the root and file2.pdf.
    expect(screen.getByText('sub')).toBeDefined();
    // getAllByText: file names also appear in the Largest Data / Cold Files tiles.
    expect(screen.getAllByText('file1.txt').length).toBeGreaterThan(0);
  });

  it('removes the folder using the real root path', async () => {
    // fullPath used to be built from the group key as if it were a name, so the
    // request carried "test" (or "test/C:/projects") and the LIKE prefix on
    // files.path matched nothing -- while the UI still reported success.
    renderWithProviders(<ExplorerPage />);

    const del = screen.getAllByTitle('Delete this folder index')[0];
    fireEvent.click(del);

    expect(removeFolderIndex).toHaveBeenCalledWith([ROOT]);
  });
});
