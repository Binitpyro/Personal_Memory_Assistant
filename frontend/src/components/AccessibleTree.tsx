/**
 * AccessibleTree.tsx
 *
 * The assistive-technology surface for the two canvas views.
 *
 * A canvas is opaque to a screen reader no matter how many key handlers it
 * carries: there is nothing in the accessibility tree to read. Key bindings
 * make a view *operable*; this makes it *perceivable*, which is the other half
 * and the one that cannot be faked with a live region alone — a live region
 * announces one node at a time and gives no sense of shape.
 *
 * Both views feed the same adapter shape. They have genuinely different
 * internals — the 3D view a flat `NavNode[]` linked by index, the treemap a
 * nested `{ name, children, fullPath }` from `utils/treeBuilder` — so a thin
 * adapter is the honest join, not a shared model imposed on both.
 *
 * Visually hidden until focused, then shown. A permanently hidden focusable
 * control is its own accessibility defect: a sighted keyboard user tabs into
 * it and the focus ring vanishes. This is the skip-link pattern.
 */

import { useCallback, type KeyboardEvent } from 'react';

export interface A11yNode {
    readonly id: string;
    readonly name: string;
    readonly isFolder: boolean;
    /** Undefined for files. Folders report it so `aria-expanded` is accurate. */
    readonly expanded?: boolean;
    /** Present only when expanded — the DOM mirrors what is actually open. */
    readonly children?: readonly A11yNode[];
    /** Free text appended to the label, e.g. "12 items · 4.2 MB". */
    readonly detail?: string;
}

export interface AccessibleTreeProps {
    readonly label: string;
    readonly nodes: readonly A11yNode[];
    readonly selectedId: string | null;
    readonly onSelect: (id: string) => void;
    readonly onActivate: (id: string) => void;
    /**
     * Keys the tree does not own — the camera half of the keymap — so the same
     * bindings work whichever of the two tab stops holds focus.
     */
    readonly onUnhandledKey?: (e: KeyboardEvent<HTMLElement>) => void;
}

function Item({
    node,
    level,
    selectedId,
    onSelect,
    onActivate,
}: Readonly<{
    node: A11yNode;
    level: number;
    selectedId: string | null;
    onSelect: (id: string) => void;
    onActivate: (id: string) => void;
}>) {
    const selected = node.id === selectedId;
    // An explicit name is load-bearing, not decoration. A treeitem's accessible
    // name is otherwise computed from its whole subtree, so an expanded folder
    // announces as itself PLUS every descendant label concatenated — the
    // deeper the branch, the longer the sentence. Naming the item stops the
    // descendant text leaking upward.
    const label = `${node.name}${node.detail ? ` — ${node.detail}` : ''}`;
    return (
        <li
            role="treeitem"
            aria-label={label}
            aria-level={level}
            aria-selected={selected}
            aria-expanded={node.isFolder ? !!node.expanded : undefined}
            // Roving tabIndex: the whole tree is ONE tab stop, and arrow keys
            // move within it. A tabIndex on every item would make Tab walk
            // thousands of files.
            tabIndex={selected ? 0 : -1}
            className={selected ? 'text-primary' : undefined}
            onClick={e => {
                e.stopPropagation();
                onSelect(node.id);
            }}
            onDoubleClick={e => {
                e.stopPropagation();
                onActivate(node.id);
            }}
        >
            <span aria-hidden className="font-mono text-[11px]">
                {node.isFolder ? '▸ ' : '· '}
                {label}
            </span>
            {node.children && node.children.length > 0 && (
                <ul role="group" className="pl-3 list-none m-0">
                    {node.children.map(child => (
                        <Item
                            key={child.id}
                            node={child}
                            level={level + 1}
                            selectedId={selectedId}
                            onSelect={onSelect}
                            onActivate={onActivate}
                        />
                    ))}
                </ul>
            )}
        </li>
    );
}

export function AccessibleTree({
    label,
    nodes,
    selectedId,
    onSelect,
    onActivate,
    onUnhandledKey,
}: AccessibleTreeProps) {
    // Arrow handling lives with the view, which owns the NavigationController;
    // the tree forwards everything so there is exactly one keymap
    // implementation rather than two that can disagree.
    const onKeyDown = useCallback(
        (e: KeyboardEvent<HTMLElement>) => onUnhandledKey?.(e),
        [onUnhandledKey],
    );

    return (
        <div className="sr-only focus-within:not-sr-only focus-within:absolute focus-within:z-30 focus-within:top-2 focus-within:left-2 focus-within:max-h-[70%] focus-within:w-72 focus-within:overflow-auto focus-within:bg-surface focus-within:border focus-within:border-edge focus-within:rounded-md focus-within:p-3 focus-within:shadow-xl">
            <ul
                role="tree"
                aria-label={label}
                onKeyDown={onKeyDown}
                className="list-none m-0 p-0"
            >
                {nodes.map(n => (
                    <Item
                        key={n.id}
                        node={n}
                        level={1}
                        selectedId={selectedId}
                        onSelect={onSelect}
                        onActivate={onActivate}
                    />
                ))}
            </ul>
        </div>
    );
}
