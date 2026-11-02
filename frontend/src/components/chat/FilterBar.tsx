
interface FilterBarProps {
  selectedFileType: string;
  setSelectedFileType: (val: string) => void;
  selectedFolderTag: string;
  setSelectedFolderTag: (val: string) => void;
  selectedMode: string;
  setSelectedMode: (val: string) => void;
  selectedLength: string;
  setSelectedLength: (val: string) => void;
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
  selectedLength,
  setSelectedLength,
  fileTypeOptions,
  folderOptions,
  disabled
}: FilterBarProps) {
  // The ask bar's scope: what this question searches. Each select is named,
  // because its first option is a value, not a label.
  const select = 'h-[34px] max-w-[9.5rem] bg-transparent border border-edge px-2 font-mono text-[11px] tracking-[.1em] uppercase text-text-primary';
  return (
    <div className="flex items-center gap-1.5">
      <select
        value={selectedFileType}
        onChange={(e) => setSelectedFileType(e.target.value)}
        aria-label="File type"
        className={select}
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
        aria-label="Folder"
        className={select}
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
        aria-label="Answer style"
        className={select}
        disabled={disabled}
      >
        <option value="">Default Mode</option>
        <option value="explain">Explain</option>
        <option value="verify">Verify</option>
        <option value="explore">Explore</option>
        <option value="distill">Distill</option>
      </select>
      <select
        value={selectedLength}
        onChange={(e) => setSelectedLength(e.target.value)}
        aria-label="Answer length"
        className={select}
        disabled={disabled}
      >
        <option value="">Default length</option>
        <option value="short">Short</option>
        <option value="detailed">Detailed</option>
      </select>
      {(selectedFileType || selectedFolderTag || selectedMode || selectedLength) && (
        <button
          type="button"
          onClick={() => {
            setSelectedFileType('')
            setSelectedFolderTag('')
            setSelectedMode('')
            setSelectedLength('')
          }}
          className="tap-24 font-mono text-[11px] tracking-[.1em] uppercase text-text-secondary hover:text-text-primary underline underline-offset-4"
        >
          Clear filters
        </button>
      )}
    </div>
  );
}
