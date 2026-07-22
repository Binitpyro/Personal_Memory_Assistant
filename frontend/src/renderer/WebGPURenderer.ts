/**
 * WebGPURenderer.ts
 *
 * Tier-1 renderer for the Crystal Dreamscape insights view.
 *
 * Instanced-mesh generation:
 *   - Collapsed folders render as flat-shaded icosahedron "crystals"
 *     (crystal.wgsl, opaque, fake screen-space refraction of the
 *     previous frame).
 *   - Files render as smooth icosphere "bubbles" (bubble.wgsl,
 *     transparent, two-pass back/front cull, pre-order draw so nested
 *     translucency composites correctly without a per-frame sort).
 *   - GPU picking renders instance indices into an r32uint target
 *     (picking.wgsl); the CPU maps the compacted slot back to the
 *     source node index via VisibleSet.crystalIndices/bubbleIndices.
 *
 * Shares the NavigationController with WebGL2Renderer so LOD,
 * expand/collapse and breadcrumbs are tier-agnostic. Public API matches
 * WebGL2Renderer — the RendererLike contract in WebGPUFallback.tsx.
 */

import commonShaderCode from './shaders/common.wgsl?raw';
import crystalShaderCode from './shaders/crystal.wgsl?raw';
import bubbleShaderCode from './shaders/bubble.wgsl?raw';
import pickingShaderCode from './shaders/picking.wgsl?raw';
import outlineShaderCode from './shaders/outline.wgsl?raw';
import { generateCrystalVariants, type MeshData } from './geometry/icosahedron';
import { generateIcosphereLOD } from './geometry/icosphere';
import { NavigationController, NODE_STRIDE, NO_PARENT } from '../interaction/NavigationController';

/** CameraUniform in crystal/bubble/picking.wgsl:
 *  mat4x4 viewProj (64) + eyePosition vec3 + currentVariant u32 (16) + time/w/h/fogDen (16) + fogCol/pad2 (16) = 112 bytes. */
const CAMERA_UNIFORM_SIZE = 112;

/** Background: dark void matching the WebGL2 tier and the page chrome (#02030a). */
const CLEAR_COLOR: GPUColor = [0.008, 0.012, 0.039, 1];

interface GpuMesh {
    vertexBuffer: GPUBuffer;
    indexBuffer: GPUBuffer;
    indexCount: number;
}

export class WebGPURenderer {
    private readonly canvas: HTMLCanvasElement;
    private device!: GPUDevice;
    private context!: GPUCanvasContext;
    private format!: GPUTextureFormat;

    // Static geometry (allocated once at init)
    private crystalMeshes: GpuMesh[] = [];
    private bubbleMesh!: GpuMesh;

    public enableOutline: boolean = true;

    // Per-frame compacted instance buffer.
    // Layout: crystal rows in [0, crystalCount), bubble rows in
    // [crystalCount, crystalCount + bubbleCount). Keeping both in ONE
    // buffer lets the picking pass use firstInstance = crystalCount for
    // bubbles so pick IDs are unique across both mesh types.
    private instanceBuffer?: GPUBuffer;
    private crystalCount = 0;
    private bubbleCount = 0;
    private crystalIndices: Uint32Array = new Uint32Array(0);
    private bubbleIndices: Uint32Array = new Uint32Array(0);

    // Render targets
    private depthTexture!: GPUTexture;
    private pickingTexture!: GPUTexture;

    private cameraBuffers: GPUBuffer[] = [];
    private pickBuffer!: GPUBuffer;

    // Explicit layouts so all pipelines share ONE camera bind group.
    private cameraBGL!: GPUBindGroupLayout;
    private cameraBindGroups: GPUBindGroup[] = [];

    private outlineBGL!: GPUBindGroupLayout;
    private outlinePipeline!: GPURenderPipeline;
    private outlineBindGroup!: GPUBindGroup;
    private nearestSampler!: GPUSampler;

    private crystalPipeline!: GPURenderPipeline;
    private bubbleBackPipeline!: GPURenderPipeline;
    private bubbleFrontPipeline!: GPURenderPipeline;
    private pickingPipeline!: GPURenderPipeline;

    public readonly nav = new NavigationController();
    private nodeCount = 0;
    private visibleDirty = true;

    // Camera state — same conventions as WebGL2Renderer.
    private rotationX = 0.5;
    private rotationY = 0.5;
    private zoom = 550;
    public focusPosition: [number, number, number] = [0, 0, 0];
    private cameraPosition: [number, number, number] = [0, 0, 0];
    private isFirstFrame = true;
    private readonly startTime = performance.now();

    constructor(canvas: HTMLCanvasElement) {
        this.canvas = canvas;
    }

    public async init(): Promise<void> {
        if (!navigator.gpu) {
            throw new Error('WebGPU not supported on this browser.');
        }
        const adapter = await navigator.gpu.requestAdapter();
        if (!adapter) {
            throw new Error('No appropriate GPUAdapter found.');
        }
        this.device = await adapter.requestDevice();

        this.context = this.canvas.getContext('webgpu') as GPUCanvasContext;
        this.format = navigator.gpu.getPreferredCanvasFormat();
        this.context.configure({
            device: this.device,
            format: this.format,
            alphaMode: 'premultiplied',
            // COPY_SRC: we snapshot the presented frame into prevFrameTexture
            // each frame so crystal.wgsl can fake refraction next frame.
            usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.COPY_SRC,
        });

        this.canvas.width = Math.max(1, this.canvas.clientWidth);
        this.canvas.height = Math.max(1, this.canvas.clientHeight);

        for (let i = 0; i < 3; i++) {
            this.cameraBuffers.push(this.device.createBuffer({
                size: CAMERA_UNIFORM_SIZE,
                usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
            }));
        }

        this.pickBuffer = this.device.createBuffer({
            size: 256,
            usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ,
        });

        const variants = generateCrystalVariants(3);
        this.crystalMeshes = variants.map(v => this.uploadMesh(v));
        this.bubbleMesh = this.uploadMesh(generateIcosphereLOD(3));

        this.nearestSampler = this.device.createSampler({ magFilter: 'nearest', minFilter: 'nearest' });

        this.setupPipelines();
        this.setupTextures();

        this.cameraBindGroups = this.cameraBuffers.map(buffer => this.device.createBindGroup({
            layout: this.cameraBGL,
            entries: [{ binding: 0, resource: { buffer } }],
        }));
    }

    private uploadMesh(mesh: MeshData): GpuMesh {
        const vertexBuffer = this.device.createBuffer({
            size: mesh.vertices.byteLength,
            usage: GPUBufferUsage.VERTEX,
            mappedAtCreation: true,
        });
        new Float32Array(vertexBuffer.getMappedRange()).set(mesh.vertices);
        vertexBuffer.unmap();

        // Index buffer size must be a multiple of 4 bytes.
        const idxByteLength = Math.ceil(mesh.indices.byteLength / 4) * 4;
        const indexBuffer = this.device.createBuffer({
            size: idxByteLength,
            usage: GPUBufferUsage.INDEX,
            mappedAtCreation: true,
        });
        new Uint16Array(indexBuffer.getMappedRange(), 0, mesh.indices.length).set(mesh.indices);
        indexBuffer.unmap();

        return { vertexBuffer, indexBuffer, indexCount: mesh.indexCount };
    }

    /** Vertex-buffer layouts shared by crystal, bubble and picking pipelines.
     *  Must stay in sync with the VertexInput structs in the WGSL files:
     *  locations 0-1 = mesh (stride 24), locations 2-7 = instance (stride 32,
     *  matching the Rust Node struct byte-for-byte). */
    private vertexLayouts(): GPUVertexBufferLayout[] {
        return [
            {
                arrayStride: 24,
                attributes: [
                    { shaderLocation: 0, offset: 0, format: 'float32x3' },  // local_pos
                    { shaderLocation: 1, offset: 12, format: 'float32x3' }, // local_normal
                ],
            },
            {
                arrayStride: NODE_STRIDE,
                stepMode: 'instance',
                attributes: [
                    { shaderLocation: 2, offset: 0, format: 'float32x3' },  // inst_position
                    { shaderLocation: 3, offset: 12, format: 'float32' },   // inst_radius
                    { shaderLocation: 4, offset: 16, format: 'uint32' },    // inst_parent_index
                    { shaderLocation: 5, offset: 20, format: 'uint32' },    // inst_flags
                    { shaderLocation: 6, offset: 24, format: 'uint32' },    // inst_type_hash
                    { shaderLocation: 7, offset: 28, format: 'uint32' },    // inst_pad
                ],
            },
        ];
    }

    private setupPipelines(): void {
        this.cameraBGL = this.device.createBindGroupLayout({
            entries: [{
                binding: 0,
                visibility: GPUShaderStage.VERTEX | GPUShaderStage.FRAGMENT,
                buffer: { type: 'uniform', minBindingSize: CAMERA_UNIFORM_SIZE },
            }],
        });

        const cameraOnlyLayout = this.device.createPipelineLayout({ bindGroupLayouts: [this.cameraBGL] });
        const buffers = this.vertexLayouts();

        const fullCrystalShader = commonShaderCode + '\n' + crystalShaderCode;
        const crystalModule = this.device.createShaderModule({ code: fullCrystalShader });
        
        // Crystals: opaque, depth-writing.
        this.crystalPipeline = this.device.createRenderPipeline({
            layout: cameraOnlyLayout,
            vertex: { module: crystalModule, entryPoint: 'vs_main', buffers },
            fragment: { module: crystalModule, entryPoint: 'fs_main', targets: [{ format: this.format }] },
            primitive: { topology: 'triangle-list', cullMode: 'back' },
            depthStencil: { depthWriteEnabled: true, depthCompare: 'less', format: 'depth24plus' },
        });

        // Bubbles: transparent, no depth write, two passes (interior back
        // faces first, then exterior front faces) per bubble.wgsl's contract.
        const fullBubbleShader = commonShaderCode + '\n' + bubbleShaderCode;
        const bubbleModule = this.device.createShaderModule({ code: fullBubbleShader });
        const bubbleBlend: GPUBlendState = {
            color: { srcFactor: 'src-alpha', dstFactor: 'one-minus-src-alpha', operation: 'add' },
            alpha: { srcFactor: 'one', dstFactor: 'one-minus-src-alpha', operation: 'add' },
        };
        this.bubbleBackPipeline = this.device.createRenderPipeline({
            layout: cameraOnlyLayout,
            vertex: { module: bubbleModule, entryPoint: 'vs_main', buffers },
            fragment: { module: bubbleModule, entryPoint: 'fs_main', targets: [{ format: this.format, blend: bubbleBlend }] },
            primitive: { topology: 'triangle-list', cullMode: 'front' }, // Back faces
            depthStencil: { depthWriteEnabled: false, depthCompare: 'less', format: 'depth24plus' },
        });
        this.bubbleFrontPipeline = this.device.createRenderPipeline({
            layout: cameraOnlyLayout,
            vertex: { module: bubbleModule, entryPoint: 'vs_main', buffers },
            fragment: { module: bubbleModule, entryPoint: 'fs_main', targets: [{ format: this.format, blend: bubbleBlend }] },
            primitive: { topology: 'triangle-list', cullMode: 'back' }, // Front faces
            depthStencil: { depthWriteEnabled: false, depthCompare: 'less', format: 'depth24plus' },
        });

        // Outline post-process pipeline
        this.outlineBGL = this.device.createBindGroupLayout({
            entries: [
                { binding: 0, visibility: GPUShaderStage.VERTEX | GPUShaderStage.FRAGMENT, buffer: { type: 'uniform' } },
                { binding: 1, visibility: GPUShaderStage.FRAGMENT, texture: { sampleType: 'depth' } },
                { binding: 2, visibility: GPUShaderStage.FRAGMENT, sampler: { type: 'non-filtering' } },
            ],
        });
        const outlineLayout = this.device.createPipelineLayout({ bindGroupLayouts: [this.outlineBGL] });
        const outlineModule = this.device.createShaderModule({ code: outlineShaderCode });
        this.outlinePipeline = this.device.createRenderPipeline({
            layout: outlineLayout,
            vertex: { module: outlineModule, entryPoint: 'vs_main' },
            fragment: { 
                module: outlineModule, 
                entryPoint: 'fs_main', 
                targets: [{ 
                    format: this.format, 
                    blend: {
                        color: { srcFactor: 'src-alpha', dstFactor: 'one-minus-src-alpha', operation: 'add' },
                        alpha: { srcFactor: 'zero', dstFactor: 'one', operation: 'add' },
                    }
                }] 
            },
            primitive: { topology: 'triangle-list' },
        });

        const pickingModule = this.device.createShaderModule({ code: pickingShaderCode });
        this.pickingPipeline = this.device.createRenderPipeline({
            layout: cameraOnlyLayout,
            vertex: { module: pickingModule, entryPoint: 'vs_main', buffers },
            fragment: { module: pickingModule, entryPoint: 'fs_main', targets: [{ format: 'r32uint' }] },
            primitive: { topology: 'triangle-list', cullMode: 'back' },
            depthStencil: { depthWriteEnabled: true, depthCompare: 'less', format: 'depth24plus' },
        });
    }

    private setupTextures(): void {
        const size = [this.canvas.width, this.canvas.height];
        
        this.depthTexture = this.device.createTexture({
            size, format: 'depth24plus',
            usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.TEXTURE_BINDING,
        });
        
        this.pickingTexture = this.device.createTexture({
            size, format: 'r32uint',
            usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.COPY_SRC,
        });

        this.outlineBindGroup = this.device.createBindGroup({
            layout: this.outlineBGL,
            entries: [
                { binding: 0, resource: { buffer: this.cameraBuffers[0] } },
                { binding: 1, resource: this.depthTexture.createView() },
                { binding: 2, resource: this.nearestSampler },
            ],
        });
    }

    public resize(width: number, height: number): void {
        const w = Math.max(1, Math.floor(width));
        const h = Math.max(1, Math.floor(height));
        if (this.canvas.width === w && this.canvas.height === h) return;
        this.canvas.width = w;
        this.canvas.height = h;

        this.depthTexture?.destroy();
        this.pickingTexture?.destroy();
        this.setupTextures();
    }

    public async loadData(data: ArrayBuffer): Promise<void> {
        this.nodeCount = Math.floor(data.byteLength / NODE_STRIDE);
        if (this.nodeCount === 0) return;

        // Same integrity check as the WebGL2 renderer.
        const dv = new DataView(data);
        let hasRoot = false;
        for (let i = 0; i < this.nodeCount; i++) {
            if (dv.getUint32(i * NODE_STRIDE + 16, true) === NO_PARENT) { hasRoot = true; break; }
        }
        if (!hasRoot) throw new Error('Visualizer stream is malformed: no root node.');

        this.nav.loadData(data);

        // Worst-case capacity: every node visible.
        this.instanceBuffer?.destroy();
        this.instanceBuffer = this.device.createBuffer({
            size: Math.max(NODE_STRIDE, this.nodeCount * NODE_STRIDE),
            usage: GPUBufferUsage.VERTEX | GPUBufferUsage.COPY_DST,
        });

        this.visibleDirty = true;

        const rootIdx = this.nav.getRootIndex();
        const rootPos = this.nav.getPosition(rootIdx);
        if (rootPos) this.focusPosition = rootPos;
        const rootRadius = this.nav.getRadius(rootIdx);
        if (rootRadius > 0) this.zoom = Math.max(50, rootRadius * 2.5);
        this.isFirstFrame = true;
    }

    public markDirty(): void { this.visibleDirty = true; }

    public focusOnNode(sourceIndex: number): void {
        const p = this.nav.getPosition(sourceIndex);
        if (!p) return;
        const r = this.nav.getRadius(sourceIndex);
        this.focusPosition = p;
        this.zoom = Math.max(50, r * 2.5);
    }

    /** Recompact the visible set into the shared instance buffer.
     *  Crystal rows first, bubble rows after — see instanceBuffer docs. */
    private rebuildInstances(): void {
        if (!this.instanceBuffer) return;
        const v = this.nav.buildVisibleSet();
        this.crystalCount = v.crystalCount;
        this.bubbleCount = v.bubbleCount;
        this.crystalIndices = v.crystalIndices.slice();
        this.bubbleIndices = v.bubbleIndices.slice();

        if (v.crystalCount > 0) {
            this.device.queue.writeBuffer(
                this.instanceBuffer, 0,
                v.crystalData.buffer, v.crystalData.byteOffset, v.crystalData.byteLength,
            );
        }
        if (v.bubbleCount > 0) {
            this.device.queue.writeBuffer(
                this.instanceBuffer, v.crystalCount * NODE_STRIDE,
                v.bubbleData.buffer, v.bubbleData.byteOffset, v.bubbleData.byteLength,
            );
        }
        this.visibleDirty = false;
    }

    public handleMouseMove(dx: number, dy: number): void {
        this.rotationY -= dx * 0.005;
        this.rotationX += dy * 0.005;
        const EPS = 0.1;
        this.rotationX = Math.max(-Math.PI / 2 + EPS, Math.min(Math.PI / 2 - EPS, this.rotationX));
    }

    public handleZoom(delta: number): void {
        const speed = Math.max(10, this.zoom * 0.05);
        this.zoom = Math.max(5, this.zoom + (delta > 0 ? speed : -speed));
    }

    private updateCamera(): void {
        const aspect = this.canvas.width / this.canvas.height;
        const projection = this.perspective(45 * Math.PI / 180, aspect, 0.1, 100000);

        const t = this.focusPosition;
        const eyeX = t[0] + this.zoom * Math.cos(this.rotationX) * Math.sin(this.rotationY);
        const eyeY = t[1] + this.zoom * Math.sin(this.rotationX);
        const eyeZ = t[2] + this.zoom * Math.cos(this.rotationX) * Math.cos(this.rotationY);

        if (this.isFirstFrame) {
            this.cameraPosition = [eyeX, eyeY, eyeZ];
            this.isFirstFrame = false;
        } else {
            this.cameraPosition[0] += (eyeX - this.cameraPosition[0]) * 0.1;
            this.cameraPosition[1] += (eyeY - this.cameraPosition[1]) * 0.1;
            this.cameraPosition[2] += (eyeZ - this.cameraPosition[2]) * 0.1;
        }

        const view = this.lookAt(this.cameraPosition, [t[0], t[1], t[2]], [0, 1, 0]);
        const vpMatrix = this.multiply(projection, view);

        // Update all 3 camera variant buffers
        for (let i = 0; i < 3; i++) {
            // 28 floats = 112 bytes
            const uniformData = new Float32Array(28);
            uniformData.set(vpMatrix, 0);
            uniformData.set([this.cameraPosition[0], this.cameraPosition[1], this.cameraPosition[2]], 16);
            // currentVariant at byte offset 76 (index 19 as Float32 alias, better to write as Uint32)
            const uniformDataU32 = new Uint32Array(uniformData.buffer);
            uniformDataU32[19] = i;

            uniformData[20] = (performance.now() - this.startTime) / 1000; // time
            uniformData[21] = this.canvas.width;                           // screenWidth
            uniformData[22] = this.canvas.height;                          // screenHeight
            uniformData[23] = 0.0005;                                      // fogDensity
            uniformData.set([0.05, 0.03, 0.12, 0], 24);                    // fogColor (indigo) + _pad2

            this.device.queue.writeBuffer(this.cameraBuffers[i], 0, uniformData);
        }
    }

    public render(): void {
        if (!this.device) return;

        if (this.nodeCount === 0 || !this.instanceBuffer) {
            const encoder = this.device.createCommandEncoder();
            const clearPass = encoder.beginRenderPass({
                colorAttachments: [{
                    view: this.context.getCurrentTexture().createView(),
                    loadOp: 'clear',
                    clearValue: CLEAR_COLOR,
                    storeOp: 'store',
                }],
            });
            clearPass.end();
            this.device.queue.submit([encoder.finish()]);
            return;
        }

        if (this.visibleDirty) this.rebuildInstances();
        this.updateCamera();

        const currentTexture = this.context.getCurrentTexture();
        const encoder = this.device.createCommandEncoder();

        const pass = encoder.beginRenderPass({
            colorAttachments: [{
                view: currentTexture.createView(),
                loadOp: 'clear',
                clearValue: CLEAR_COLOR,
                storeOp: 'store',
            }],
            depthStencilAttachment: {
                view: this.depthTexture.createView(),
                depthClearValue: 1,
                depthLoadOp: 'clear',
                depthStoreOp: 'store',
            },
        });

        pass.setBindGroup(0, this.cameraBindGroups[0]);

        // 1) Opaque crystals (collapsed folders).
        if (this.crystalCount > 0) {
            pass.setPipeline(this.crystalPipeline);
            pass.setVertexBuffer(1, this.instanceBuffer, 0, this.crystalCount * NODE_STRIDE);
            
            for (let i = 0; i < 3; i++) {
                pass.setBindGroup(0, this.cameraBindGroups[i]);
                pass.setVertexBuffer(0, this.crystalMeshes[i].vertexBuffer);
                pass.setIndexBuffer(this.crystalMeshes[i].indexBuffer, 'uint16');
                pass.drawIndexed(this.crystalMeshes[i].indexCount, this.crystalCount);
            }
        }

        // 2) Transparent bubbles (files) — back faces then front faces.
        if (this.bubbleCount > 0) {
            pass.setBindGroup(0, this.cameraBindGroups[0]);
            pass.setVertexBuffer(0, this.bubbleMesh.vertexBuffer);
            pass.setVertexBuffer(1, this.instanceBuffer, this.crystalCount * NODE_STRIDE, this.bubbleCount * NODE_STRIDE);
            pass.setIndexBuffer(this.bubbleMesh.indexBuffer, 'uint16');

            pass.setPipeline(this.bubbleBackPipeline);
            pass.drawIndexed(this.bubbleMesh.indexCount, this.bubbleCount);

            pass.setPipeline(this.bubbleFrontPipeline);
            pass.drawIndexed(this.bubbleMesh.indexCount, this.bubbleCount);
        }

        pass.end();

        // 3) Outline post-process (optional)
        if (this.enableOutline) {
            const outlinePass = encoder.beginRenderPass({
                colorAttachments: [{
                    view: currentTexture.createView(),
                    loadOp: 'load',
                    storeOp: 'store',
                }],
            });
            outlinePass.setPipeline(this.outlinePipeline);
            outlinePass.setBindGroup(0, this.outlineBindGroup);
            outlinePass.draw(3, 1, 0, 0);
            outlinePass.end();
        }

        this.device.queue.submit([encoder.finish()]);
    }

    /**
     * GPU picking. Returns the ORIGINAL source-buffer node index (not the
     * compacted slot), or null if the click hit empty space.
     *
     * Crystals draw with firstInstance 0 into pick IDs [0, crystalCount);
     * bubbles draw with firstInstance = crystalCount so their IDs land in
     * [crystalCount, crystalCount + bubbleCount) AND the instance fetch
     * reads the bubble rows of the shared instance buffer. One buffer,
     * unique IDs, no rebinding.
     */
    public async pick(x: number, y: number): Promise<number | null> {
        if (!this.instanceBuffer) return null;
        if (this.visibleDirty) this.rebuildInstances();
        const total = this.crystalCount + this.bubbleCount;
        if (total === 0) return null;

        const px = Math.max(0, Math.min(Math.floor(x), this.canvas.width - 1));
        const py = Math.max(0, Math.min(Math.floor(y), this.canvas.height - 1));

        this.updateCamera();

        const encoder = this.device.createCommandEncoder();
        const pass = encoder.beginRenderPass({
            colorAttachments: [{
                view: this.pickingTexture.createView(),
                loadOp: 'clear',
                clearValue: { r: 0xFFFFFFFF, g: 0, b: 0, a: 0 },
                storeOp: 'store',
            }],
            depthStencilAttachment: {
                view: this.depthTexture.createView(),
                depthClearValue: 1,
                depthLoadOp: 'clear',
                depthStoreOp: 'store',
            },
        });

        pass.setPipeline(this.pickingPipeline);
        pass.setScissorRect(px, py, 1, 1);
        pass.setBindGroup(0, this.cameraBindGroups[0]);
        pass.setVertexBuffer(1, this.instanceBuffer);

        if (this.crystalCount > 0) {
            pass.setVertexBuffer(0, this.crystalMeshes[0].vertexBuffer);
            pass.setIndexBuffer(this.crystalMeshes[0].indexBuffer, 'uint16');
            pass.drawIndexed(this.crystalMeshes[0].indexCount, this.crystalCount, 0, 0, 0);
        }
        if (this.bubbleCount > 0) {
            pass.setVertexBuffer(0, this.bubbleMesh.vertexBuffer);
            pass.setIndexBuffer(this.bubbleMesh.indexBuffer, 'uint16');
            pass.drawIndexed(this.bubbleMesh.indexCount, this.bubbleCount, 0, 0, this.crystalCount);
        }
        pass.end();

        encoder.copyTextureToBuffer(
            { texture: this.pickingTexture, origin: [px, py, 0] },
            { buffer: this.pickBuffer, bytesPerRow: 256 },
            [1, 1, 1],
        );
        this.device.queue.submit([encoder.finish()]);

        await this.pickBuffer.mapAsync(GPUMapMode.READ);
        const id = new Uint32Array(this.pickBuffer.getMappedRange())[0];
        this.pickBuffer.unmap();

        if (id === 0xFFFFFFFF) return null;
        if (id < this.crystalCount) return this.crystalIndices[id];
        const bubbleSlot = id - this.crystalCount;
        if (bubbleSlot < this.bubbleCount) return this.bubbleIndices[bubbleSlot];
        return null;
    }

    // ── Matrix helpers ──────────────────────────────────────────────────

    private perspective(fovy: number, aspect: number, near: number, far: number): Float32Array {
        const f = 1 / Math.tan(fovy / 2);
        const out = new Float32Array(16);
        out[0] = f / aspect; out[5] = f; out[10] = far / (near - far); out[11] = -1; out[14] = (near * far) / (near - far);
        return out;
    }

    private lookAt(eye: number[], center: number[], up: number[]): Float32Array {
        const z = this.normalize(this.subtract(eye, center));
        const x = this.normalize(this.cross(up, z));
        const y = this.cross(z, x);
        const out = new Float32Array(16);
        out[0] = x[0]; out[4] = x[1]; out[8] = x[2]; out[12] = -this.dot(x, eye);
        out[1] = y[0]; out[5] = y[1]; out[9] = y[2]; out[13] = -this.dot(y, eye);
        out[2] = z[0]; out[6] = z[1]; out[10] = z[2]; out[14] = -this.dot(z, eye);
        out[3] = 0; out[7] = 0; out[11] = 0; out[15] = 1;
        return out;
    }

    private multiply(a: Float32Array, b: Float32Array): Float32Array {
        const out = new Float32Array(16);
        for (let col = 0; col < 4; col++) {
            for (let row = 0; row < 4; row++) {
                out[col * 4 + row] =
                    a[0 * 4 + row] * b[col * 4 + 0] +
                    a[1 * 4 + row] * b[col * 4 + 1] +
                    a[2 * 4 + row] * b[col * 4 + 2] +
                    a[3 * 4 + row] * b[col * 4 + 3];
            }
        }
        return out;
    }

    private subtract(a: number[], b: number[]): number[] { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
    private normalize(a: number[]): number[] {
        const len = Math.hypot(a[0], a[1], a[2]);
        if (len === 0) return [0, 0, 1];
        return [a[0] / len, a[1] / len, a[2] / len];
    }
    private cross(a: number[], b: number[]): number[] {
        return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    }
    private dot(a: number[], b: number[]): number { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

    public destroy(): void {
        this.depthTexture?.destroy();
        this.pickingTexture?.destroy();
        for (const b of this.cameraBuffers) b?.destroy();
        this.pickBuffer?.destroy();
        this.instanceBuffer?.destroy();
        for (const m of this.crystalMeshes) {
            m?.vertexBuffer.destroy();
            m?.indexBuffer.destroy();
        }
        this.bubbleMesh?.vertexBuffer.destroy();
        this.bubbleMesh?.indexBuffer.destroy();
        this.device?.destroy();
        this.context?.unconfigure();
    }
}
