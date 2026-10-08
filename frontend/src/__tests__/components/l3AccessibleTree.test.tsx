/**
 * A8-20: the tree's only tab stop was the item matching selectedId, which is
 * null at start and names the drilled-into folder (not an item) after Enter,
 * so the tree had no tab stop; and moving the cursor never moved DOM focus.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AccessibleTree, type A11yNode } from '../../components/AccessibleTree';

const nodes: A11yNode[] = [
    { id: '1', name: 'a.md', isFolder: false },
    { id: '2', name: 'b.md', isFolder: false },
];
const other: A11yNode[] = [
    { id: '8', name: 'c.md', isFolder: false },
    { id: '9', name: 'd.md', isFolder: false },
];

const tree = (ns: A11yNode[], selectedId: string | null) => (
    <AccessibleTree label="t" nodes={ns} selectedId={selectedId} onSelect={() => {}} onActivate={() => {}} />
);
const stops = () => screen.getAllByRole('treeitem').filter(e => e.tabIndex === 0);

describe('AccessibleTree roving tabindex', () => {
    it('has one tab stop when nothing is selected', () => {
        render(tree(nodes, null));
        expect(stops().map(e => e.dataset.nodeId)).toEqual(['1']);
    });

    it('has one tab stop when selectedId is not a rendered item', () => {
        render(tree(nodes, '0'));
        expect(stops().map(e => e.dataset.nodeId)).toEqual(['1']);
    });

    it('moves DOM focus with the selection while focus is in the tree', () => {
        const { rerender } = render(tree(nodes, '1'));
        screen.getAllByRole('treeitem')[0].focus();
        rerender(tree(nodes, '2'));
        expect(document.activeElement).toBe(screen.getAllByRole('treeitem')[1]);
    });

    it('refocuses the tab stop when the focused item unmounts', () => {
        const { rerender } = render(tree(nodes, '1'));
        screen.getAllByRole('treeitem')[0].focus();
        rerender(tree(other, '0'));
        expect(document.activeElement).toBe(screen.getAllByRole('treeitem')[0]);
    });

    it('does not take focus when it was never inside the tree', () => {
        const { rerender } = render(tree(nodes, '1'));
        rerender(tree(nodes, '2'));
        expect(document.activeElement).toBe(document.body);
    });
});
