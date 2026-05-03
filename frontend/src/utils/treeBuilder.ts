import { type FileEntry } from '../api'

export const CATEGORY_MAP: Record<string, string> = {
  '.py': 'Code', '.js': 'Code', '.ts': 'Code', '.tsx': 'Code', '.jsx': 'Code',
  '.java': 'Code', '.c': 'Code', '.cpp': 'Code', '.h': 'Code', '.hpp': 'Code',
  '.rs': 'Code', '.go': 'Code', '.rb': 'Code', '.sh': 'Code', '.bat': 'Code',
  '.cs': 'Code', '.swift': 'Code', '.kt': 'Code', '.scala': 'Code',
  '.lua': 'Code', '.r': 'Code', '.pl': 'Code', '.php': 'Code', '.m': 'Code',
  '.sql': 'Code', '.css': 'Web', '.html': 'Web', '.htm': 'Web',
  '.scss': 'Web', '.sass': 'Web', '.less': 'Web', '.vue': 'Web', '.svelte': 'Web',
  '.json': 'Data', '.xml': 'Data', '.yaml': 'Data', '.yml': 'Data',
  '.pdf': 'Documents', '.doc': 'Documents', '.docx': 'Documents',
  '.txt': 'Documents', '.md': 'Documents', '.rtf': 'Documents',
  '.odt': 'Documents', '.pages': 'Documents', '.tex': 'Documents',
  '.log': 'Documents', '.csv': 'Data',
  '.uasset': 'Unreal', '.umap': 'Unreal', '.uproject': 'Unreal',
  '.png': 'Images', '.jpg': 'Images', '.jpeg': 'Images', '.gif': 'Images',
  '.svg': 'Images', '.zip': 'Archives', '.exe': 'Executables',
}

export const COLORS: Record<string, string> = {
  Code: '#3572A5', Web: '#e34c26', Documents: '#e11d48', Data: '#a4c639',
  Unreal: '#007fff', Images: '#a36ad5', Media: '#1db954', Presentations: '#d24726',
  Archives: '#f59e0b', Executables: '#9333ea', Other: '#6b7280',
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

export function lightenColor(hex: string, amount: number): string {
  const c = hex.replace('#', '')
  if (c.length !== 6) return hex
  const r = parseInt(c.substring(0, 2), 16)
  const g = parseInt(c.substring(2, 4), 16)
  const b = parseInt(c.substring(4, 6), 16)
  const lr = Math.min(255, Math.round(r + (255 - r) * amount))
  const lg = Math.min(255, Math.round(g + (255 - g) * amount))
  const lb = Math.min(255, Math.round(b + (255 - b) * amount))
  return `#${lr.toString(16).padStart(2, '0')}${lg.toString(16).padStart(2, '0')}${lb.toString(16).padStart(2, '0')}`
}

export function normalizePath(p: string): string {
  let norm = p.replaceAll('\\', '/');
  while (norm.endsWith('/')) { norm = norm.slice(0, -1); }
  return norm;
}

export function findCommonPrefix(paths: string[]): string {
  if (paths.length === 0) return '';
  const sorted = [...paths].sort((a, b) => a.localeCompare(b));
  const first = sorted[0].split('/');
  const last = (sorted.at(-1) || '').split('/');
  let i = 0;
  while (i < first.length && i < last.length && first[i] === last[i]) {
    i++;
  }
  return first.slice(0, i).join('/');
}

export function buildTypeTree(flatFiles: FileEntry[], getVal: (s: number) => number) {
  const structure = new Map<string, Map<string, FileEntry[]>>()
  for (const f of flatFiles) {
    const ext = ('.' + f.path.split('.').pop()?.toLowerCase()) || '.other'
    const category = CATEGORY_MAP[ext] || 'Other'
    if (!structure.has(category)) structure.set(category, new Map())
    const catMap = structure.get(category)!
    if (!catMap.has(ext)) catMap.set(ext, [])
    catMap.get(ext)!.push(f)
  }

  return Array.from(structure.entries()).map(([category, extMap]) => {
    const baseColor = COLORS[category] || COLORS.Other
    const children = Array.from(extMap.entries()).map(([ext, files], i) => {
      const shade = i === 0 ? baseColor : lightenColor(baseColor, Math.min(0.4, 0.05 * i))
      const fileNodes = [...files].sort((a: FileEntry, b: FileEntry) => b.size - a.size).map((f: FileEntry) => ({
        name: f.path.split(/[\\/]/).pop() || f.path,
        value: getVal(f.size),
        realSize: f.size,
        fileData: f,
        itemStyle: { color: shade }
      }))
      return {
        name: ext,
        value: fileNodes.reduce((s: number, c: any) => s + c.value, 0),
        realSize: files.reduce((s: number, f: FileEntry) => s + f.size, 0),
        children: fileNodes,
        itemStyle: { color: shade }
      }
    })
    return {
      name: category,
      value: children.reduce((s: number, c: any) => s + c.value, 0),
      realSize: children.reduce((s: number, c: any) => s + c.realSize, 0),
      children,
      itemStyle: { color: baseColor }
    }
  }).sort((a: any, b: any) => b.realSize - a.realSize)
}

function finalizeTree(node: any): any {
  if (node.fileData) return node;
  const childArray = Array.from(node.children.values()).map(finalizeTree);
  return {
    ...node,
    children: childArray,
    value: childArray.reduce((s: number, c: any) => s + c.value, 0),
    realSize: childArray.reduce((s: number, c: any) => s + (c.realSize || 0), 0)
  };
}

function collapseFolderChains(node: any): any {
  if (!node.children || !Array.isArray(node.children) || node.children.length === 0) return node;
  node.children = node.children.map(collapseFolderChains);
  while (
    node.children.length === 1 &&
    node.children[0].children &&
    Array.isArray(node.children[0].children) &&
    node.children[0].children.length > 0
  ) {
    const only = node.children[0];
    node.name = `${node.name} / ${only.name}`;
    node.fullPath = only.fullPath || node.fullPath;
    node.children = only.children;
    node.realSize = only.realSize;
    node.value = only.value;
  }
  return node;
}

function insertFileIntoTree(
  rootNode: any,
  f: FileEntry,
  startFolderName: string,
  stripPath: string,
  getVal: (s: number) => number
) {
  const fullPath = normalizePath(f.path);
  let relative = fullPath;
  if (stripPath && fullPath.startsWith(stripPath)) {
    relative = fullPath.slice(stripPath.length).replace(/^\/+/, '');
  }

  const parts = relative.split('/').filter(Boolean);
  let current = rootNode;
  const startIdx = parts[0] === startFolderName ? 1 : 0;

  for (let i = startIdx; i < parts.length; i++) {
    const part = parts[i];
    const isFile = (i === parts.length - 1);
    if (isFile) {
      const ext = ('.' + part.split('.').pop()?.toLowerCase()) || '.other';
      const category = CATEGORY_MAP[ext] || 'Other';
      current.children.set(part, {
        name: part,
        value: getVal(f.size),
        realSize: f.size,
        fileData: f,
        itemStyle: { color: COLORS[category] || COLORS.Other }
      });
    } else {
      if (!current.children.has(part)) {
        current.children.set(part, {
          name: part,
          children: new Map(),
          fullPath: current.fullPath + '/' + part,
          realSize: 0,
          value: 0,
          itemStyle: { color: '#1e1a3a' }
        });
      }
      current = current.children.get(part);
    }
  }
}

export function buildFolderTree(flatFiles: FileEntry[], getVal: (s: number) => number) {
  const normalizedPaths = flatFiles.map(f => normalizePath(f.path));
  const commonPrefix = findCommonPrefix(normalizedPaths);
  const prefixParts = commonPrefix.split('/').filter(Boolean);
  const startFolderName = prefixParts.at(-1) || 'Root';
  const stripPath = prefixParts.slice(0, -1).join('/');

  const rootNode: any = {
    name: startFolderName,
    fullPath: commonPrefix,
    children: new Map(),
    realSize: 0,
    value: 0,
    itemStyle: { color: '#1e1a3a' }
  };

  for (const f of flatFiles) {
    insertFileIntoTree(rootNode, f, startFolderName, stripPath, getVal);
  }

  const finalizedTree = finalizeTree(rootNode);
  return collapseFolderChains(finalizedTree);
}
