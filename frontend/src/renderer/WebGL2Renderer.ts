/**
 * WebGL2Renderer.ts
 *
 * Three.js-based middle-tier renderer for platforms where WebGPU is not
 * available (mostly macOS/WKWebView releases where WebGPU hasn't landed,
 * and older browsers). Same public API as WebGPURenderer so the React
 * layer treats them interchangeably.
 *
 * Design goals:
 *   - Reuse the SAME hierarchy layout (Rust Barnes-Hut, 32-byte-per-node
 *     buffer) so the visualization is spatially identical between tiers.
 *   - Reuse the SAME NavigationController — LOD, expand/collapse, and
 *     visible-set compaction are tier-agnostic pure TypeScript.
 *   - Approximate the WGSL crystal/bubble look with three.js built-in
 *     MeshPhysicalMaterial (transmission, iridescence, thickness). Not
 *     pixel-identical to WebGPU tier, but same visual language.
 *   - Real GPU-accelerated picking via Raycaster (not the raster-readback
 *     trick WebGPU uses — three.js Raycaster is fine at these node counts).
 *
 * What we deliberately don't do:
 *   - No custom GLSL shaders. The whole point of the WebGL2 tier is
 *     "safe, universal path" — sticking to stock three.js materials avoids
 *     GLSL version pitfalls on older drivers.
 *   - No screen-space refraction snapshot loop. MeshPhysicalMaterial's
 *     `transmission` already handles this reasonably.
 *
 * Fixes vs. the previous revision:
 *   - pick() actually works (was returning null).
 *   - Bubbles emitted in tree pre-order so nested transparency composites
 *     correctly (matches the WebGPU tier).
 *   - Compacted per-frame instance matrices; count = visibleSet size.
 *   - focusOnNode() matches WebGPURenderer's signature so the shared
 *     hook in WebGPUFallback.tsx works over both.
 */

import * as THREE from 'three';
import { NavigationController, NODE_STRIDE } from '../interaction/NavigationController';

export class WebGL2Renderer {
    private readonly canvas: HTMLCanvasElement;
    private renderer!: THREE.WebGLRenderer;
    private scene!: THREE.Scene;
    private camera!: THREE.PerspectiveCamera;

    private crystalMesh!: THREE.InstancedMesh;
    /** Bubbles get TWO InstancedMesh instances so we can render back faces then
     *  front faces with the same instance matrices — the same trick as the
     *  WebGPU tier. */
    private bubbleBack!: THREE.InstancedMesh;
    private bubbleFront!: THREE.InstancedMesh;

    /** Off-screen invisible mesh used ONLY for raycasting — cheaper than
     *  intersecting the instanced glass meshes, which need to consider
     *  material.side. Kept in sync with visible crystal + bubble positions. */
    private pickMesh!: THREE.InstancedMesh;

    public readonly nav = new NavigationController();
    private nodeCount = 0;
    private visibleDirty = true;

    // Camera state — matches WebGPURenderer's conventions.
    private rotationX = 0.5;
    private rotationY = 0.5;
    private zoom = 550;
    public focusPosition: [number, number, number] = [0, 0, 0];
    private cameraPosition = new THREE.Vector3();
    private isFirstFrame = true;

    /** Persistent Object3D used to compose per-instance matrices. Reusing a
     *  single one is a standard three.js idiom to avoid GC churn. */
    private dummy = new THREE.Object3D();
    private raycaster = new THREE.Raycaster();
    private pointerNDC = new THREE.Vector2();

    /** Source-index arrays maintained per-frame, matching the InstancedMesh
     *  slot order — used to translate a raycast hit's instanceId back to
     *  the original node index. */
    private crystalSourceIndices: number[] = [];
    private bubbleSourceIndices: number[] = [];

    constructor(canvas: HTMLCanvasElement) {
        this.canvas = canvas;
    }

    public async init(): Promise<void> {
        this.renderer = new THREE.WebGLRenderer({
            canvas: this.canvas,
            antialias: true,
            alpha: true,
        });
        this.renderer.setClearColor(0x02030a, 1); // matches WebGPU tier's void
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        const w = Math.max(1, this.canvas.clientWidth);
        const h = Math.max(1, this.canvas.clientHeight);
        this.renderer.setSize(w, h, false);

        this.scene = new THREE.Scene();
        this.camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 100000);

        // Lighting: one key + one fill + a subtle rim from behind so
        // Fresnel effects on the bubbles read as glass.
        this.scene.add(new THREE.AmbientLight(0xffffff, 0.35));
        const key = new THREE.DirectionalLight(0xffffff, 0.9);
        key.position.set(200, 300, 100);
        this.scene.add(key);
        const rim = new THREE.DirectionalLight(0x88aaff, 0.6);
        rim.position.set(-150, -50, -200);
        this.scene.add(rim);

        // Base geometries (allocated once, shared across instances).
        const crystalGeo = new THREE.IcosahedronGeometry(1, 1); // matches WebGPU's subdivision-1
        // three.js smooths per-vertex normals by default. For flat facets we
        // clear the vertex normals and rely on IcosahedronGeometry's face-based
        // shading via toNonIndexed + computeVertexNormals.
        const crystalGeoFlat = crystalGeo.toNonIndexed();
        crystalGeoFlat.computeVertexNormals();

        const bubbleGeo = new THREE.IcosahedronGeometry(1, 3);

        // Materials
        const crystalMat = new THREE.MeshPhysicalMaterial({
            color: 0xa080ff,          // violet gem base
            metalness: 0.0,
            roughness: 0.15,
            transmission: 0.55,        // some refraction, not full glass
            thickness: 1.2,
            ior: 1.55,
            iridescence: 0.2,
            iridescenceIOR: 1.4,
            flatShading: true,
            envMapIntensity: 1.4,
        });

        const bubbleMatBack = new THREE.MeshPhysicalMaterial({
            color: 0xaaddff,
            metalness: 0.0,
            roughness: 0.05,
            transmission: 1.0,
            thickness: 0.3,
            ior: 1.33,
            iridescence: 1.0,
            iridescenceIOR: 1.3,
            iridescenceThicknessRange: [100, 400],
            transparent: true,
            opacity: 0.35,
            side: THREE.BackSide,
            depthWrite: false,
        });
        const bubbleMatFront = new THREE.MeshPhysicalMaterial({
            color: 0xaaddff,
            metalness: 0.0,
            roughness: 0.05,
            transmission: 1.0,
            thickness: 0.3,
            ior: 1.33,
            iridescence: 1.0,
            iridescenceIOR: 1.3,
            iridescenceThicknessRange: [100, 400],
            transparent: true,
            opacity: 0.45,
            side: THREE.FrontSide,
            depthWrite: false,
        });

        // Zero-capacity placeholders — resized in loadData once we know node count.
        this.crystalMesh = new THREE.InstancedMesh(crystalGeoFlat, crystalMat, 1);
        this.crystalMesh.count = 0;
        this.bubbleBack   = new THREE.InstancedMesh(bubbleGeo,     bubbleMatBack,  1);
        this.bubbleBack.count = 0;
        this.bubbleFront  = new THREE.InstancedMesh(bubbleGeo,     bubbleMatFront, 1);
        this.bubbleFront.count = 0;

        // Simple opaque mesh dedicated to picking. Not added to the visible scene.
        const pickMat = new THREE.MeshBasicMaterial({ color: 0xffffff, visible: false });
        this.pickMesh = new THREE.InstancedMesh(bubbleGeo, pickMat, 1);
        this.pickMesh.count = 0;

        this.scene.add(this.crystalMesh);
        // Ordering matters for three.js's own transparent-sort — but because
        // depthWrite is false on both bubble meshes and their materials are
        // marked transparent, three.js will sort by centroid distance anyway.
        // We DO NOT rely on that; we rebuild instance matrices in tree pre-order
        // (see render()) so the intra-mesh instance order is already correct.
        this.scene.add(this.bubbleBack);
        this.scene.add(this.bubbleFront);
    }

    public resize(width: number, height: number): void {
        if (!this.renderer) return;
        this.renderer.setSize(Math.max(1, width), Math.max(1, height), false);
        this.camera.aspect = Math.max(1, width) / Math.max(1, height);
        this.camera.updateProjectionMatrix();
    }

    public async loadData(data: ArrayBuffer): Promise<void> {
        this.nodeCount = Math.floor(data.byteLength / NODE_STRIDE);
        if (this.nodeCount === 0) return;

        // Same integrity check as the WebGPU renderer.
        const dv = new DataView(data);
        let hasRoot = false;
        for (let i = 0; i < this.nodeCount; i++) {
            if (dv.getUint32(i * NODE_STRIDE + 16, true) === 0xFFFFFFFF) { hasRoot = true; break; }
        }
        if (!hasRoot) throw new Error('Visualizer stream is malformed: no root node.');

        this.nav.loadData(data);

        // Reallocate InstancedMeshes with worst-case capacity = nodeCount.
        // three.js's InstancedMesh doesn't grow dynamically; capacity is fixed
        // at construction. We recreate them here on load.
        const crystalGeo = this.crystalMesh.geometry;
        const bubbleGeo  = this.bubbleBack.geometry;
        const crystalMat = this.crystalMesh.material as THREE.Material;
        const backMat    = this.bubbleBack.material  as THREE.Material;
        const frontMat   = this.bubbleFront.material as THREE.Material;
        const pickMat    = this.pickMesh.material    as THREE.Material;

        this.scene.remove(this.crystalMesh);
        this.scene.remove(this.bubbleBack);
        this.scene.remove(this.bubbleFront);
        this.crystalMesh.dispose();
        this.bubbleBack.dispose();
        this.bubbleFront.dispose();
        this.pickMesh.dispose();

        const cap = Math.max(1, this.nodeCount);
        this.crystalMesh = new THREE.InstancedMesh(crystalGeo, crystalMat, cap);
        this.bubbleBack  = new THREE.InstancedMesh(bubbleGeo,  backMat,  cap);
        this.bubbleFront = new THREE.InstancedMesh(bubbleGeo,  frontMat, cap);
        this.pickMesh    = new THREE.InstancedMesh(bubbleGeo,  pickMat,  cap);
        this.crystalMesh.count = 0;
        this.bubbleBack.count = 0;
        this.bubbleFront.count = 0;
        this.pickMesh.count = 0;

        this.scene.add(this.crystalMesh);
        this.scene.add(this.bubbleBack);
        this.scene.add(this.bubbleFront);

        this.visibleDirty = true;

        // Focus root at load.
        const rootPos = this.nav.getPosition(this.nav.getRootIndex());
        if (rootPos) this.focusPosition = rootPos;
        this.isFirstFrame = true;
    }

    public markDirty(): void { this.visibleDirty = true; }

    public handleMouseMove(dx: number, dy: number): void {
        this.rotationY -= dx * 0.005;
        this.rotationX += dy * 0.005;
        const EPS = 0.1;
        this.rotationX = Math.max(-Math.PI / 2 + EPS, Math.min(Math.PI / 2 - EPS, this.rotationX));
    }
    public handleZoom(delta: number): void {
        const step = Math.max(10, this.zoom * 0.05);
        this.zoom = Math.max(5, this.zoom + (delta > 0 ? step : -step));
    }

    private updateCamera(): void {
        const t = this.focusPosition;
        const eyeX = t[0] + this.zoom * Math.cos(this.rotationX) * Math.sin(this.rotationY);
        const eyeY = t[1] + this.zoom * Math.sin(this.rotationX);
        const eyeZ = t[2] + this.zoom * Math.cos(this.rotationX) * Math.cos(this.rotationY);
        if (this.isFirstFrame) {
            this.cameraPosition.set(eyeX, eyeY, eyeZ);
            this.isFirstFrame = false;
        } else {
            this.cameraPosition.lerp(new THREE.Vector3(eyeX, eyeY, eyeZ), 0.12);
        }
        this.camera.position.copy(this.cameraPosition);
        this.camera.lookAt(t[0], t[1], t[2]);
    }

    /**
     * Rebuild the per-instance matrices from the current VisibleSet. Called
     * only when nav state changed (visibleDirty) OR when the source buffer
     * changed. Matches the WebGPU tier's "compacted VBO on nav event" pattern.
     */
    private rebuildInstances(): void {
        const v = this.nav.buildVisibleSet();
        const rawSrc = this.nav.getSourceView();
        if (!rawSrc) return;

        // Crystals
        this.crystalSourceIndices = Array.from(v.crystalIndices);
        this.crystalMesh.count = v.crystalCount;
        for (let i = 0; i < v.crystalCount; i++) {
            const src = v.crystalIndices[i] * NODE_STRIDE;
            const x = rawSrc.getFloat32(src + 0,  true);
            const y = rawSrc.getFloat32(src + 4,  true);
            const z = rawSrc.getFloat32(src + 8,  true);
            const r = rawSrc.getFloat32(src + 12, true);
            const hash = rawSrc.getUint32(src + 24, true);
            this.dummy.position.set(x, y, z);
            this.dummy.scale.set(r, r, r);
            // Per-instance rotation seeded by hash — matches the WebGPU tier's
            // in-shader rotation trick. Keeps silhouettes varied without any
            // per-instance geometry variation.
            this.dummy.rotation.set(
                (hash % 360) * 0.017453,
                ((hash >> 8) % 360) * 0.017453,
                ((hash >> 16) % 360) * 0.017453,
            );
            this.dummy.updateMatrix();
            this.crystalMesh.setMatrixAt(i, this.dummy.matrix);
        }
        this.crystalMesh.instanceMatrix.needsUpdate = true;

        // Bubbles — both back and front share the same matrix set.
        this.bubbleSourceIndices = Array.from(v.bubbleIndices);
        this.bubbleBack.count = v.bubbleCount;
        this.bubbleFront.count = v.bubbleCount;
        this.pickMesh.count = v.bubbleCount + v.crystalCount;
        this.dummy.rotation.set(0, 0, 0); // bubbles don't rotate
        for (let i = 0; i < v.bubbleCount; i++) {
            const src = v.bubbleIndices[i] * NODE_STRIDE;
            const x = rawSrc.getFloat32(src + 0,  true);
            const y = rawSrc.getFloat32(src + 4,  true);
            const z = rawSrc.getFloat32(src + 8,  true);
            const r = rawSrc.getFloat32(src + 12, true);
            this.dummy.position.set(x, y, z);
            this.dummy.scale.set(r, r, r);
            this.dummy.updateMatrix();
            this.bubbleBack.setMatrixAt(i, this.dummy.matrix);
            this.bubbleFront.setMatrixAt(i, this.dummy.matrix);
            // Pick mesh: bubble slots occupy indices [crystalCount, crystalCount+bubbleCount).
            this.pickMesh.setMatrixAt(v.crystalCount + i, this.dummy.matrix);
        }
        // Pick mesh crystals occupy [0, crystalCount).
        for (let i = 0; i < v.crystalCount; i++) {
            const src = v.crystalIndices[i] * NODE_STRIDE;
            const x = rawSrc.getFloat32(src + 0,  true);
            const y = rawSrc.getFloat32(src + 4,  true);
            const z = rawSrc.getFloat32(src + 8,  true);
            const r = rawSrc.getFloat32(src + 12, true);
            this.dummy.position.set(x, y, z);
            this.dummy.scale.set(r, r, r);
            this.dummy.updateMatrix();
            this.pickMesh.setMatrixAt(i, this.dummy.matrix);
        }
        this.bubbleBack.instanceMatrix.needsUpdate = true;
        this.bubbleFront.instanceMatrix.needsUpdate = true;
        this.pickMesh.instanceMatrix.needsUpdate = true;

        this.visibleDirty = false;
    }

    public render(): void {
        if (this.nodeCount === 0) return;
        if (this.visibleDirty) this.rebuildInstances();
        this.updateCamera();
        this.renderer.render(this.scene, this.camera);
    }

    /**
     * Ray-based picking. Uses a hidden InstancedMesh (pickMesh) that
     * mirrors the visible geometry — cheaper than raycasting the actual
     * transparent bubble meshes with their material.side/depthWrite quirks.
     *
     * The pickMesh packs crystals into slots [0, crystalCount) and bubbles
     * into [crystalCount, crystalCount+bubbleCount), so the hit's
     * instanceId tells us which array to look up.
     */
    public async pick(x: number, y: number): Promise<number | null> {
        if (this.pickMesh.count === 0) return null;

        const rect = this.canvas.getBoundingClientRect();
        // three.js NDC: [-1, 1] with Y flipped from screen coords.
        this.pointerNDC.x =  (x / rect.width)  * 2 - 1;
        this.pointerNDC.y = -(y / rect.height) * 2 + 1;
        this.raycaster.setFromCamera(this.pointerNDC, this.camera);

        const hits = this.raycaster.intersectObject(this.pickMesh, false);
        if (hits.length === 0) return null;

        const inst = hits[0].instanceId;
        if (inst === undefined) return null;

        if (inst < this.crystalSourceIndices.length) {
            return this.crystalSourceIndices[inst];
        }
        const bubbleSlot = inst - this.crystalSourceIndices.length;
        if (bubbleSlot < this.bubbleSourceIndices.length) {
            return this.bubbleSourceIndices[bubbleSlot];
        }
        return null;
    }

    public focusOnNode(sourceIndex: number): void {
        const p = this.nav.getPosition(sourceIndex);
        const r = this.nav.getRadius(sourceIndex);
        if (!p) return;
        this.focusPosition = p;
        this.zoom = Math.max(50, r * 2.5);
    }

    public destroy(): void {
        this.crystalMesh?.dispose();
        this.bubbleBack?.dispose();
        this.bubbleFront?.dispose();
        this.pickMesh?.dispose();
        this.renderer?.dispose();
    }
}
