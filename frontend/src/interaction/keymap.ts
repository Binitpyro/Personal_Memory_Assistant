/**
 * keymap.ts
 *
 * The single source of truth for the corpus views' key bindings.
 *
 * Both the key handlers and the shortcut overlay read this table, so the
 * reference card cannot drift from what the handlers actually do — the usual
 * failure mode for a help screen maintained by hand.
 *
 * The bindings follow the Unreal and Unity editor conventions rather than
 * inventing a scheme. Those editors settled this a long time ago, and the
 * settlement is that a 3D tool has TWO keymaps: a viewport (camera verbs) and
 * an outliner (hierarchy verbs). `group` is that split, and it is also what
 * lets the 2D treemap show only the half that applies to it — the treemap has
 * no camera, so offering `F` there would be a dead key on a help screen.
 */

export type KeyGroup = 'viewport' | 'outliner';

export interface Binding {
    /** Display form, e.g. ['W','A','S','D'] or ['Shift','↑']. */
    readonly keys: readonly string[];
    readonly label: string;
    readonly group: KeyGroup;
    /**
     * Where the binding comes from. Shown in the overlay so the convention is
     * visible rather than folklore — and so a future change has to argue with
     * the precedent instead of just re-binding.
     */
    readonly reference?: string;
}

export const BINDINGS: readonly Binding[] = [
    // ── Viewport: camera ────────────────────────────────────────────────
    { group: 'viewport', keys: ['W', 'A', 'S', 'D'], label: 'Fly forward, left, back, right', reference: 'Unreal / Unity flythrough' },
    { group: 'viewport', keys: ['Q', 'E'], label: 'Fly down / up', reference: 'Unreal / Unity' },
    { group: 'viewport', keys: ['Shift'], label: 'Hold to move faster', reference: 'Unreal / Unity' },
    { group: 'viewport', keys: ['Shift', '←↑↓→'], label: 'Orbit around the pivot' },
    { group: 'viewport', keys: ['+', '−'], label: 'Dolly in / out', reference: 'scroll wheel' },
    { group: 'viewport', keys: ['F'], label: 'Frame the selection', reference: 'Unreal / Unity' },
    { group: 'viewport', keys: ['Home'], label: 'Frame everything — back to root', reference: 'Unreal' },
    { group: 'viewport', keys: ['Esc'], label: 'Clear the selection' },

    // ── Outliner: hierarchy ─────────────────────────────────────────────
    { group: 'outliner', keys: ['↑', '↓'], label: 'Previous / next sibling', reference: 'World Outliner, Hierarchy' },
    { group: 'outliner', keys: ['→'], label: 'Expand folder, else first child', reference: 'World Outliner, Hierarchy' },
    { group: 'outliner', keys: ['←'], label: 'Collapse folder, else parent', reference: 'World Outliner, Hierarchy' },
    { group: 'outliner', keys: ['Enter'], label: 'Drill into the selection' },
    { group: 'outliner', keys: ['Backspace'], label: 'Up one level' },
    { group: 'outliner', keys: ['?', 'F1'], label: 'This reference' },
];

export const GROUP_TITLE: Record<KeyGroup, string> = {
    viewport: 'Viewport',
    outliner: 'Hierarchy',
};

/** The camera half does not apply to the 2D treemap. */
export function bindingsFor(groups: readonly KeyGroup[]): Binding[] {
    return BINDINGS.filter(b => groups.includes(b.group));
}

/**
 * Keys held for continuous camera movement, mapped to a unit fly vector.
 *
 * Read per frame rather than per `keydown`: key auto-repeat is an OS setting,
 * so integrating repeats would make fly speed depend on the user's control
 * panel.
 */
export const FLY_KEYS: Record<string, { forward: number; right: number; up: number }> = {
    w: { forward: 1, right: 0, up: 0 },
    s: { forward: -1, right: 0, up: 0 },
    a: { forward: 0, right: -1, up: 0 },
    d: { forward: 0, right: 1, up: 0 },
    e: { forward: 0, right: 0, up: 1 },
    q: { forward: 0, right: 0, up: -1 },
};

/** Shift multiplier, matching the "hold to go faster" both editors use. */
export const FLY_BOOST = 4;
