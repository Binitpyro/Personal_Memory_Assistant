import { Filter } from 'lucide-react';

interface FilterBarProps {
  selectedFileType: string;
  setSelectedFileType: (val: string) => void;
  selectedFolderTag: string;
  setSelectedFolderTag: (val: string) => void;
  selectedMode: string;
  setSelectedMode: (val: string) => void;
  fileTypeOptions: string[];
  folderOptions: string[];
  disabled: boolean;
}

export function FilterBar({
  selectedFileType,
  setSelectedFileType,
  selectedFolderTag,
  setSelectedFolderTag,
  selectedMode,
  setSelectedMode,
  fileTypeOptions,
  folderOptions,
  disabled
}: FilterBarProps) {
  return (
    <div className="flex flex-wrap items-center gap-2 px-1">
      <span className="text-[10px] text-text-secondary font-bold uppercase tracking-widest flex items-center gap-1">
        <Filter className="w-3 h-3" /> Quick Filters
      </span>
      <select
        value={selectedFileType}
        onChange={(e) => setSelectedFileType(e.target.value)}
        className="text-[11px] bg-white/5 border border-white/10 rounded-lg px-2 py-1 text-text-primary"
        disabled={disabled}
      >
        <option value="">All file types</option>
        {fileTypeOptions.map((ext) => (
          <option key={ext} value={ext}>{ext}</option>
        ))}
      </select>
      <select
        value={selectedFolderTag}
        onChange={(e) => setSelectedFolderTag(e.target.value)}
        className="text-[11px] bg-white/5 border border-white/10 rounded-lg px-2 py-1 text-text-primary"
        disabled={disabled}
      >
        <option value="">All folders</option>
        {folderOptions.map((folder) => (
          <option key={folder} value={folder}>{folder}</option>
        ))}
      </select>
      <select
        value={selectedMode}
        onChange={(e) => setSelectedMode(e.target.value)}
        className="text-[11px] bg-white/5 border border-white/10 rounded-lg px-2 py-1 text-text-primary"
        disabled={disabled}
      >
        <option value="">Default Mode</option>
        <option value="explain">Explain</option>
        <option value="verify">Verify</option>
        <option value="explore">Explore</option>
        <option value="distill">Distill</option>
        <option value="challenge">Challenge</option>
      </select>
      {(selectedFileType || selectedFolderTag || selectedMode) && (
        <button
          type="button"
          onClick={() => {
            setSelectedFileType('')
            setSelectedFolderTag('')
            setSelectedMode('')
          }}
          className="text-[10px] px-2 py-1 rounded-lg border border-primary/20 text-primary-light hover:bg-primary/10"
        >
          Clear filters
        </button>
      )}
    </div>
  );
}
