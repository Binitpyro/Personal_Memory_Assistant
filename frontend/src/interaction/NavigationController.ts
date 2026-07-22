/**
 * NavigationController.ts
 *
 * LOD + navigation state for the 3D visualizer.
 *
 * Responsibilities:
 *   1. Parse the raw 32-byte-per-node buffer that both the WebGPU and WebGL2
 *      renderers consume, and build a lightweight tree of parent/children
 *      indices in memory. Positions/radii are NOT copied out — renderers
 *      re-read them directly from the source buffer per frame.
 *   2. Track which folders are currently expanded and produce the visible
 *      node set (crystals for collapsed folders, bubbles for files).
 *   3. Produce that set in **hierarchy-aware pre-order** — parent before
 *      children — so the renderer can iterate it directly for correct
 *      back-to-front translucency across nested bubbles without an extra
 *      per-frame distance sort. (Strictly-nested spheres do not need a
 *      general depth sort; a valid tree pre-order is enough.)
 *   4. Compact the visible set into a small VBO-ready buffer so the renderer
 *      can issue ONE drawIndexed call per mesh type instead of one per node.
 *   5. Resolve human-readable names for breadcrumbs when the caller supplies
 *      a path table separately (from getFileTree()); we can't ship names
 *      through the 32-byte struct.
 *
 * Non-goals:
 *   - No positions/radii math. Layout is done upstream (Rust Barnes-Hut).
 *   - No rendering. Emits data buffers and index arrays only.
 */

/** Byte offsets inside the 32-byte Node struct (matches Rust repr(C, align(32))). */
export const NODE_STRIDE = 32;
export const NODE_OFF_POS_X       = 0;
export const NODE_OFF_POS_Y       = 4;
export const NODE_OFF_POS_Z       = 8;
export const NODE_OFF_RADIUS      = 12;
export const NODE_OFF_PARENT_IDX  = 16;
export const NODE_OFF_FLAGS       = 20;
export const NODE_OFF_TYPE_HASH   = 24;
export const NODE_OFF_PAD         = 28;

/** Sentinel written by the Rust/Python backend when a node has no parent (root). */
export const NO_PARENT = 0xFFFFFFFF;

/** flags bit 0: 1 = folder, 0 = file. */
export const FLAG_FOLDER = 1;

export interface NavNode {
    /** Position in the source 32-byte buffer (also the instance index used by picking). */
    index: number;
    parentIndex: number;
    flags: number;
    typeHash: number;
    /** Child indices, in source order. */
    children: number[];
    /** Depth from root; root = 0. Computed after tree assembly. */
    depth: number;
}

export interface Breadcrumb {
    index: number;
    name: string;
}

/**
 * Compact per-frame output for the renderer.
 *
 * The `data` field is a slice of instance rows in the exact 32-byte layout
 * that the vertex-buffer layout on the render pipeline expects, so the
 * renderer can queue.writeBuffer() it into a single instance VBO and issue
 * one drawIndexed call.
 *
 * Order is strict pre-order (parent index appears before any of its
 * descendants) so bubble alpha blending is correct without a separate sort.
 */
export interface VisibleSet {
    /** Compacted 32-byte-per-node buffer of visible crystals (collapsed folders). */
    crystalData: Uint8Array;
    crystalCount: number;
    /** Original source indices for crystals — used to reconstruct name / metadata. */
    crystalIndices: Uint32Array;

    /** Compacted 32-byte-per-node buffer of visible bubbles (files). Pre-ordered. */
    bubbleData: Uint8Array;
    bubbleCount: number;
    bubbleIndices: Uint32Array;
}

/**
 * Optional path table produced from getFileTree() so breadcrumbs can show
 * real folder names. Keyed by node.index into the visualizer buffer.
 *
 * The wire format doesn't carry names, so this is a separate side-channel
 * populated by the React component before it hands the buffer to the
 * renderer. If not supplied, breadcrumbs fall back to `#index`.
 */
export type NameTable = ReadonlyMap<number, string>;

export class NavigationController {
    public nodes: NavNode[] = [];
    public expandedNodes = new Set<number>();
    public breadcrumbs: Breadcrumb[] = [];

    /** Current "focus" — the last breadcrumb. Traversal starts from here. */
    private focusIndex: number = -1;
    private rootIndex: number = -1;

    /** Source buffer reference. NOT copied; we memcpy 32-byte slices out of it on demand. */
    private srcBuffer: ArrayBuffer | null = null;
    private srcBytes: Uint8Array | null = null;

    /** Optional name table, indexed by node.index. Populated by loadNames(). */
    private names: NameTable | null = null;

    /**
     * Ingest a fresh visualizer buffer and rebuild the in-memory tree.
     * O(n) — one pass to read the flat structs, one pass to link children.
     */
    public loadData(buffer: ArrayBuffer): void {
        this.srcBuffer = buffer;
        this.srcBytes = new Uint8Array(buffer);
        const numNodes = Math.floor(buffer.byteLength / NODE_STRIDE);
        const dataView = new DataView(buffer);

        this.nodes = new Array(numNodes);
        this.expandedNodes.clear();
        this.breadcrumbs = [];
        this.rootIndex = -1;

        for (let i = 0; i < numNodes; i++) {
            const offset = i * NODE_STRIDE;
            const parentIndex = dataView.getUint32(offset + NODE_OFF_PARENT_IDX, true);
            const flags       = dataView.getUint32(offset + NODE_OFF_FLAGS,       true);
            const typeHash    = dataView.getUint32(offset + NODE_OFF_TYPE_HASH,   true);

            this.nodes[i] = {
                index: i,
                parentIndex,
                flags,
                typeHash,
                children: [],
                depth: 0, // filled below
            };

            if (parentIndex === NO_PARENT) {
                // Take the FIRST root we see. Rust BFS ordering guarantees a single root at index 0.
                if (this.rootIndex === -1) this.rootIndex = i;
            }
        }

        // Wire up children in a second pass (needs all parents constructed first).
        for (let i = 0; i < numNodes; i++) {
            const pIdx = this.nodes[i].parentIndex;
            if (pIdx !== NO_PARENT && pIdx < numNodes) {
                this.nodes[pIdx].children.push(i);
            }
        }

        // Compute depth via BFS from root. Depth is used by shaders (via VBO stream) later
        // and could gate LOD thresholds; keeping it as a field for cheap read.
        // We also apply two safeguards during this pass:
        // 1. Anomaly Coercion: Any node with children must be marked as a folder.
        // 2. Radius Clamping: Child radius must be <= parent.radius * 0.42 to prevent facet clipping.
        if (this.rootIndex !== -1) {
            if (this.nodes[this.rootIndex].children.length > 0 && (this.nodes[this.rootIndex].flags & FLAG_FOLDER) === 0) {
                this.nodes[this.rootIndex].flags |= FLAG_FOLDER;
                dataView.setUint32(this.rootIndex * NODE_STRIDE + NODE_OFF_FLAGS, this.nodes[this.rootIndex].flags, true);
            }

            const queue: number[] = [this.rootIndex];
            this.nodes[this.rootIndex].depth = 0;
            while (queue.length > 0) {
                const cur = queue.shift()!;
                const parentRadius = dataView.getFloat32(cur * NODE_STRIDE + NODE_OFF_RADIUS, true);
                const maxChildRadius = parentRadius * 0.42;

                for (const c of this.nodes[cur].children) {
                    this.nodes[c].depth = this.nodes[cur].depth + 1;
                    
                    if (this.nodes[c].children.length > 0 && (this.nodes[c].flags & FLAG_FOLDER) === 0) {
                        this.nodes[c].flags |= FLAG_FOLDER;
                        dataView.setUint32(c * NODE_STRIDE + NODE_OFF_FLAGS, this.nodes[c].flags, true);
                    }

                    const childRadius = dataView.getFloat32(c * NODE_STRIDE + NODE_OFF_RADIUS, true);
                    if (childRadius > maxChildRadius) {
                        dataView.setFloat32(c * NODE_STRIDE + NODE_OFF_RADIUS, maxChildRadius, true);
                    }
                    
                    queue.push(c);
                }
            }

            // Initial UX: root is focused, root is expanded, breadcrumb shows "Root".
            this.focusIndex = this.rootIndex;
            this.expandedNodes.add(this.rootIndex);
            this.breadcrumbs = [{ index: this.rootIndex, name: this.resolveName(this.rootIndex) }];
        }
    }

    /**
     * Attach a name table. Called by the React component after both the
     * getVisualizerStream and getFileTree responses have arrived. Safe to
     * call multiple times; last write wins.
     *
     * Matching is up to the caller. The simplest reliable approach:
     *   - the caller iterates its tree.folders result depth-first in the
     *     same order Rust's build_tree produces (path components sorted
     *     lexicographically per level) and pairs each name with its
     *     corresponding node.index.
     *   - If the caller can't guarantee identical ordering, it can match
     *     by typeHash (unique per folder path in Rust's implementation) —
     *     but that requires the Rust side to hash the full path, not just
     *     the extension. Verify before relying on it.
     *
     * Given the two paths above, I suggest the caller pass a Map<index, name>
     * built from a single BFS over tree.folders that mirrors Rust's traversal.
     * See WebGPUFallback.tsx for the concrete implementation.
     */
    public loadNames(names: NameTable): void {
        this.names = names;
        // Re-materialize breadcrumbs with real names, if we already have any.
        for (const bc of this.breadcrumbs) {
            bc.name = this.resolveName(bc.index);
        }
    }

    private resolveName(index: number): string {
        if (this.names) {
            const n = this.names.get(index);
            if (n) return n;
        }
        if (index === this.rootIndex) return 'Root';
        return `#${index}`;
    }

    /**
     * Expand a folder (show its children). Idempotent.
     * Silently ignores non-folder indices — the renderer may call this
     * from a pick result that landed on a bubble.
     */
    public expandNode(index: number): void {
        if (index < 0 || index >= this.nodes.length) return;
        if (this.nodes[index].flags !== FLAG_FOLDER) return;
        this.expandedNodes.add(index);
    }

    /**
     * Collapse a folder AND all of its descendants. Otherwise a stale
     * expanded-descendant would resurface next time the user re-expands
     * the parent, which is confusing.
     */
    public collapseNode(index: number): void {
        if (index < 0 || index >= this.nodes.length) return;
        const stack: number[] = [index];
        while (stack.length > 0) {
            const cur = stack.pop()!;
            this.expandedNodes.delete(cur);
            for (const c of this.nodes[cur].children) stack.push(c);
        }
    }

    /**
     * Drill into a folder — expand it and everything from root down to it,
     * update breadcrumbs, and set the traversal focus.
     *
     * If a file (bubble) is passed, we drill to its parent folder instead.
     */
    public navigateTo(index: number): void {
        if (index < 0 || index >= this.nodes.length) return;

        // If the target is a file, redirect to its containing folder.
        let target = index;
        if (this.nodes[target].flags !== FLAG_FOLDER) {
            const parent = this.nodes[target].parentIndex;
            if (parent === NO_PARENT) return;
            target = parent;
        }

        // Walk back to root, then reverse to get root-to-target order.
        const path: number[] = [];
        let curr = target;
        while (curr !== NO_PARENT) {
            path.push(curr);
            const p = this.nodes[curr]?.parentIndex;
            if (p === undefined) break;
            curr = p;
        }
        path.reverse();

        // Reset expansion to exactly the ancestor chain + the target itself.
        // Everything else collapses. This matches the "drill in / zoom out"
        // interaction model — one focused branch at a time.
        this.expandedNodes.clear();
        this.breadcrumbs = [];
        for (const idx of path) {
            this.expandedNodes.add(idx);
            this.breadcrumbs.push({ index: idx, name: this.resolveName(idx) });
        }
        this.focusIndex = target;
    }

    /**
     * "Zoom out" one level. If we're already at root, does nothing.
     */
    public navigateUp(): void {
        if (this.breadcrumbs.length <= 1) return;
        this.breadcrumbs.pop();
        const parent = this.breadcrumbs[this.breadcrumbs.length - 1];
        this.navigateTo(parent.index);
    }

    /** Convenience for the renderer's camera-focus calculation. */
    public getFocusIndex(): number { return this.focusIndex; }
    public getRootIndex(): number  { return this.rootIndex; }

    /**
     * Build the compacted per-frame instance buffers.
     *
     * Traversal rules:
     *   - Start from focusIndex.
     *   - If a folder is expanded, recurse into its children (don't draw
     *     the folder itself — the user has drilled inside it).
     *   - If a folder is collapsed, emit it as a crystal and stop.
     *   - Files always emit as bubbles.
     *
     * We push crystals and bubbles into two separate compact buffers,
     * preserving pre-order across each. This is the whole reason we don't
     * need a per-frame CPU sort: for strictly-nested bubbles, any pre-order
     * of the tree is a valid back-to-front order (parents strictly enclose
     * children, so drawing the parent hemispheres around the children
     * gives correct multiplicative attenuation).
     *
     * Note: the two-pass front/back-face bubble draw in WebGPURenderer
     * still applies — this function just decides WHICH bubbles get drawn
     * and in WHICH order across the tree. The renderer then does back-
     * hemisphere pass + front-hemisphere pass over the same compact set.
     */
    public buildVisibleSet(): VisibleSet {
        if (!this.srcBytes || this.rootIndex === -1) {
            return {
                crystalData: new Uint8Array(0), crystalCount: 0, crystalIndices: new Uint32Array(0),
                bubbleData:  new Uint8Array(0), bubbleCount:  0, bubbleIndices:  new Uint32Array(0),
            };
        }

        // Worst case: every node in the tree is visible. Pre-size to that
        // and slice down at the end. Avoids re-alloc during the recursion.
        const maxN = this.nodes.length;
        const crystalData = new Uint8Array(maxN * NODE_STRIDE);
        const bubbleData  = new Uint8Array(maxN * NODE_STRIDE);
        const crystalIdx  = new Uint32Array(maxN);
        const bubbleIdx   = new Uint32Array(maxN);
        let cCount = 0;
        let bCount = 0;

        const src = this.srcBytes;
        const nodes = this.nodes;
        const expanded = this.expandedNodes;

        // Iterative pre-order traversal to avoid recursion stack overflow
        // on deep trees. Stack holds source-node indices to visit.
        const stack: number[] = [];

        // Seed the stack with the focus node's children. We don't emit the
        // focus node itself (the user has drilled into it, so we're "inside"
        // its bubble, which would occlude everything).
        //
        // Pushing children in REVERSE so pop() yields them in original order.
        // That means the visible buffer preserves the natural order Rust
        // laid them out in, which corresponds to spatial layout adjacency.
        const focusChildren = nodes[this.focusIndex].children;
        for (let i = focusChildren.length - 1; i >= 0; i--) stack.push(focusChildren[i]);

        while (stack.length > 0) {
            const idx = stack.pop()!;
            const node = nodes[idx];
            if (!node) continue;

            const srcOff = idx * NODE_STRIDE;

            if (node.flags === FLAG_FOLDER) {
                if (expanded.has(idx)) {
                    // Expanded folder — recurse into children, don't emit self.
                    // Push children in reverse for stable in-order pop.
                    for (let i = node.children.length - 1; i >= 0; i--) {
                        stack.push(node.children[i]);
                    }
                } else {
                    // Collapsed folder — emit as a crystal.
                    crystalData.set(src.subarray(srcOff, srcOff + NODE_STRIDE), cCount * NODE_STRIDE);
                    crystalIdx[cCount] = idx;
                    cCount++;
                }
            } else {
                // File — emit as a bubble.
                bubbleData.set(src.subarray(srcOff, srcOff + NODE_STRIDE), bCount * NODE_STRIDE);
                bubbleIdx[bCount] = idx;
                bCount++;
            }
        }

        return {
            crystalData:    crystalData.subarray(0, cCount * NODE_STRIDE),
            crystalCount:   cCount,
            crystalIndices: crystalIdx.subarray(0, cCount),
            bubbleData:     bubbleData.subarray(0, bCount * NODE_STRIDE),
            bubbleCount:    bCount,
            bubbleIndices:  bubbleIdx.subarray(0, bCount),
        };
    }

    /**
     * Read the world-space center of a node from the source buffer.
     * Used by the renderer to place the camera focus.
     */
    public getPosition(index: number): [number, number, number] | null {
        if (!this.srcBuffer) return null;
        if (index < 0 || index >= this.nodes.length) return null;
        const dv = new DataView(this.srcBuffer);
        const off = index * NODE_STRIDE;
        return [
            dv.getFloat32(off + NODE_OFF_POS_X, true),
            dv.getFloat32(off + NODE_OFF_POS_Y, true),
            dv.getFloat32(off + NODE_OFF_POS_Z, true),
        ];
    }

    /** Radius of a node — used for camera zoom-to-fit. */
    public getRadius(index: number): number {
        if (!this.srcBuffer) return 0;
        if (index < 0 || index >= this.nodes.length) return 0;
        return new DataView(this.srcBuffer).getFloat32(index * NODE_STRIDE + NODE_OFF_RADIUS, true);
    }

    /**
     * Read-only accessor for the underlying source buffer. Renderers use this
     * when they need to read per-node fields directly (e.g. the WebGL2 tier
     * decoding position/radius while building instance matrices).
     *
     * Returns null if no data has been loaded. Callers MUST NOT mutate the
     * returned view — write access would corrupt every renderer sharing this
     * controller.
     */
    public getSourceView(): DataView | null {
        return this.srcBuffer ? new DataView(this.srcBuffer) : null;
    }
}
