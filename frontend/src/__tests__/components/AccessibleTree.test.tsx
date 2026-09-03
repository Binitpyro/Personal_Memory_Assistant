/**
 * The AT surface for the canvas views.
 *
 * A canvas cannot be read by a screen reader however many key handlers it
 * carries, so this tree is what actually makes the corpus perceivable. These
 * tests hold the ARIA contract that makes it a tree rather than a list of
 * divs, and the two properties most likely to regress quietly: that only
 * expanded nodes are in the DOM, and that the whole tree is one tab stop.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { AccessibleTree, type A11yNode } from '../../components/AccessibleTree';

const NODES: A11yNode[] = [
    {
        id: 'docs',
        name: 'Documents',
        isFolder: true,
        expanded: true,
        detail: '2 items',
        children: [
            { id: 'a', name: 'a.txt', isFolder: false, detail: '1 KB' },
            {
                id: 'nested',
                name: 'Nested',
                isFolder: true,
                expanded: false,
                // Collapsed: children deliberately absent.
                children: undefined,
            },
        ],
    },
    { id: 'code', name: 'Code', isFolder: true, expanded: false },
];

function renderTree(selectedId: string | null = 'docs', props: Partial<React.ComponentProps<typeof AccessibleTree>> = {}) {
    const onSelect = vi.fn();
    const onActivate = vi.fn();
    render(
        <AccessibleTree
            label="Corpus hierarchy"
            nodes={NODES}
            selectedId={selectedId}
            onSelect={onSelect}
            onActivate={onActivate}
            {...props}
        />,
    );
    return { onSelect, onActivate };
}

describe('AccessibleTree ARIA contract', () => {
    it('exposes a named tree', () => {
        renderTree();
        expect(screen.getByRole('tree', { name: 'Corpus hierarchy' })).toBeDefined();
    });

    it('reports expansion state on folders only', () => {
        renderTree();
        expect(screen.getByRole('treeitem', { name: /Documents/ }).getAttribute('aria-expanded')).toBe('true');
        expect(screen.getByRole('treeitem', { name: /Code/ }).getAttribute('aria-expanded')).toBe('false');
        // A file has no expansion state to report.
        expect(screen.getByRole('treeitem', { name: /a\.txt/ }).getAttribute('aria-expanded')).toBeNull();
    });

    it('reports depth as 1-based aria-level', () => {
        renderTree();
        expect(screen.getByRole('treeitem', { name: /Documents/ }).getAttribute('aria-level')).toBe('1');
        expect(screen.getByRole('treeitem', { name: /a\.txt/ }).getAttribute('aria-level')).toBe('2');
    });

    it('groups children under role=group', () => {
        renderTree();
        const docs = screen.getByRole('treeitem', { name: /Documents/ });
        expect(within(docs).getByRole('group')).toBeDefined();
    });

    it('marks the selected node and nothing else', () => {
        renderTree('a');
        expect(screen.getByRole('treeitem', { name: /a\.txt/ }).getAttribute('aria-selected')).toBe('true');
        expect(screen.getByRole('treeitem', { name: /Code/ }).getAttribute('aria-selected')).toBe('false');
    });
});

describe('AccessibleTree behaviour', () => {
    it('renders only expanded branches', () => {
        renderTree();
        // `Documents` is expanded, so its children are present...
        expect(screen.queryByRole('treeitem', { name: /a\.txt/ })).not.toBeNull();
        // ...but `Nested` is collapsed and contributes nothing below itself.
        const nested = screen.getByRole('treeitem', { name: /Nested/ });
        expect(within(nested).queryByRole('group')).toBeNull();
    });

    it('is a single tab stop — roving tabIndex', () => {
        renderTree('docs');
        const focusable = screen
            .getAllByRole('treeitem')
            .filter(el => el.getAttribute('tabindex') === '0');
        expect(focusable).toHaveLength(1);
        expect(focusable[0].getAttribute('aria-selected')).toBe('true');
    });

    it('selects on click and activates on double click', () => {
        const { onSelect, onActivate } = renderTree();
        const code = screen.getByRole('treeitem', { name: /Code/ });
        fireEvent.click(code);
        expect(onSelect).toHaveBeenCalledWith('code');
        fireEvent.doubleClick(code);
        expect(onActivate).toHaveBeenCalledWith('code');
    });

    it('forwards keys it does not own, so the camera keymap still works', () => {
        const onUnhandledKey = vi.fn();
        renderTree('docs', { onUnhandledKey });
        fireEvent.keyDown(screen.getByRole('tree'), { key: 'w' });
        expect(onUnhandledKey).toHaveBeenCalled();
    });

    it('renders nothing but the empty tree for an empty corpus', () => {
        render(
            <AccessibleTree
                label="Corpus hierarchy"
                nodes={[]}
                selectedId={null}
                onSelect={vi.fn()}
                onActivate={vi.fn()}
            />,
        );
        expect(screen.getByRole('tree')).toBeDefined();
        expect(screen.queryAllByRole('treeitem')).toHaveLength(0);
    });
});
