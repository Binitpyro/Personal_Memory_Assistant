/**
 * Cursor movement over a real NavigationController.
 *
 * The controller is fed an actual 32-byte-per-node buffer rather than mocked,
 * because the thing most likely to break here is the parent/child linking and
 * the NO_PARENT sentinel — which a mock would paper over. Building the buffer
 * is ~15 lines and keeps the test honest about the wire format.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import {
    NavigationController,
    NODE_STRIDE,
    NODE_OFF_PARENT_IDX,
    NODE_OFF_FLAGS,
    NODE_OFF_RADIUS,
    NO_PARENT,
    FLAG_FOLDER,
} from '../../interaction/NavigationController';
import {
    nextSibling,
    previousSibling,
    expandOrDescend,
    collapseOrAscend,
    initialCursor,
    ariaLevel,
    describeNode,
    isFolder,
} from '../../interaction/KeyboardNavigation';

/**
 * The fixture tree:
 *
 *   0 root (folder)
 *   ├── 1 docs (folder)
 *   │   ├── 3 a.txt (file)
 *   │   └── 4 b.txt (file)
 *   ├── 2 code (folder)
 *   │   └── 5 main.rs (file)
 *   └── 6 empty (folder, no children)
 */
const TREE: Array<{ parent: number; folder: boolean }> = [
    { parent: NO_PARENT, folder: true },  // 0 root
    { parent: 0, folder: true },          // 1 docs
    { parent: 0, folder: true },          // 2 code
    { parent: 1, folder: false },         // 3 a.txt
    { parent: 1, folder: false },         // 4 b.txt
    { parent: 2, folder: false },         // 5 main.rs
    { parent: 0, folder: true },          // 6 empty
];

function buildBuffer(): ArrayBuffer {
    const buf = new ArrayBuffer(TREE.length * NODE_STRIDE);
    const dv = new DataView(buf);
    TREE.forEach((n, i) => {
        const off = i * NODE_STRIDE;
        dv.setFloat32(off + NODE_OFF_RADIUS, 10, true);
        dv.setUint32(off + NODE_OFF_PARENT_IDX, n.parent, true);
        dv.setUint32(off + NODE_OFF_FLAGS, n.folder ? FLAG_FOLDER : 0, true);
    });
    return buf;
}

let nav: NavigationController;
beforeEach(() => {
    nav = new NavigationController();
    nav.loadData(buildBuffer());
});

describe('tree fixture', () => {
    it('links children through the wire format', () => {
        expect(nav.getGraphNode(0)!.children).toEqual([1, 2, 6]);
        expect(nav.getGraphNode(1)!.children).toEqual([3, 4]);
        expect(nav.getGraphNode(6)!.children).toEqual([]);
        expect(nav.getRootIndex()).toBe(0);
    });

    it('reads the folder bit', () => {
        expect(isFolder(nav, 1)).toBe(true);
        expect(isFolder(nav, 3)).toBe(false);
    });
});

describe('sibling movement', () => {
    it('steps forward and back within one parent', () => {
        expect(nextSibling(nav, 1).index).toBe(2);
        expect(previousSibling(nav, 2).index).toBe(1);
    });

    it('clamps at both ends rather than wrapping', () => {
        const atFirst = previousSibling(nav, 1);
        expect(atFirst.index).toBe(1);
        expect(atFirst.changed).toBe(false);
        expect(atFirst.announce).toBe('First item');

        const atLast = nextSibling(nav, 6);
        expect(atLast.index).toBe(6);
        expect(atLast.changed).toBe(false);
        expect(atLast.announce).toBe('Last item');
    });

    it('treats the root as its own only sibling', () => {
        expect(nextSibling(nav, 0).changed).toBe(false);
        expect(previousSibling(nav, 0).changed).toBe(false);
    });

    it('does not step across parents', () => {
        // 4 is the last child of `docs`; `code`'s child must not be reachable.
        expect(nextSibling(nav, 4).index).toBe(4);
    });
});

describe('expandOrDescend (right arrow)', () => {
    it('expands a collapsed folder in place, without moving', () => {
        nav.collapseNode(1);
        const r = expandOrDescend(nav, 1);
        expect(r.index).toBe(1);
        expect(nav.expandedNodes.has(1)).toBe(true);
    });

    it('descends to the first child once already expanded', () => {
        nav.expandNode(1);
        expect(expandOrDescend(nav, 1).index).toBe(3);
    });

    it('does nothing on a file', () => {
        const r = expandOrDescend(nav, 3);
        expect(r.index).toBe(3);
        expect(r.changed).toBe(false);
    });

    it('says so for an empty folder instead of silently failing', () => {
        const r = expandOrDescend(nav, 6);
        expect(r.changed).toBe(false);
        expect(r.announce).toBe('Empty folder');
    });
});

describe('collapseOrAscend (left arrow)', () => {
    it('collapses an expanded folder in place', () => {
        nav.expandNode(1);
        const r = collapseOrAscend(nav, 1);
        expect(r.index).toBe(1);
        expect(nav.expandedNodes.has(1)).toBe(false);
    });

    it('moves to the parent from a collapsed folder', () => {
        nav.collapseNode(1);
        expect(collapseOrAscend(nav, 1).index).toBe(0);
    });

    it('moves to the parent from a file', () => {
        expect(collapseOrAscend(nav, 3).index).toBe(1);
    });

    it('refuses at the root, which has no parent', () => {
        nav.collapseNode(0);
        const r = collapseOrAscend(nav, 0);
        expect(r.index).toBe(0);
        expect(r.changed).toBe(false);
        expect(r.announce).toBe('At root');
    });
});

describe('cursor seeding and description', () => {
    it('starts on the first child of whatever is drilled into', () => {
        expect(initialCursor(nav)).toBe(1);
    });

    it('reports aria-level 1-based from the root', () => {
        expect(ariaLevel(nav, 0)).toBe(1);
        expect(ariaLevel(nav, 1)).toBe(2);
        expect(ariaLevel(nav, 3)).toBe(3);
    });

    it('describes a folder with its item count and expansion state', () => {
        nav.expandNode(1);
        expect(describeNode(nav, 1, 'docs')).toBe('docs, folder, 2 items, expanded, level 2');
        nav.collapseNode(1);
        expect(describeNode(nav, 1, 'docs')).toBe('docs, folder, 2 items, collapsed, level 2');
    });

    it('singularises a one-item folder', () => {
        expect(describeNode(nav, 2, 'code')).toContain('1 item,');
    });

    it('describes a file without expansion state', () => {
        expect(describeNode(nav, 3, 'a.txt')).toBe('a.txt, file, level 3');
    });
});
