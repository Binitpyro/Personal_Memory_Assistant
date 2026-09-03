/**
 * KeyboardNavigation.ts
 *
 * Cursor movement over the NavigationController tree — the "Outliner" half of
 * the keymap.
 *
 * **The cursor is not `NavigationController.focusIndex`.** That field means
 * *the node you have drilled into* — the root of the visible set, the thing
 * breadcrumbs track. The cursor is *the node under selection*, which moves
 * every time an arrow key is pressed and usually does not change what is
 * drawn at all. Unreal draws exactly this distinction between the viewport's
 * pivot and the World Outliner's selection, and collapsing the two here would
 * mean every arrow keypress re-rooted the scene.
 *
 * Deliberately pure and React-free: every edge case that actually bites —
 * root has no parent, a collapsed folder, a leaf file, an empty folder, both
 * ends of a sibling list — is reachable from a hand-built controller in a
 * test, with no GPU and no canvas.
 *
 * Expansion state is NOT duplicated here. `expandOrDescend` / `collapseOrAscend`
 * call the controller's own `expandNode` / `collapseNode`, so there is one
 * owner of `expandedNodes`.
 */

import { FLAG_FOLDER, NO_PARENT, type NavigationController } from './NavigationController';

/** What a cursor move produced. `changed` is false when the move was a no-op. */
export interface CursorMove {
    readonly index: number;
    readonly changed: boolean;
    /**
     * Set when the move was refused for a reason worth saying out loud —
     * announced through the live region rather than failing silently, which is
     * the difference between "nothing happened" and "nothing CAN happen here".
     */
    readonly announce?: string;
}

const stay = (index: number, announce?: string): CursorMove => ({ index, changed: false, announce });
const move = (index: number): CursorMove => ({ index, changed: true });

export function isFolder(nav: NavigationController, index: number): boolean {
    const node = nav.getGraphNode(index);
    return ((node?.flags ?? 0) & FLAG_FOLDER) === FLAG_FOLDER;
}

/**
 * Siblings of a node, in source order. The root is its own only sibling — it
 * has no parent to enumerate from.
 */
function siblingsOf(nav: NavigationController, index: number): number[] {
    const node = nav.getGraphNode(index);
    if (!node) return [];
    if (node.parentIndex === NO_PARENT) return [index];
    const parent = nav.getGraphNode(node.parentIndex);
    return parent ? parent.children : [index];
}

/**
 * Move within the sibling list. Clamped, not wrapped — Outliners do not wrap,
 * and wrapping in a spatial view is disorienting because the camera jumps from
 * one end of a cluster to the other.
 */
export function siblingStep(nav: NavigationController, index: number, delta: number): CursorMove {
    const sibs = siblingsOf(nav, index);
    const at = sibs.indexOf(index);
    if (at === -1) return stay(index);
    const next = at + delta;
    if (next < 0 || next >= sibs.length) {
        return stay(index, delta < 0 ? 'First item' : 'Last item');
    }
    return move(sibs[next]);
}

export const nextSibling = (nav: NavigationController, index: number) => siblingStep(nav, index, 1);
export const previousSibling = (nav: NavigationController, index: number) => siblingStep(nav, index, -1);

/**
 * Right arrow, two-step: a collapsed folder expands in place; an already
 * expanded folder moves the cursor to its first child. A file does nothing.
 * This is the standard tree behaviour in both editors, and in every OS file
 * manager.
 */
export function expandOrDescend(nav: NavigationController, index: number): CursorMove {
    if (!isFolder(nav, index)) return stay(index);
    const node = nav.getGraphNode(index);
    if (!node) return stay(index);

    if (!nav.expandedNodes.has(index)) {
        if (node.children.length === 0) return stay(index, 'Empty folder');
        nav.expandNode(index);
        return { index, changed: true };
    }
    if (node.children.length === 0) return stay(index, 'Empty folder');
    return move(node.children[0]);
}

/**
 * Left arrow, the mirror: an expanded folder collapses in place; anything else
 * moves to the parent. At the root there is nowhere to go.
 */
export function collapseOrAscend(nav: NavigationController, index: number): CursorMove {
    const node = nav.getGraphNode(index);
    if (!node) return stay(index);

    if (isFolder(nav, index) && nav.expandedNodes.has(index)) {
        nav.collapseNode(index);
        return { index, changed: true };
    }
    if (node.parentIndex === NO_PARENT) return stay(index, 'At root');
    return move(node.parentIndex);
}

/**
 * The first thing the cursor should land on when the view gains focus: the
 * first child of whatever is currently drilled into, falling back to the root
 * itself for an empty corpus.
 */
export function initialCursor(nav: NavigationController): number {
    const focus = nav.getFocusIndex();
    const node = nav.getGraphNode(focus);
    if (node && node.children.length > 0) return node.children[0];
    return focus >= 0 ? focus : nav.getRootIndex();
}

/**
 * Depth for `aria-level`, which is 1-based. `NavNode.depth` is 0-based from
 * the root, computed once during `loadData`.
 */
export function ariaLevel(nav: NavigationController, index: number): number {
    return (nav.getGraphNode(index)?.depth ?? 0) + 1;
}

/**
 * What a screen reader should hear for a node. Names live in a side-channel
 * the controller resolves for breadcrumbs, so the caller passes whatever it
 * has — the metadata map in the 3D view, or the tree node's own name.
 */
export function describeNode(
    nav: NavigationController,
    index: number,
    name: string,
): string {
    const node = nav.getGraphNode(index);
    if (!node) return name;
    const folder = isFolder(nav, index);
    const parts = [name, folder ? 'folder' : 'file'];
    if (folder) {
        parts.push(`${node.children.length} ${node.children.length === 1 ? 'item' : 'items'}`);
        parts.push(nav.expandedNodes.has(index) ? 'expanded' : 'collapsed');
    }
    parts.push(`level ${ariaLevel(nav, index)}`);
    return parts.join(', ');
}
