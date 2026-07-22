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
import { generateCrystalVariants, type MeshData } from './geometry/icosahedron';
import { generateIcosphereLOD } from './geometry/icosphere';

const crystalVert = `
#include <common>
#include <fog_pars_vertex>
varying vec3 vViewPosition;
varying vec3 vNormal;
varying vec3 vWorldPosition;
void main() {
    vec4 instancePos = instanceMatrix * vec4(position, 1.0);
    vec4 mvPosition = viewMatrix * modelMatrix * instancePos;
    gl_Position = projectionMatrix * mvPosition;
    vViewPosition = -mvPosition.xyz;
    vWorldPosition = (modelMatrix * instancePos).xyz;
    vNormal = normalMatrix * mat3(instanceMatrix) * normal; 
    #include <fog_vertex>
}
`;

const crystalFrag = `
#include <common>
#include <fog_pars_fragment>
varying vec3 vViewPosition;
varying vec3 vNormal;
varying vec3 vWorldPosition;
uniform vec3 uColor;
uniform vec3 uLightDir;
uniform float uTime;
void main() {
    vec3 N = normalize(vNormal);
    vec3 V = normalize(vViewPosition);
    vec3 L = normalize(uLightDir);
    
    float NdotL = dot(N, L);
    float diff = max(NdotL, 0.0);
    float band = 0.2;
    if (diff > 0.5) band = 1.0;
    else if (diff > 0.1) band = 0.6;
    
    vec3 baseColor = uColor * band;
    
    float rim = 1.0 - max(dot(V, N), 0.0);
    rim = smoothstep(0.6, 1.0, rim);
    vec3 rimColor = vec3(0.5, 0.7, 1.0) * rim * 1.5;
    
    float pulse = (sin(uTime * 2.0 + vWorldPosition.x + vWorldPosition.y) * 0.5 + 0.5) * 0.15;
    
    gl_FragColor = vec4(baseColor + rimColor + vec3(pulse), 1.0);
    
    #include <fog_fragment>
}
`;

const bubbleVert = `
#include <common>
#include <fog_pars_vertex>
varying vec3 vViewPosition;
varying vec3 vNormal;
varying vec3 vWorldPosition;
uniform float uTime;
void main() {
    vec4 instancePos = instanceMatrix * vec4(position, 1.0);
    vec4 worldPos = modelMatrix * instancePos;
    
    vec3 wobble = sin(uTime * 3.0 + worldPos.x * 2.0) * 0.05 * position;
    vec4 wobbledPos = instanceMatrix * vec4(position + wobble, 1.0);
    vec4 mvPosition = viewMatrix * modelMatrix * wobbledPos;
    
    gl_Position = projectionMatrix * mvPosition;
    vViewPosition = -mvPosition.xyz;
    vWorldPosition = (modelMatrix * wobbledPos).xyz;
    vNormal = normalMatrix * mat3(instanceMatrix) * normal; 
    #include <fog_vertex>
}
`;

const bubbleFrag = `
#include <common>
#include <fog_pars_fragment>
varying vec3 vViewPosition;
varying vec3 vNormal;
varying vec3 vWorldPosition;
uniform vec3 uColor;
uniform vec3 uLightDir;
uniform float uTime;
void main() {
    vec3 N = normalize(vNormal);
    vec3 V = normalize(vViewPosition);
    vec3 L = normalize(uLightDir);
    
    float NdotL = dot(N, L);
    float diff = max(NdotL, 0.0);
    float band = 0.3;
    if (diff > 0.5) band = 1.0;
    else if (diff > 0.1) band = 0.7;
    
    vec3 baseColor = uColor * band;
    
    float rim = 1.0 - max(dot(V, N), 0.0);
    rim = smoothstep(0.4, 1.0, rim);
    vec3 rimColor = vec3(0.6, 0.8, 1.0) * rim * 2.0;
    
    gl_FragColor = vec4(baseColor + rimColor, 0.35 + rim * 0.5);
    
    #include <fog_fragment>
}
`;

export class WebGL2Renderer {
    private readonly canvas: HTMLCanvasElement;
    private renderer!: THREE.WebGLRenderer;
    private scene!: THREE.Scene;
    private camera!: THREE.PerspectiveCamera;

    private crystalMeshes: THREE.InstancedMesh[] = [];
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
    private readonly dummy = new THREE.Object3D();
    private readonly raycaster = new THREE.Raycaster();
    private readonly pointerNDC = new THREE.Vector2();

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

        this.scene.fog = new THREE.FogExp2(0x02030a, 0.0005);

        // Uniforms for shaders
        const uniforms = {
            uTime: { value: 0 },
            uLightDir: { value: new THREE.Vector3(1, 1, 0.5).normalize() },
            uColor: { value: new THREE.Color(0xa080ff) },
            ...THREE.UniformsLib['fog']
        };

        const crystalMat = new THREE.ShaderMaterial({
            vertexShader: crystalVert,
            fragmentShader: crystalFrag,
            uniforms: THREE.UniformsUtils.clone(uniforms),
            fog: true,
        });

        const bubbleUniforms = THREE.UniformsUtils.clone(uniforms);
        bubbleUniforms.uColor.value = new THREE.Color(0xaaddff);

        const bubbleMatBack = new THREE.ShaderMaterial({
            vertexShader: bubbleVert,
            fragmentShader: bubbleFrag,
            uniforms: bubbleUniforms,
            fog: true,
            transparent: true,
            side: THREE.BackSide,
            depthWrite: false,
        });
        const bubbleMatFront = new THREE.ShaderMaterial({
            vertexShader: bubbleVert,
            fragmentShader: bubbleFrag,
            uniforms: bubbleUniforms,
            fog: true,
            transparent: true,
            side: THREE.FrontSide,
            depthWrite: false,
        });

        // Base geometries (allocated once, shared across instances).
        const crystalVariants = generateCrystalVariants(3);
        const bubbleData = generateIcosphereLOD(3);
        
        const createGeo = (data: MeshData, flat: boolean) => {
            const geo = new THREE.BufferGeometry();
            const positions = new Float32Array(data.vertexCount * 3);
            const normals = new Float32Array(data.vertexCount * 3);
            for (let i = 0; i < data.vertexCount; i++) {
                positions[i*3+0] = data.vertices[i*6+0];
                positions[i*3+1] = data.vertices[i*6+1];
                positions[i*3+2] = data.vertices[i*6+2];
                normals[i*3+0] = data.vertices[i*6+3];
                normals[i*3+1] = data.vertices[i*6+4];
                normals[i*3+2] = data.vertices[i*6+5];
            }
            geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
            geo.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
            if (data.indices) {
                geo.setIndex(new THREE.BufferAttribute(data.indices, 1));
            }
            if (flat) {
                const nonIndexed = geo.toNonIndexed();
                nonIndexed.computeVertexNormals();
                return nonIndexed;
            }
            return geo;
        };
        
        const bubbleGeo = createGeo(bubbleData, false);

        // Zero-capacity placeholders — resized in loadData once we know node count.
        for (let i = 0; i < 3; i++) {
            const mesh = new THREE.InstancedMesh(createGeo(crystalVariants[i], true), crystalMat, 1);
            mesh.count = 0;
            this.crystalMeshes.push(mesh);
            this.scene.add(mesh);
        }
        this.bubbleBack   = new THREE.InstancedMesh(bubbleGeo,     bubbleMatBack,  1);
        this.bubbleBack.count = 0;
        this.bubbleFront  = new THREE.InstancedMesh(bubbleGeo,     bubbleMatFront, 1);
        this.bubbleFront.count = 0;

        // Simple opaque mesh dedicated to picking. Not added to the visible scene.
        const pickMat = new THREE.MeshBasicMaterial({ color: 0xffffff, visible: false });
        this.pickMesh = new THREE.InstancedMesh(bubbleGeo, pickMat, 1);
        this.pickMesh.count = 0;

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
        const crystalMat = this.crystalMeshes[0].material as THREE.Material;
        const backMat    = this.bubbleBack.material  as THREE.Material;
        const frontMat   = this.bubbleFront.material as THREE.Material;
        const pickMat    = this.pickMesh.material    as THREE.Material;

        for (const m of this.crystalMeshes) {
            this.scene.remove(m);
            m.dispose();
        }
        this.crystalMeshes = [];
        
        this.scene.remove(this.bubbleBack);
        this.scene.remove(this.bubbleFront);
        this.bubbleBack.dispose();
        this.bubbleFront.dispose();
        this.pickMesh.dispose();

        const cap = Math.max(1, this.nodeCount);
        
        const crystalVariants = generateCrystalVariants(3);
        const createGeo = (data: MeshData, flat: boolean) => {
            const geo = new THREE.BufferGeometry();
            const positions = new Float32Array(data.vertexCount * 3);
            const normals = new Float32Array(data.vertexCount * 3);
            for (let i = 0; i < data.vertexCount; i++) {
                positions[i*3+0] = data.vertices[i*6+0];
                positions[i*3+1] = data.vertices[i*6+1];
                positions[i*3+2] = data.vertices[i*6+2];
                normals[i*3+0] = data.vertices[i*6+3];
                normals[i*3+1] = data.vertices[i*6+4];
                normals[i*3+2] = data.vertices[i*6+5];
            }
            geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
            geo.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
            if (data.indices) geo.setIndex(new THREE.BufferAttribute(data.indices, 1));
            if (flat) {
                const nonIdx = geo.toNonIndexed();
                nonIdx.computeVertexNormals();
                return nonIdx;
            }
            return geo;
        };

        for (let i = 0; i < 3; i++) {
            const mesh = new THREE.InstancedMesh(createGeo(crystalVariants[i], true), crystalMat, cap);
            mesh.count = 0;
            this.crystalMeshes.push(mesh);
            this.scene.add(mesh);
        }

        const bubbleGeo = createGeo(generateIcosphereLOD(3), false);
        
        this.bubbleBack  = new THREE.InstancedMesh(bubbleGeo,  backMat,  cap);
        this.bubbleFront = new THREE.InstancedMesh(bubbleGeo,  frontMat, cap);
        this.pickMesh    = new THREE.InstancedMesh(bubbleGeo,  pickMat,  cap);
        this.bubbleBack.count = 0;
        this.bubbleFront.count = 0;
        this.pickMesh.count = 0;

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

        this.crystalSourceIndices = [];
        const variantCounts = [0, 0, 0];
        
        for (let i = 0; i < v.crystalCount; i++) {
            const src = v.crystalIndices[i] * NODE_STRIDE;
            const hash = rawSrc.getUint32(src + 24, true);
            const variantIdx = hash % 3;
            
            const x = rawSrc.getFloat32(src + 0,  true);
            const y = rawSrc.getFloat32(src + 4,  true);
            const z = rawSrc.getFloat32(src + 8,  true);
            const r = rawSrc.getFloat32(src + 12, true);
            
            this.dummy.position.set(x, y, z);
            this.dummy.scale.set(r, r, r);
            this.dummy.rotation.set(
                (hash % 360) * 0.017453,
                ((hash >> 8) % 360) * 0.017453,
                ((hash >> 16) % 360) * 0.017453,
            );
            this.dummy.updateMatrix();
            
            const mesh = this.crystalMeshes[variantIdx];
            mesh.setMatrixAt(variantCounts[variantIdx], this.dummy.matrix);
            variantCounts[variantIdx]++;
            
            // To make picking map to the correct source, we need to map per-variant.
            // But raycaster returns instanceId which corresponds to the mesh.
            // The pick mesh doesn't have variants! We populate pickMesh with ALL crystals
            // linearly in [0, crystalCount). So crystalSourceIndices maps directly to pickMesh.
            this.crystalSourceIndices.push(v.crystalIndices[i]);
        }
        
        for (let i = 0; i < 3; i++) {
            this.crystalMeshes[i].count = variantCounts[i];
            this.crystalMeshes[i].instanceMatrix.needsUpdate = true;
        }

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

    private startTime = performance.now();

    public render(): void {
        if (this.nodeCount === 0) return;
        if (this.visibleDirty) this.rebuildInstances();
        this.updateCamera();
        
        const time = (performance.now() - this.startTime) / 1000;
        if (this.crystalMeshes.length > 0) {
            const mat = this.crystalMeshes[0].material as THREE.ShaderMaterial;
            if (mat.uniforms.uTime) mat.uniforms.uTime.value = time;
        }
        const bMat = this.bubbleFront.material as THREE.ShaderMaterial;
        if (bMat.uniforms.uTime) bMat.uniforms.uTime.value = time;
        
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
        for (const m of this.crystalMeshes) m?.dispose();

        this.bubbleBack?.dispose();
        this.bubbleFront?.dispose();
        this.pickMesh?.dispose();
        this.renderer?.dispose();
    }
}
