import { create } from 'zustand';

interface SelectedChunk {
  // Narrowed from `number | string`: these ids travel to the backend as
  // `forced_chunk_ids`, which is typed `number[]` in useChatStream and queried
  // against `chunks.id` (a SQLite INTEGER). A string could never match a row,
  // so allowing one only deferred the failure to runtime.
  id: number;
  previewText?: string;
  filename?: string;
}

interface DreamscapeState {
  selectedChunks: SelectedChunk[];
  addChunk: (chunk: SelectedChunk) => void;
  removeChunk: (chunkId: number) => void;
  clearChunks: () => void;
}

export const useDreamscapeStore = create<DreamscapeState>((set) => ({
  selectedChunks: [],
  addChunk: (chunk) => set((state) => {
    // avoid duplicates
    if (state.selectedChunks.some((c) => c.id === chunk.id)) {
      return state;
    }
    return { selectedChunks: [...state.selectedChunks, chunk] };
  }),
  removeChunk: (chunkId) => set((state) => ({
    selectedChunks: state.selectedChunks.filter((c) => c.id !== chunkId)
  })),
  clearChunks: () => set({ selectedChunks: [] })
}));
