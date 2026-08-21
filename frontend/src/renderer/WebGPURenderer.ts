/**
 * WebGPURenderer.ts — "Aurora" build.
 *
 * Full render graph overhaul. Preserves the exact public API from the
 * legacy renderer (init / loadData / render / pick / resize / focusOnNode /
 * handleMouseMove / handleZoom / destroy / markDirty / nav / focusPosition)
 * so WebGPUFallback.tsx does not need to be touched.
 *
 * Preserved contracts:
 *   • 32-byte per-instance Node stride (see NavigationController.NODE_STRIDE).
 *   • Compacted instance buffer: crystals first, bubbles after. Picking IDs
 *     for bubbles use firstInstance = crystalCount so they don't collide.
 *   • Shared crystal pipeline across all CRYSTAL_VARIANTS meshes. Instances are
 *     counting-sorted by `type_hash % CRYSTAL_VARIANTS` in rebuildInstances so
 *     each variant is a contiguous run drawn with one firstInstance offset.
 *     crystalIndices is permuted with them — picking decodes through it.
 *
 * Upgrades:
 *   • Camera UBO grown from 112 → 192 bytes (adds invViewProj, focus,
 *     exposure). The layout matches common.wgsl exactly.
 *   • HDR linear pipeline: SceneColor / SceneColorPrev / Bloom / GodRays
 *     all rgba16f. Only the final tonemap writes to the sRGB swap-chain.
 *   • New passes: Sky, GPU particles (compute + additive draw),
 *     Weighted-Blended OIT (accum + reveal + resolve), radial god-rays,
 *     5-mip Kawase bloom, ACES tonemap composite with grain/vignette/CA.
 *
 * The render graph mirrors the diagram in the design doc:
 *   Sky → Crystals → Particles → Bubbles(OIT) → OIT-Resolve → GodRays
 *       → Bloom(down×5, up×5) → Tonemap → SwapChain → Copy(SceneColorPrev).
 */

import commonShaderCode      from './shaders/common.wgsl?raw';
import skyShaderCode         from './shaders/aurora_sky.wgsl?raw';
import crystalShaderCode     from './shaders/crystal.wgsl?raw';
import bubbleShaderCode      from './shaders/bubble.wgsl?raw';
import oitResolveShaderCode  from './shaders/oit_resolve.wgsl?raw';
import particlesUpdateCode   from './shaders/particles_update.wgsl?raw';
import particlesDrawCode     from './shaders/particles_draw.wgsl?raw';
import godRaysShaderCode     from './shaders/godrays.wgsl?raw';
import bloomShaderCode       from './shaders/bloom.wgsl?raw';
import tonemapShaderCode     from './shaders/tonemap.wgsl?raw';
import pickingShaderCode     from './shaders/picking.wgsl?raw';
import outlineShaderCode     from './shaders/outline.wgsl?raw';

import { generateCrystalVariants, CRYSTAL_VARIANTS, type MeshData } from './geometry/icosahedron';
import { generateIcosphereMulti } from './geometry/icosphere';
import { NavigationController, NODE_STRIDE, NODE_OFF_TYPE_HASH, NO_PARENT } from '../interaction/NavigationController';

/** Grown UBO — matches common.wgsl's CameraUniform (std140). */
const CAMERA_UNIFORM_SIZE = 192;

/**
 * Vertical field of view. Shared by the projection matrix and the
 * `projScaleY` UBO field so the two can never disagree — particles size
 * their billboards off that field, and a mismatch there is what produced
 * screen-filling sprites and a GPU hang.
 */
const FOV_Y = 45 * Math.PI / 180;

// HDR clear — SceneColor is rgba16f linear, sky pass overwrites everything.
const CLEAR_HDR: GPUColor = [0.0, 0.0, 0.0, 1.0];

// Particle system — 64k is comfortable on integrated GPUs and looks lush.
const PARTICLE_COUNT = 65_536;
const PARTICLE_STRIDE = 32; // vec3 pos + f32 life + vec3 vel + f32 seed
const SIM_PARAMS_SIZE = 48;

const BLOOM_MIPS = 5;

interface GpuMesh {
    vertexBuffer: GPUBuffer;
    indexBuffer:  GPUBuffer;
    indexCount:   number;
}

export class WebGPURenderer {
    private readonly canvas: HTMLCanvasElement;
    private device!: GPUDevice;
    private context!: GPUCanvasContext;
    private format!: GPUTextureFormat;

    // ── Geometry ────────────────────────────────────────────────────────
    private crystalMeshes: GpuMesh[] = [];
    private bubbleMesh!: GpuMesh; // near-LOD; renderer picks it per instance

    // ── Instance buffer ─────────────────────────────────────────────────
    private instanceBuffer?: GPUBuffer;
    private crystalCount = 0;
    private bubbleCount = 0;
    private crystalIndices: Uint32Array = new Uint32Array(0);
    private bubbleIndices: Uint32Array  = new Uint32Array(0);

    // ── Render targets ──────────────────────────────────────────────────
    private sceneColor!: GPUTexture;     // rgba16f — main HDR
    private sceneColorPrev!: GPUTexture; // rgba16f — refraction source
    private oitAccum!: GPUTexture;       // rgba16f
    private oitReveal!: GPUTexture;      // r8unorm
    private depthTex!: GPUTexture;       // depth32float — written as RenderAttachment
    private depthTexCopy!: GPUTexture;   // depth32float — read-only copy for TextureBinding
    private pickTex!: GPUTexture;        // r32uint
    private pickDepthTex!: GPUTexture;   // depth32float — dedicated picking depth target
    private godRaysMask!: GPUTexture;    // half-res r16f
    private godRaysBlur!: GPUTexture;    // half-res rgba16f
    private bloomMips: GPUTexture[] = []; // rgba16f, decreasing sizes
    private bloomUp: GPUTexture[] = [];   // rgba16f, upsample chain

    // ── Uniform / staging buffers ───────────────────────────────────────
    private cameraBuffers: GPUBuffer[] = [];
    /**
     * Prefix sums into the crystal region of the instance buffer, one entry per
     * mesh variant plus a terminator. rebuildInstances() sorts crystals by
     * `type_hash % CRYSTAL_VARIANTS` so each variant occupies one contiguous
     * run and can be drawn with a single firstInstance offset.
     */
    private crystalVariantOffsets = new Uint32Array(CRYSTAL_VARIANTS + 1);
    private pickBuffer!: GPUBuffer;
    private simParamsBuffer!: GPUBuffer;
    private particleBuffer!: GPUBuffer;
    private bloomParamBuffers: GPUBuffer[] = []; // one per pass in the chain

    // ── Bind group layouts / groups (kept explicit for clarity) ─────────
    private cameraBGL!: GPUBindGroupLayout;
    private cameraBindGroups: GPUBindGroup[] = [];

    private crystalSceneBGL!: GPUBindGroupLayout;
    private crystalSceneBindGroup!: GPUBindGroup;

    private oitResolveBGL!: GPUBindGroupLayout;
    private oitResolveBG!: GPUBindGroup;

    private simBGL!: GPUBindGroupLayout;
    private simBindGroup!: GPUBindGroup;

    private particleDrawBGL!: GPUBindGroupLayout;
    private particleDrawBG!: GPUBindGroup;

    private godRaysBGL!: GPUBindGroupLayout;
    private godRaysMaskBG!: GPUBindGroup;
    private godRaysBlurBG!: GPUBindGroup;

    private bloomDownBGL!: GPUBindGroupLayout;
    private bloomUpBGL!: GPUBindGroupLayout;
    private bloomDownBindGroups: GPUBindGroup[] = [];
    private bloomUpBindGroups: GPUBindGroup[] = [];

    private tonemapBGL!: GPUBindGroupLayout;
    private tonemapBG!: GPUBindGroup;

    private outlineBGL!: GPUBindGroupLayout;
    private outlineBG!: GPUBindGroup;

    // ── Pipelines ───────────────────────────────────────────────────────
    private skyPipeline!: GPURenderPipeline;
    private crystalPipeline!: GPURenderPipeline;
    private bubbleOitPipeline!: GPURenderPipeline;
    private oitResolvePipeline!: GPURenderPipeline;
    private simComputePipeline!: GPUComputePipeline;
    private particlesDrawPipeline!: GPURenderPipeline;
    private godRaysMaskPipeline!: GPURenderPipeline;
    private godRaysBlurPipeline!: GPURenderPipeline;
    private bloomPrefilterPipeline!: GPURenderPipeline;
    private bloomDownPipeline!: GPURenderPipeline;
    private bloomUpPipeline!: GPURenderPipeline;
    private tonemapPipeline!: GPURenderPipeline;
    private pickingPipeline!: GPURenderPipeline;
    private outlinePipeline!: GPURenderPipeline;

    // Samplers
    private linearSampler!: GPUSampler;
    private pointSampler!: GPUSampler;

    // ── Nav + camera state ──────────────────────────────────────────────
    public readonly nav = new NavigationController();
    public enableOutline = false; // Aurora relies on bloom / lighting, not ink outlines
    public exposure = 1.0;
    private nodeCount = 0;
    private visibleDirty = true;
    /**
     * Set once `device.lost` resolves. A lost device rejects every subsequent
     * submit, so the RAF loop must stop feeding it rather than pile up errors
     * behind a fault the user can no longer act on.
     */
    private deviceLost = false;

    private rotationX = 0.5;
    private rotationY = 0.5;
    private zoom = 550;
    public focusPosition: [number, number, number] = [0, 0, 0];
    private cameraPosition: [number, number, number] = [0, 0, 0];
    private isFirstFrame = true;
    private needsPrevFrameSeed = true; // seed SceneColorPrev from sky on first data render
    private readonly startTime = performance.now();
    private lastFrameTime = performance.now();

    public onDeviceLost?: () => void;

    constructor(canvas: HTMLCanvasElement) { this.canvas = canvas; }

    // ── Init ────────────────────────────────────────────────────────────
    public async init(): Promise<void> {
        if (!navigator.gpu) throw new Error('WebGPU not supported.');
        const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });
        if (!adapter) throw new Error('No appropriate GPUAdapter found.');
        this.device = await adapter.requestDevice();
        this.device.lost.then(info => {
            this.deviceLost = true;
            console.error('[Aurora] GPU device lost:', info.message);
            this.onDeviceLost?.();
        });

        this.context = this.canvas.getContext('webgpu') as GPUCanvasContext;
        this.format = navigator.gpu.getPreferredCanvasFormat();
        this.context.configure({
            device: this.device,
            format: this.format,
            alphaMode: 'premultiplied',
            usage: GPUTextureUsage.RENDER_ATTACHMENT, // tonemap writes here; no copy needed
        });

        // Device pixels, matching what `resize()` is fed by the ResizeObserver.
        // Seeding in CSS px left the very first frames under-resolved on any
        // display with DPR > 1 until the observer's first callback corrected it.
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        this.canvas.width  = Math.max(1, Math.round(this.canvas.clientWidth * dpr));
        this.canvas.height = Math.max(1, Math.round(this.canvas.clientHeight * dpr));

        // One camera UBO. There used to be three, existing solely to carry a
        // `currentVariant` discriminant that the crystal vertex shader compared
        // against; instances are now bucketed by variant on the CPU instead, so
        // the discriminant — and the two duplicate buffers — are gone.
        this.cameraBuffers.push(this.device.createBuffer({
            size: CAMERA_UNIFORM_SIZE,
            usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
        }));
        this.pickBuffer = this.device.createBuffer({
            size: 256, usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ,
        });
        this.simParamsBuffer = this.device.createBuffer({
            size: SIM_PARAMS_SIZE, usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
        });
        this.particleBuffer = this.device.createBuffer({
            size: PARTICLE_COUNT * PARTICLE_STRIDE,
            usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST,
        });
        // Zero-initialize — life = 0 forces respawn on first tick.
        this.device.queue.writeBuffer(
            this.particleBuffer, 0,
            new Float32Array(PARTICLE_COUNT * (PARTICLE_STRIDE / 4)),
        );

        // Geometry — CRYSTAL_VARIANTS cluster habits, 1 near-LOD icosphere for
        // bubbles. At 3 variants the shape repetition across the scene was
        // obvious; the meshes are small enough that more of them is ~free.
        this.crystalMeshes = generateCrystalVariants(CRYSTAL_VARIANTS).map(v => this.uploadMesh(v));
        const [near] = generateIcosphereMulti();
        this.bubbleMesh = this.uploadMesh(near);

        // Samplers
        this.linearSampler = this.device.createSampler({
            magFilter: 'linear', minFilter: 'linear', mipmapFilter: 'linear',
            addressModeU: 'clamp-to-edge', addressModeV: 'clamp-to-edge',
        });
        this.pointSampler = this.device.createSampler({
            magFilter: 'nearest', minFilter: 'nearest',
            addressModeU: 'clamp-to-edge', addressModeV: 'clamp-to-edge',
        });

        this.setupPipelines();
        this.setupTextures();
        this.rebuildBindGroups();
    }

    // ── Mesh upload ─────────────────────────────────────────────────────
    private uploadMesh(mesh: MeshData): GpuMesh {
        const vertexBuffer = this.device.createBuffer({
            size: mesh.vertices.byteLength,
            usage: GPUBufferUsage.VERTEX,
            mappedAtCreation: true,
        });
        new Float32Array(vertexBuffer.getMappedRange()).set(mesh.vertices);
        vertexBuffer.unmap();

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

    // ── Vertex layouts (unchanged 24 + 32 byte stride) ──────────────────
    private vertexLayouts(): GPUVertexBufferLayout[] {
        return [
            {
                arrayStride: 24,
                attributes: [
                    { shaderLocation: 0, offset: 0,  format: 'float32x3' },
                    { shaderLocation: 1, offset: 12, format: 'float32x3' },
                ],
            },
            {
                arrayStride: NODE_STRIDE,
                stepMode: 'instance',
                attributes: [
                    { shaderLocation: 2, offset: 0,  format: 'float32x3' },
                    { shaderLocation: 3, offset: 12, format: 'float32'   },
                    { shaderLocation: 4, offset: 16, format: 'uint32'    },
                    { shaderLocation: 5, offset: 20, format: 'uint32'    },
                    { shaderLocation: 6, offset: 24, format: 'uint32'    },
                    { shaderLocation: 7, offset: 28, format: 'uint32'    },
                ],
            },
        ];
    }

    // ── Pipeline creation ───────────────────────────────────────────────
    private setupPipelines(): void {
        // Bind group layouts
        this.cameraBGL = this.device.createBindGroupLayout({
            entries: [{
                binding: 0,
                visibility: GPUShaderStage.VERTEX | GPUShaderStage.FRAGMENT,
                buffer: { type: 'uniform', minBindingSize: CAMERA_UNIFORM_SIZE },
            }],
        });

        this.crystalSceneBGL = this.device.createBindGroupLayout({
            entries: [
                { binding: 0, visibility: GPUShaderStage.FRAGMENT, texture: { sampleType: 'float' } },
                { binding: 1, visibility: GPUShaderStage.FRAGMENT, texture: { sampleType: 'depth' } },
                { binding: 2, visibility: GPUShaderStage.FRAGMENT, sampler: { type: 'filtering' } },
            ],
        });

        this.oitResolveBGL = this.device.createBindGroupLayout({
            entries: [
                { binding: 0, visibility: GPUShaderStage.FRAGMENT, texture: { sampleType: 'float' } },
                { binding: 1, visibility: GPUShaderStage.FRAGMENT, texture: { sampleType: 'float' } },
                { binding: 2, visibility: GPUShaderStage.FRAGMENT, sampler: { type: 'filtering' } },
            ],
        });

        this.simBGL = this.device.createBindGroupLayout({
            entries: [
                { binding: 0, visibility: GPUShaderStage.COMPUTE, buffer: { type: 'storage' } },
                { binding: 1, visibility: GPUShaderStage.COMPUTE, buffer: { type: 'uniform' } },
            ],
        });

        this.particleDrawBGL = this.device.createBindGroupLayout({
            entries: [
                { binding: 0, visibility: GPUShaderStage.VERTEX | GPUShaderStage.FRAGMENT, buffer: { type: 'uniform' } },
                { binding: 1, visibility: GPUShaderStage.VERTEX, buffer: { type: 'read-only-storage' } },
                { binding: 2, visibility: GPUShaderStage.FRAGMENT, texture: { sampleType: 'depth' } },
                { binding: 3, visibility: GPUShaderStage.FRAGMENT, sampler: { type: 'non-filtering' } },
            ],
        });

        this.godRaysBGL = this.device.createBindGroupLayout({
            entries: [
                { binding: 0, visibility: GPUShaderStage.FRAGMENT, buffer: { type: 'uniform' } },
                { binding: 1, visibility: GPUShaderStage.FRAGMENT, texture: { sampleType: 'float' } },
                { binding: 2, visibility: GPUShaderStage.FRAGMENT, texture: { sampleType: 'depth' } },
                { binding: 3, visibility: GPUShaderStage.FRAGMENT, sampler: { type: 'non-filtering' } },
            ],
        });

        this.bloomDownBGL = this.device.createBindGroupLayout({
            entries: [
                { binding: 0, visibility: GPUShaderStage.FRAGMENT, texture: { sampleType: 'float' } },
                { binding: 1, visibility: GPUShaderStage.FRAGMENT, sampler: { type: 'filtering' } },
                { binding: 2, visibility: GPUShaderStage.FRAGMENT, buffer: { type: 'uniform' } },
            ],
        });

        this.bloomUpBGL = this.device.createBindGroupLayout({
            entries: [
                { binding: 0, visibility: GPUShaderStage.FRAGMENT, texture: { sampleType: 'float' } },
                { binding: 1, visibility: GPUShaderStage.FRAGMENT, sampler: { type: 'filtering' } },
                { binding: 2, visibility: GPUShaderStage.FRAGMENT, buffer: { type: 'uniform' } },
                { binding: 3, visibility: GPUShaderStage.FRAGMENT, texture: { sampleType: 'float' } },
            ],
        });

        this.tonemapBGL = this.device.createBindGroupLayout({
            entries: [
                { binding: 0, visibility: GPUShaderStage.FRAGMENT, buffer: { type: 'uniform' } },
                { binding: 1, visibility: GPUShaderStage.FRAGMENT, texture: { sampleType: 'float' } },
                { binding: 2, visibility: GPUShaderStage.FRAGMENT, texture: { sampleType: 'float' } },
                { binding: 3, visibility: GPUShaderStage.FRAGMENT, texture: { sampleType: 'float' } },
                { binding: 4, visibility: GPUShaderStage.FRAGMENT, sampler: { type: 'filtering' } },
            ],
        });

        this.outlineBGL = this.device.createBindGroupLayout({
            entries: [
                { binding: 0, visibility: GPUShaderStage.VERTEX | GPUShaderStage.FRAGMENT, buffer: { type: 'uniform' } },
                { binding: 1, visibility: GPUShaderStage.FRAGMENT, texture: { sampleType: 'depth' } },
                { binding: 2, visibility: GPUShaderStage.FRAGMENT, sampler: { type: 'non-filtering' } },
            ],
        });

        const cameraOnly = this.device.createPipelineLayout({ bindGroupLayouts: [this.cameraBGL] });
        const buffers = this.vertexLayouts();

        // ── Sky ─────────────────────────────────────────────────────────
        const skyModule = this.device.createShaderModule({ label: 'aurora-sky-module', code: commonShaderCode + '\n' + skyShaderCode });
        this.skyPipeline = this.device.createRenderPipeline({
            label: 'aurora-sky-pipeline',
            layout: cameraOnly,
            vertex:   { module: skyModule, entryPoint: 'vs_main' },
            fragment: { module: skyModule, entryPoint: 'fs_main',
                        targets: [{ format: 'rgba16float' }] },
            primitive: { topology: 'triangle-list' },
        });

        // ── Crystal (opaque, HDR, with prev-frame refraction) ───────────
        const crystalModule = this.device.createShaderModule({ label: 'aurora-crystal-module', code: commonShaderCode + '\n' + crystalShaderCode });
        const crystalPL = this.device.createPipelineLayout({
            label: 'aurora-crystal-layout',
            bindGroupLayouts: [this.cameraBGL, this.crystalSceneBGL],
        });
        this.crystalPipeline = this.device.createRenderPipeline({
            label: 'aurora-crystal-pipeline',
            layout: crystalPL,
            vertex:   { module: crystalModule, entryPoint: 'vs_main', buffers },
            fragment: { module: crystalModule, entryPoint: 'fs_main',
                        targets: [{ format: 'rgba16float' }] },
            primitive: { topology: 'triangle-list', cullMode: 'back' },
            depthStencil: { depthWriteEnabled: true, depthCompare: 'less', format: 'depth32float' },
        });

        // ── Bubbles (WBOIT — writes accum + reveal) ─────────────────────
        const bubbleModule = this.device.createShaderModule({ label: 'aurora-bubble-module', code: commonShaderCode + '\n' + bubbleShaderCode });
        this.bubbleOitPipeline = this.device.createRenderPipeline({
            label: 'aurora-bubble-pipeline',
            layout: cameraOnly,
            vertex:   { module: bubbleModule, entryPoint: 'vs_main', buffers },
            fragment: { module: bubbleModule, entryPoint: 'fs_main',
                        targets: [
                            { format: 'rgba16float',
                              blend: { color: { srcFactor: 'one', dstFactor: 'one', operation: 'add' },
                                       alpha: { srcFactor: 'one', dstFactor: 'one', operation: 'add' } } },
                            { format: 'r8unorm',
                              blend: { color: { srcFactor: 'zero', dstFactor: 'one-minus-src', operation: 'add' },
                                       alpha: { srcFactor: 'zero', dstFactor: 'one', operation: 'add' } } },
                        ] },
            primitive: { topology: 'triangle-list', cullMode: 'none' },
            depthStencil: { depthWriteEnabled: false, depthCompare: 'less', format: 'depth32float' },
        });

        // ── OIT resolve (blends into SceneColor) ────────────────────────
        const oitResolveModule = this.device.createShaderModule({ label: 'aurora-oit-resolve-module', code: commonShaderCode + '\n' + oitResolveShaderCode });
        this.oitResolvePipeline = this.device.createRenderPipeline({
            label: 'aurora-oit-resolve-pipeline',
            layout: this.device.createPipelineLayout({ bindGroupLayouts: [this.oitResolveBGL] }),
            vertex:   { module: oitResolveModule, entryPoint: 'vs_main' },
            fragment: { module: oitResolveModule, entryPoint: 'fs_main',
                        targets: [{ format: 'rgba16float',
                                    blend: { color: { srcFactor: 'src-alpha', dstFactor: 'one-minus-src-alpha', operation: 'add' },
                                             alpha: { srcFactor: 'one', dstFactor: 'one', operation: 'add' } } }] },
            primitive: { topology: 'triangle-list' },
        });

        // ── Particles: compute + additive draw ──────────────────────────
        const simModule = this.device.createShaderModule({ label: 'aurora-particles-sim-module', code: commonShaderCode + '\n' + particlesUpdateCode });
        this.simComputePipeline = this.device.createComputePipeline({
            label: 'aurora-particles-sim-pipeline',
            layout: this.device.createPipelineLayout({ bindGroupLayouts: [this.simBGL] }),
            compute: { module: simModule, entryPoint: 'cs_main' },
        });
        const drawModule = this.device.createShaderModule({ label: 'aurora-particles-draw-module', code: commonShaderCode + '\n' + particlesDrawCode });
        this.particlesDrawPipeline = this.device.createRenderPipeline({
            label: 'aurora-particles-draw-pipeline',
            layout: this.device.createPipelineLayout({ bindGroupLayouts: [this.particleDrawBGL] }),
            vertex:   { module: drawModule, entryPoint: 'vs_main' },
            fragment: { module: drawModule, entryPoint: 'fs_main',
                        targets: [{ format: 'rgba16float',
                                    blend: { color: { srcFactor: 'src-alpha', dstFactor: 'one', operation: 'add' },
                                             alpha: { srcFactor: 'zero', dstFactor: 'one', operation: 'add' } } }] },
            primitive: { topology: 'triangle-list' },
            depthStencil: { depthWriteEnabled: false, depthCompare: 'less', format: 'depth32float' },
        });

        // ── God-rays: mask + radial-blur ────────────────────────────────
        const godRaysModule = this.device.createShaderModule({ label: 'aurora-godrays-module', code: commonShaderCode + '\n' + godRaysShaderCode });
        const godRaysLayout = this.device.createPipelineLayout({ bindGroupLayouts: [this.godRaysBGL] });
        this.godRaysMaskPipeline = this.device.createRenderPipeline({
            label: 'aurora-godrays-mask-pipeline',
            layout: godRaysLayout,
            vertex:   { module: godRaysModule, entryPoint: 'vs_main' },
            fragment: { module: godRaysModule, entryPoint: 'fs_occlusion',
                        targets: [{ format: 'rgba16float' }] },
            primitive: { topology: 'triangle-list' },
        });
        this.godRaysBlurPipeline = this.device.createRenderPipeline({
            label: 'aurora-godrays-blur-pipeline',
            layout: godRaysLayout,
            vertex:   { module: godRaysModule, entryPoint: 'vs_main' },
            fragment: { module: godRaysModule, entryPoint: 'fs_radial',
                        targets: [{ format: 'rgba16float' }] },
            primitive: { topology: 'triangle-list' },
        });

        // ── Bloom chain — 3 entry points share one shader module ────────
        const bloomModule = this.device.createShaderModule({ label: 'aurora-bloom-module', code: commonShaderCode + '\n' + bloomShaderCode });
        const bloomDownPL = this.device.createPipelineLayout({ bindGroupLayouts: [this.bloomDownBGL] });
        const bloomUpPL = this.device.createPipelineLayout({ bindGroupLayouts: [this.bloomUpBGL] });
        this.bloomPrefilterPipeline = this.device.createRenderPipeline({
            label: 'aurora-bloom-prefilter-pipeline',
            layout: bloomDownPL,
            vertex:   { module: bloomModule, entryPoint: 'vs_main' },
            fragment: { module: bloomModule, entryPoint: 'fs_prefilter',
                        targets: [{ format: 'rgba16float' }] },
            primitive: { topology: 'triangle-list' },
        });
        this.bloomDownPipeline = this.device.createRenderPipeline({
            label: 'aurora-bloom-down-pipeline',
            layout: bloomDownPL,
            vertex:   { module: bloomModule, entryPoint: 'vs_main' },
            fragment: { module: bloomModule, entryPoint: 'fs_down',
                        targets: [{ format: 'rgba16float' }] },
            primitive: { topology: 'triangle-list' },
        });
        this.bloomUpPipeline = this.device.createRenderPipeline({
            label: 'aurora-bloom-up-pipeline',
            layout: bloomUpPL,
            vertex:   { module: bloomModule, entryPoint: 'vs_main' },
            fragment: { module: bloomModule, entryPoint: 'fs_up',
                        targets: [{ format: 'rgba16float' }] },
            primitive: { topology: 'triangle-list' },
        });

        // ── Tonemap (final, writes to swap-chain) ───────────────────────
        const tonemapModule = this.device.createShaderModule({ label: 'aurora-tonemap-module', code: commonShaderCode + '\n' + tonemapShaderCode });
        this.tonemapPipeline = this.device.createRenderPipeline({
            label: 'aurora-tonemap-pipeline',
            layout: this.device.createPipelineLayout({ bindGroupLayouts: [this.tonemapBGL] }),
            vertex:   { module: tonemapModule, entryPoint: 'vs_main' },
            fragment: { module: tonemapModule, entryPoint: 'fs_main',
                        targets: [{ format: this.format }] },
            primitive: { topology: 'triangle-list' },
        });

        // ── Picking (unchanged silhouette-tight pass) ───────────────────
        const pickingModule = this.device.createShaderModule({ label: 'aurora-picking-module', code: commonShaderCode + '\n' + pickingShaderCode });
        this.pickingPipeline = this.device.createRenderPipeline({
            label: 'aurora-picking-pipeline',
            layout: cameraOnly,
            vertex:   { module: pickingModule, entryPoint: 'vs_main', buffers },
            fragment: { module: pickingModule, entryPoint: 'fs_main',
                        targets: [{ format: 'r32uint' }] },
            primitive: { topology: 'triangle-list', cullMode: 'back' },
            depthStencil: { depthWriteEnabled: true, depthCompare: 'less', format: 'depth32float' },
        });

        // ── Outline (optional, additive blue-ink) ───────────────────────
        const outlineModule = this.device.createShaderModule({ label: 'aurora-outline-module', code: commonShaderCode + '\n' + outlineShaderCode });
        this.outlinePipeline = this.device.createRenderPipeline({
            label: 'aurora-outline-pipeline',
            layout: this.device.createPipelineLayout({ bindGroupLayouts: [this.outlineBGL] }),
            vertex:   { module: outlineModule, entryPoint: 'vs_main' },
            fragment: { module: outlineModule, entryPoint: 'fs_main',
                        targets: [{ format: this.format,
                                    blend: { color: { srcFactor: 'src-alpha', dstFactor: 'one-minus-src-alpha', operation: 'add' },
                                             alpha: { srcFactor: 'zero', dstFactor: 'one', operation: 'add' } } }] },
            primitive: { topology: 'triangle-list' },
        });

        this.cameraBindGroups = this.cameraBuffers.map(buffer => this.device.createBindGroup({
            layout: this.cameraBGL,
            entries: [{ binding: 0, resource: { buffer } }],
        }));

        // Allocate bloom param buffers (prefilter + down×N + up×N = 1 + 2N)
        for (let i = 0; i < 1 + 2 * BLOOM_MIPS; i++) {
            this.bloomParamBuffers.push(this.device.createBuffer({
                size: 32,
                usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
            }));
        }
    }

    // ── Textures — recreated on resize ──────────────────────────────────
    private setupTextures(): void {
        const W = this.canvas.width;
        const H = this.canvas.height;

        const mkColor = (w: number, h: number, format: GPUTextureFormat, extra: GPUTextureUsageFlags = 0) =>
            this.device.createTexture({
                size: [w, h], format,
                usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.TEXTURE_BINDING | extra,
            });

        this.sceneColor     = mkColor(W, H, 'rgba16float', GPUTextureUsage.COPY_SRC | GPUTextureUsage.COPY_DST);
        this.sceneColorPrev = mkColor(W, H, 'rgba16float', GPUTextureUsage.COPY_DST);
        this.oitAccum       = mkColor(W, H, 'rgba16float');
        this.oitReveal      = mkColor(W, H, 'r8unorm');

        this.depthTex = this.device.createTexture({
            size: [W, H], format: 'depth32float',
            usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.COPY_SRC,
        });
        // Separate read-only copy for sampling depth in fragment shaders.
        // This avoids the WebGPU validation error where a texture is both
        // RenderAttachment (writable) and TextureBinding (readable) in the
        // same synchronization scope.
        this.depthTexCopy = this.device.createTexture({
            size: [W, H], format: 'depth32float',
            usage: GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_DST,
        });
        this.pickTex = this.device.createTexture({
            size: [W, H], format: 'r32uint',
            usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.COPY_SRC,
        });
        this.pickDepthTex = this.device.createTexture({
            size: [W, H], format: 'depth32float',
            usage: GPUTextureUsage.RENDER_ATTACHMENT,
        });

        // Half-res god-ray targets.
        const HW = Math.max(1, W >> 1), HH = Math.max(1, H >> 1);
        this.godRaysMask = mkColor(HW, HH, 'rgba16float');
        this.godRaysBlur = mkColor(HW, HH, 'rgba16float');

        // Bloom pyramid — 5 mips, each ½ the previous.
        for (const t of this.bloomMips) t.destroy();
        for (const t of this.bloomUp) t.destroy();
        this.bloomMips = [];
        this.bloomUp = [];
        let bw = W, bh = H;
        for (let i = 0; i < BLOOM_MIPS; i++) {
            bw = Math.max(1, bw >> 1);
            bh = Math.max(1, bh >> 1);
            this.bloomMips.push(mkColor(bw, bh, 'rgba16float'));
            this.bloomUp.push(mkColor(bw, bh, 'rgba16float'));
        }
    }

    // Bind groups depend on textures — split so we can rebuild on resize.
    private rebuildBindGroups(): void {
        this.crystalSceneBindGroup = this.device.createBindGroup({
            layout: this.crystalSceneBGL,
            entries: [
                { binding: 0, resource: this.sceneColorPrev.createView() },
                { binding: 1, resource: this.depthTexCopy.createView() },
                { binding: 2, resource: this.linearSampler },
            ],
        });

        this.oitResolveBG = this.device.createBindGroup({
            layout: this.oitResolveBGL,
            entries: [
                { binding: 0, resource: this.oitAccum.createView() },
                { binding: 1, resource: this.oitReveal.createView() },
                { binding: 2, resource: this.linearSampler },
            ],
        });

        this.simBindGroup = this.device.createBindGroup({
            layout: this.simBGL,
            entries: [
                { binding: 0, resource: { buffer: this.particleBuffer } },
                { binding: 1, resource: { buffer: this.simParamsBuffer } },
            ],
        });

        this.particleDrawBG = this.device.createBindGroup({
            layout: this.particleDrawBGL,
            entries: [
                { binding: 0, resource: { buffer: this.cameraBuffers[0] } },
                { binding: 1, resource: { buffer: this.particleBuffer } },
                { binding: 2, resource: this.depthTexCopy.createView() },
                { binding: 3, resource: this.pointSampler },
            ],
        });

        this.godRaysMaskBG = this.device.createBindGroup({
            layout: this.godRaysBGL,
            entries: [
                { binding: 0, resource: { buffer: this.cameraBuffers[0] } },
                { binding: 1, resource: this.sceneColor.createView() },
                { binding: 2, resource: this.depthTexCopy.createView() },
                { binding: 3, resource: this.pointSampler },
            ],
        });
        this.godRaysBlurBG = this.device.createBindGroup({
            layout: this.godRaysBGL,
            entries: [
                { binding: 0, resource: { buffer: this.cameraBuffers[0] } },
                { binding: 1, resource: this.godRaysMask.createView() },
                { binding: 2, resource: this.depthTexCopy.createView() },
                { binding: 3, resource: this.pointSampler },
            ],
        });

        // Bloom bind groups: down chain + up chain
        this.bloomDownBindGroups = [];
        // 0 — prefilter
        this.bloomDownBindGroups.push(this.device.createBindGroup({
            layout: this.bloomDownBGL,
            entries: [
                { binding: 0, resource: this.sceneColor.createView() },
                { binding: 1, resource: this.linearSampler },
                { binding: 2, resource: { buffer: this.bloomParamBuffers[0] } },
            ],
        }));
        // 1..4 — down: mip i reads mip (i-1)
        for (let i = 1; i < BLOOM_MIPS; i++) {
            this.bloomDownBindGroups.push(this.device.createBindGroup({
                layout: this.bloomDownBGL,
                entries: [
                    { binding: 0, resource: this.bloomMips[i - 1].createView() },
                    { binding: 1, resource: this.linearSampler },
                    { binding: 2, resource: { buffer: this.bloomParamBuffers[i] } },
                ],
            }));
        }
        // Up chain — writes into bloomUp[i], takes src (smaller up or smallest down) and base_mip (same-size down)
        this.bloomUpBindGroups = [];
        for (let i = 0; i < BLOOM_MIPS; i++) {
            const srcView = i === BLOOM_MIPS - 1
                ? this.bloomMips[BLOOM_MIPS - 1].createView()
                : this.bloomUp[i + 1].createView();
            const baseView = this.bloomMips[i].createView();
            this.bloomUpBindGroups.push(this.device.createBindGroup({
                layout: this.bloomUpBGL,
                entries: [
                    { binding: 0, resource: srcView },
                    { binding: 1, resource: this.linearSampler },
                    { binding: 2, resource: { buffer: this.bloomParamBuffers[BLOOM_MIPS + i] } },
                    { binding: 3, resource: baseView },
                ],
            }));
        }

        this.tonemapBG = this.device.createBindGroup({
            layout: this.tonemapBGL,
            entries: [
                { binding: 0, resource: { buffer: this.cameraBuffers[0] } },
                { binding: 1, resource: this.sceneColor.createView() },
                { binding: 2, resource: this.bloomUp[0].createView() },
                { binding: 3, resource: this.godRaysBlur.createView() },
                { binding: 4, resource: this.linearSampler },
            ],
        });

        this.outlineBG = this.device.createBindGroup({
            layout: this.outlineBGL,
            entries: [
                { binding: 0, resource: { buffer: this.cameraBuffers[0] } },
                { binding: 1, resource: this.depthTexCopy.createView() },
                { binding: 2, resource: this.pointSampler },
            ],
        });
    }

    public resize(width: number, height: number): void {
        const w = Math.max(1, Math.floor(width));
        const h = Math.max(1, Math.floor(height));
        if (this.canvas.width === w && this.canvas.height === h) return;
        this.canvas.width  = w;
        this.canvas.height = h;

        // Destroy all size-dependent textures.
        this.sceneColor?.destroy();
        this.sceneColorPrev?.destroy();
        this.oitAccum?.destroy();
        this.oitReveal?.destroy();
        this.depthTex?.destroy();
        this.depthTexCopy?.destroy();
        this.pickTex?.destroy();
        this.godRaysMask?.destroy();
        this.godRaysBlur?.destroy();

        this.setupTextures();
        this.rebuildBindGroups();
    }

    // ── Data ────────────────────────────────────────────────────────────
    public async loadData(data: ArrayBuffer): Promise<void> {
        this.nodeCount = Math.floor(data.byteLength / NODE_STRIDE);
        if (this.nodeCount === 0) return;

        const dv = new DataView(data);
        let hasRoot = false;
        for (let i = 0; i < this.nodeCount; i++) {
            if (dv.getUint32(i * NODE_STRIDE + 16, true) === NO_PARENT) { hasRoot = true; break; }
        }
        if (!hasRoot) throw new Error('Visualizer stream is malformed: no root node.');

        this.nav.loadData(data);

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

    private rebuildInstances(): void {
        if (!this.instanceBuffer) return;
        const v = this.nav.buildVisibleSet();
        this.crystalCount   = v.crystalCount;
        this.bubbleCount    = v.bubbleCount;
        this.bubbleIndices  = v.bubbleIndices.slice();

        if (v.crystalCount > 0) {
            // Counting sort by mesh variant, so each variant is one contiguous
            // run that a single draw can address via firstInstance.
            //
            // crystalIndices MUST be permuted in lockstep: the picking pass
            // writes @builtin(instance_index) as the pick id and pick() maps it
            // back through crystalIndices, so a mismatch silently selects the
            // wrong folder rather than failing loudly.
            //
            // Reordering crystals is otherwise safe — buildVisibleSet's
            // pre-order only matters for back-to-front bubble OIT, and crystals
            // are opaque and depth-tested.
            const N = CRYSTAL_VARIANTS;
            const src = v.crystalData;
            const dv = new DataView(src.buffer, src.byteOffset, src.byteLength);

            const bucket = new Uint8Array(v.crystalCount);
            const counts = new Uint32Array(N);
            for (let i = 0; i < v.crystalCount; i++) {
                const b = dv.getUint32(i * NODE_STRIDE + NODE_OFF_TYPE_HASH, true) % N;
                bucket[i] = b;
                counts[b]++;
            }

            const offsets = this.crystalVariantOffsets;
            offsets[0] = 0;
            for (let b = 0; b < N; b++) offsets[b + 1] = offsets[b] + counts[b];

            const cursor = offsets.slice(0, N);
            const sorted = new Uint8Array(v.crystalCount * NODE_STRIDE);
            const idx    = new Uint32Array(v.crystalCount);
            for (let i = 0; i < v.crystalCount; i++) {
                const dst = cursor[bucket[i]]++;
                sorted.set(src.subarray(i * NODE_STRIDE, (i + 1) * NODE_STRIDE), dst * NODE_STRIDE);
                idx[dst] = v.crystalIndices[i];
            }
            this.crystalIndices = idx;
            this.device.queue.writeBuffer(this.instanceBuffer, 0,
                sorted.buffer, sorted.byteOffset, sorted.byteLength);
        } else {
            this.crystalIndices = new Uint32Array(0);
            this.crystalVariantOffsets.fill(0);
        }
        if (v.bubbleCount > 0) {
            this.device.queue.writeBuffer(this.instanceBuffer, v.crystalCount * NODE_STRIDE,
                v.bubbleData.buffer, v.bubbleData.byteOffset, v.bubbleData.byteLength);
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
        const projection = this.perspective(FOV_Y, aspect, 0.1, 100000);

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
        const view = this.lookAt(this.cameraPosition, t, [0, 1, 0]);
        const vp   = this.multiply(projection, view);
        const invVp = this.invert4x4(vp);

        // Pack 192-byte UBO — 48 floats. See common.wgsl CameraUniform.
        const u = new Float32Array(48);
        const u32 = new Uint32Array(u.buffer);
        u.set(vp, 0);              // 16
        u.set(invVp, 16);          // 16
        u.set(this.cameraPosition, 32); // 3
        // u32[35] = variant (written per-variant below)
        const timeSec = (performance.now() - this.startTime) / 1000;
        u[36] = timeSec;
        u[37] = this.canvas.width;
        u[38] = this.canvas.height;
        u[39] = 0.000008; // fog density — tuned for typical zoom distances (200-2000)
        u.set([0.16, 0.10, 0.30], 40); // fogColor — matches sky horizon tones
        u[43] = this.exposure;
        u.set(t, 44); // focus
        // projScaleY — P[1][1]. particles_draw.wgsl scales its billboards by
        // this; it must be the PROJECTION term only, never viewProj's.
        u[47] = 1 / Math.tan(FOV_Y / 2);

        // u32[35] is now dead padding (was `currentVariant`). It is kept in the
        // struct so the 192-byte layout — and every offset after it — is
        // unchanged for the other eleven shader modules.
        u32[35] = 0;
        this.device.queue.writeBuffer(this.cameraBuffers[0], 0, u);
    }

    private updateSimParams(dtSec: number): void {
        const buf = new Float32Array(SIM_PARAMS_SIZE / 4);
        buf[0] = Math.min(dtSec, 0.05); // clamp long frames so integrator stays stable
        buf[1] = (performance.now() - this.startTime) / 1000;
        // buf[2] and buf[3] are _pad0 (vec2<f32>) for 16-byte alignment of focus
        buf[4] = this.focusPosition[0];
        buf[5] = this.focusPosition[1];
        buf[6] = this.focusPosition[2];
        buf[7] = Math.max(80, this.zoom * 0.6); // spawn radius scales with zoom @ offset 28
        this.device.queue.writeBuffer(this.simParamsBuffer, 0, buf);
    }

    // Params for a bloom pass. `dst` is the render target of THIS pass.
    private writeBloomParams(idx: number, dst: { width: number; height: number },
                             threshold: number, knee: number, intensity: number) {
        const b = new Float32Array(8);
        b[0] = threshold; b[1] = knee; b[2] = intensity; b[3] = 0;
        b[4] = 1 / dst.width; b[5] = 1 / dst.height;
        this.device.queue.writeBuffer(this.bloomParamBuffers[idx], 0, b);
    }

    // ── The main event ──────────────────────────────────────────────────
    public render(): void {
        if (!this.device || this.deviceLost) return;

        // Empty scene fast-path: still draw the sky, so the user gets the
        // dreamscape backdrop even before data arrives.
        if (this.nodeCount === 0 || !this.instanceBuffer) {
            this.renderEmpty();
            return;
        }

        if (this.visibleDirty) this.rebuildInstances();
        const now = performance.now();
        const dt = (now - this.lastFrameTime) / 1000;
        this.lastFrameTime = now;

        this.updateCamera();
        this.updateSimParams(dt);

        const encoder = this.device.createCommandEncoder();

        // ── 0. Compute — advect particles ────────────────────────────────
        {
            const cp = encoder.beginComputePass();
            cp.setPipeline(this.simComputePipeline);
            cp.setBindGroup(0, this.simBindGroup);
            cp.dispatchWorkgroups(Math.ceil(PARTICLE_COUNT / 64));
            cp.end();
        }

        const sceneView = this.sceneColor.createView();
        const depthView = this.depthTex.createView();

        // ── 1. Sky (fills SceneColor; also clears depth) ────────────────
        {
            const p = encoder.beginRenderPass({
                colorAttachments: [{ view: sceneView, loadOp: 'clear', clearValue: CLEAR_HDR, storeOp: 'store' }],
            });
            p.setPipeline(this.skyPipeline);
            p.setBindGroup(0, this.cameraBindGroups[0]);
            p.draw(3);
            p.end();
        }
        // Need a fresh depth clear for the geometry passes.
        {
            const p = encoder.beginRenderPass({
                colorAttachments: [],
                depthStencilAttachment: {
                    view: depthView, depthClearValue: 1,
                    depthLoadOp: 'clear', depthStoreOp: 'store',
                },
            });
            p.end();
        }

        // On the first data frame, seed SceneColorPrev with the sky so the
        // crystal refraction shader samples the sky gradient instead of black.
        if (this.needsPrevFrameSeed) {
            encoder.copyTextureToTexture(
                { texture: this.sceneColor }, { texture: this.sceneColorPrev },
                [this.canvas.width, this.canvas.height, 1],
            );
            this.needsPrevFrameSeed = false;
        }

        // Copy depth → depthTexCopy so fragment shaders can sample it
        // without conflicting with the RenderAttachment write.
        encoder.copyTextureToTexture(
            { texture: this.depthTex, aspect: 'depth-only' },
            { texture: this.depthTexCopy, aspect: 'depth-only' },
            [this.canvas.width, this.canvas.height, 1],
        );

        // ── 2. Crystals (opaque, HDR, samples prev-frame) ───────────────
        if (this.crystalCount > 0) {
            const p = encoder.beginRenderPass({
                colorAttachments: [{ view: sceneView, loadOp: 'load', storeOp: 'store' }],
                depthStencilAttachment: {
                    view: depthView, depthLoadOp: 'load', depthStoreOp: 'store',
                },
            });
            p.setPipeline(this.crystalPipeline);
            p.setBindGroup(0, this.cameraBindGroups[0]);
            p.setBindGroup(1, this.crystalSceneBindGroup);
            p.setVertexBuffer(1, this.instanceBuffer, 0, this.crystalCount * NODE_STRIDE);
            // One draw per variant over its own contiguous run. Previously each
            // of 3 draws submitted the FULL crystal count and the vertex shader
            // discarded 2/3 by pushing them outside clip space — which does not
            // scale to CRYSTAL_VARIANTS meshes. Empty buckets are common on
            // small folders, so skip them.
            const off = this.crystalVariantOffsets;
            for (let v = 0; v < CRYSTAL_VARIANTS; v++) {
                const count = off[v + 1] - off[v];
                if (count === 0) continue;
                p.setVertexBuffer(0, this.crystalMeshes[v].vertexBuffer);
                p.setIndexBuffer(this.crystalMeshes[v].indexBuffer, 'uint16');
                p.drawIndexed(this.crystalMeshes[v].indexCount, count, 0, 0, off[v]);
            }
            p.end();
        }

        // Refresh depthTexCopy after crystals wrote new depth data.
        encoder.copyTextureToTexture(
            { texture: this.depthTex, aspect: 'depth-only' },
            { texture: this.depthTexCopy, aspect: 'depth-only' },
            [this.canvas.width, this.canvas.height, 1],
        );

        // ── 3. Particles (additive, depth-test but no write) ────────────
        {
            const p = encoder.beginRenderPass({
                colorAttachments: [{ view: sceneView, loadOp: 'load', storeOp: 'store' }],
                depthStencilAttachment: {
                    view: depthView, depthLoadOp: 'load', depthStoreOp: 'store',
                },
            });
            p.setPipeline(this.particlesDrawPipeline);
            p.setBindGroup(0, this.particleDrawBG);
            p.draw(PARTICLE_COUNT * 6);
            p.end();
        }

        // ── 4. Bubbles → WBOIT (accum + reveal) ─────────────────────────
        if (this.bubbleCount > 0) {
            const p = encoder.beginRenderPass({
                colorAttachments: [
                    { view: this.oitAccum.createView(),  loadOp: 'clear', clearValue: [0,0,0,0], storeOp: 'store' },
                    { view: this.oitReveal.createView(), loadOp: 'clear', clearValue: [1,0,0,0], storeOp: 'store' },
                ],
                depthStencilAttachment: {
                    view: depthView, depthLoadOp: 'load', depthStoreOp: 'store',
                },
            });
            p.setPipeline(this.bubbleOitPipeline);
            p.setBindGroup(0, this.cameraBindGroups[0]);
            p.setVertexBuffer(0, this.bubbleMesh.vertexBuffer);
            p.setVertexBuffer(1, this.instanceBuffer, this.crystalCount * NODE_STRIDE, this.bubbleCount * NODE_STRIDE);
            p.setIndexBuffer(this.bubbleMesh.indexBuffer, 'uint16');
            p.drawIndexed(this.bubbleMesh.indexCount, this.bubbleCount);
            p.end();
        }

        // ── 5. OIT resolve — blend accum/reveal back into SceneColor ────
        if (this.bubbleCount > 0) {
            const p = encoder.beginRenderPass({
                colorAttachments: [{ view: sceneView, loadOp: 'load', storeOp: 'store' }],
            });
            p.setPipeline(this.oitResolvePipeline);
            p.setBindGroup(0, this.oitResolveBG);
            p.draw(3);
            p.end();
        }

        // Refresh depthTexCopy for god-rays and outline passes that sample depth.
        encoder.copyTextureToTexture(
            { texture: this.depthTex, aspect: 'depth-only' },
            { texture: this.depthTexCopy, aspect: 'depth-only' },
            [this.canvas.width, this.canvas.height, 1],
        );

        // ── 6. God-rays — mask, then radial blur ────────────────────────
        {
            const p = encoder.beginRenderPass({
                colorAttachments: [{ view: this.godRaysMask.createView(), loadOp: 'clear', clearValue: [0,0,0,0], storeOp: 'store' }],
            });
            p.setPipeline(this.godRaysMaskPipeline);
            p.setBindGroup(0, this.godRaysMaskBG);
            p.draw(3);
            p.end();

            const q = encoder.beginRenderPass({
                colorAttachments: [{ view: this.godRaysBlur.createView(), loadOp: 'clear', clearValue: [0,0,0,0], storeOp: 'store' }],
            });
            q.setPipeline(this.godRaysBlurPipeline);
            q.setBindGroup(0, this.godRaysBlurBG);
            q.draw(3);
            q.end();
        }

        // ── 7. Bloom chain ──────────────────────────────────────────────
        // Prefilter: sceneColor → bloomMips[0]
        this.writeBloomParams(0, this.dimsOf(this.bloomMips[0]), 1.05, 0.35, 1.0);
        {
            const p = encoder.beginRenderPass({
                colorAttachments: [{ view: this.bloomMips[0].createView(), loadOp: 'clear', clearValue: [0,0,0,0], storeOp: 'store' }],
            });
            p.setPipeline(this.bloomPrefilterPipeline);
            p.setBindGroup(0, this.bloomDownBindGroups[0]);
            p.draw(3);
            p.end();
        }
        // Down: bloomMips[i-1] → bloomMips[i]
        for (let i = 1; i < BLOOM_MIPS; i++) {
            this.writeBloomParams(i, this.dimsOf(this.bloomMips[i]), 0, 0, 1);
            const p = encoder.beginRenderPass({
                colorAttachments: [{ view: this.bloomMips[i].createView(), loadOp: 'clear', clearValue: [0,0,0,0], storeOp: 'store' }],
            });
            p.setPipeline(this.bloomDownPipeline);
            p.setBindGroup(0, this.bloomDownBindGroups[i]);
            p.draw(3);
            p.end();
        }
        // Up: shader computes base_mip + tent(src) * intensity
        for (let i = BLOOM_MIPS - 1; i >= 0; i--) {
            const dst = this.bloomUp[i];
            this.writeBloomParams(BLOOM_MIPS + i, this.dimsOf(dst), 0, 0, 0.75);
            const p = encoder.beginRenderPass({
                colorAttachments: [{
                    view: dst.createView(),
                    loadOp: 'clear',
                    clearValue: [0,0,0,0],
                    storeOp: 'store',
                }],
            });
            p.setPipeline(this.bloomUpPipeline);
            p.setBindGroup(0, this.bloomUpBindGroups[i]);
            p.draw(3);
            p.end();
        }

        // ── 8. Tonemap composite → swap-chain ───────────────────────────
        const swap = this.context.getCurrentTexture();
        {
            const p = encoder.beginRenderPass({
                colorAttachments: [{ view: swap.createView(), loadOp: 'clear', clearValue: [0,0,0,1], storeOp: 'store' }],
            });
            p.setPipeline(this.tonemapPipeline);
            p.setBindGroup(0, this.tonemapBG);
            p.draw(3);
            p.end();
        }

        // ── 9. Optional outline (over the composited swap-chain) ────────
        if (this.enableOutline) {
            const p = encoder.beginRenderPass({
                colorAttachments: [{ view: swap.createView(), loadOp: 'load', storeOp: 'store' }],
            });
            p.setPipeline(this.outlinePipeline);
            p.setBindGroup(0, this.outlineBG);
            p.draw(3);
            p.end();
        }

        // ── 10. Snapshot SceneColor → SceneColorPrev for next frame's crystals
        encoder.copyTextureToTexture(
            { texture: this.sceneColor }, { texture: this.sceneColorPrev },
            [this.canvas.width, this.canvas.height, 1],
        );

        this.device.queue.submit([encoder.finish()]);
    }

    private renderEmpty(): void {
        this.updateCamera();
        const encoder = this.device.createCommandEncoder();
        const sceneView = this.sceneColor.createView();
        {
            const p = encoder.beginRenderPass({
                colorAttachments: [{ view: sceneView, loadOp: 'clear', clearValue: CLEAR_HDR, storeOp: 'store' }],
            });
            p.setPipeline(this.skyPipeline);
            p.setBindGroup(0, this.cameraBindGroups[0]);
            p.draw(3);
            p.end();
        }
        // Cheap tonemap — bloom/godrays targets stay empty (their clears run in render()).
        this.writeBloomParams(0, this.dimsOf(this.bloomMips[0]), 100.0, 0.1, 0); // effectively kills bloom
        const swap = this.context.getCurrentTexture();
        {
            const p = encoder.beginRenderPass({
                colorAttachments: [{ view: swap.createView(), loadOp: 'clear', clearValue: [0,0,0,1], storeOp: 'store' }],
            });
            p.setPipeline(this.tonemapPipeline);
            p.setBindGroup(0, this.tonemapBG);
            p.draw(3);
            p.end();
        }
        this.device.queue.submit([encoder.finish()]);
    }

    private dimsOf(t: GPUTexture): { width: number; height: number } {
        return { width: t.width, height: t.height };
    }

    // ── Picking (unchanged from legacy — silhouette-tight) ──────────────
    public async pick(x: number, y: number): Promise<number | null> {
        if (!this.instanceBuffer || this.deviceLost) return null;
        if (this.visibleDirty) this.rebuildInstances();
        const total = this.crystalCount + this.bubbleCount;
        if (total === 0) return null;

        // Callers pass CSS pixels (clientX minus getBoundingClientRect), but the
        // pick texture is sized in device pixels. Scale by the canvas's own
        // backing-store ratio rather than window.devicePixelRatio: that stays
        // correct even where the DPR used for sizing was clamped.
        const sx = this.canvas.clientWidth > 0 ? this.canvas.width / this.canvas.clientWidth : 1;
        const sy = this.canvas.clientHeight > 0 ? this.canvas.height / this.canvas.clientHeight : 1;
        const px = Math.max(0, Math.min(Math.floor(x * sx), this.canvas.width - 1));
        const py = Math.max(0, Math.min(Math.floor(y * sy), this.canvas.height - 1));

        // Note: updateCamera() is not called here so picking does not advance camera smoothing lerp
        const encoder = this.device.createCommandEncoder();
        const pass = encoder.beginRenderPass({
            colorAttachments: [{
                view: this.pickTex.createView(),
                loadOp: 'clear', clearValue: { r: 0xFFFFFFFF, g: 0, b: 0, a: 0 }, storeOp: 'store',
            }],
            depthStencilAttachment: {
                view: this.pickDepthTex.createView(),
                depthClearValue: 1, depthLoadOp: 'clear', depthStoreOp: 'store',
            },
        });
        pass.setPipeline(this.pickingPipeline);
        pass.setScissorRect(px, py, 1, 1);
        pass.setBindGroup(0, this.cameraBindGroups[0]);
        pass.setVertexBuffer(1, this.instanceBuffer);

        // Per-variant, matching the colour pass. This used to draw variant 0's
        // mesh for EVERY crystal, so two thirds of them were picked against the
        // wrong silhouette; the bucketing makes the correct mesh free to use.
        // firstInstance keeps @builtin(instance_index) equal to the global slot,
        // which is what pick_id decodes against below.
        const pOff = this.crystalVariantOffsets;
        for (let v = 0; v < CRYSTAL_VARIANTS; v++) {
            const count = pOff[v + 1] - pOff[v];
            if (count === 0) continue;
            pass.setVertexBuffer(0, this.crystalMeshes[v].vertexBuffer);
            pass.setIndexBuffer(this.crystalMeshes[v].indexBuffer, 'uint16');
            pass.drawIndexed(this.crystalMeshes[v].indexCount, count, 0, 0, pOff[v]);
        }
        if (this.bubbleCount > 0) {
            pass.setVertexBuffer(0, this.bubbleMesh.vertexBuffer);
            pass.setIndexBuffer(this.bubbleMesh.indexBuffer, 'uint16');
            pass.drawIndexed(this.bubbleMesh.indexCount, this.bubbleCount, 0, 0, this.crystalCount);
        }
        pass.end();

        encoder.copyTextureToBuffer(
            { texture: this.pickTex, origin: [px, py, 0] },
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
        out[0] = f / aspect; out[5] = f;
        out[10] = far / (near - far); out[11] = -1;
        out[14] = (near * far) / (near - far);
        return out;
    }
    private lookAt(eye: number[], center: number[], up: number[]): Float32Array {
        const z = this.norm3(this.sub3(eye, center));
        const x = this.norm3(this.cross3(up, z));
        const y = this.cross3(z, x);
        const out = new Float32Array(16);
        out[0] = x[0]; out[4] = x[1]; out[8]  = x[2]; out[12] = -this.dot3(x, eye);
        out[1] = y[0]; out[5] = y[1]; out[9]  = y[2]; out[13] = -this.dot3(y, eye);
        out[2] = z[0]; out[6] = z[1]; out[10] = z[2]; out[14] = -this.dot3(z, eye);
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
    /** Full 4×4 inverse — needed for invViewProj in the sky pass. */
    private invert4x4(m: Float32Array): Float32Array {
        const inv = new Float32Array(16);
        inv[0]  =  m[5]*m[10]*m[15] - m[5]*m[11]*m[14] - m[9]*m[6]*m[15] + m[9]*m[7]*m[14] + m[13]*m[6]*m[11] - m[13]*m[7]*m[10];
        inv[4]  = -m[4]*m[10]*m[15] + m[4]*m[11]*m[14] + m[8]*m[6]*m[15] - m[8]*m[7]*m[14] - m[12]*m[6]*m[11] + m[12]*m[7]*m[10];
        inv[8]  =  m[4]*m[9] *m[15] - m[4]*m[11]*m[13] - m[8]*m[5]*m[15] + m[8]*m[7]*m[13] + m[12]*m[5]*m[11] - m[12]*m[7]*m[9];
        inv[12] = -m[4]*m[9] *m[14] + m[4]*m[10]*m[13] + m[8]*m[5]*m[14] - m[8]*m[6]*m[13] - m[12]*m[5]*m[10] + m[12]*m[6]*m[9];
        inv[1]  = -m[1]*m[10]*m[15] + m[1]*m[11]*m[14] + m[9]*m[2]*m[15] - m[9]*m[3]*m[14] - m[13]*m[2]*m[11] + m[13]*m[3]*m[10];
        inv[5]  =  m[0]*m[10]*m[15] - m[0]*m[11]*m[14] - m[8]*m[2]*m[15] + m[8]*m[3]*m[14] + m[12]*m[2]*m[11] - m[12]*m[3]*m[10];
        inv[9]  = -m[0]*m[9] *m[15] + m[0]*m[11]*m[13] + m[8]*m[1]*m[15] - m[8]*m[3]*m[13] - m[12]*m[1]*m[11] + m[12]*m[3]*m[9];
        inv[13] =  m[0]*m[9] *m[14] - m[0]*m[10]*m[13] - m[8]*m[1]*m[14] + m[8]*m[2]*m[13] + m[12]*m[1]*m[10] - m[12]*m[2]*m[9];
        inv[2]  =  m[1]*m[6] *m[15] - m[1]*m[7] *m[14] - m[5]*m[2]*m[15] + m[5]*m[3]*m[14] + m[13]*m[2]*m[7]  - m[13]*m[3]*m[6];
        inv[6]  = -m[0]*m[6] *m[15] + m[0]*m[7] *m[14] + m[4]*m[2]*m[15] - m[4]*m[3]*m[14] - m[12]*m[2]*m[7]  + m[12]*m[3]*m[6];
        inv[10] =  m[0]*m[5] *m[15] - m[0]*m[7] *m[13] - m[4]*m[1]*m[15] + m[4]*m[3]*m[13] + m[12]*m[1]*m[7]  - m[12]*m[3]*m[5];
        inv[14] = -m[0]*m[5] *m[14] + m[0]*m[6] *m[13] + m[4]*m[1]*m[14] - m[4]*m[2]*m[13] - m[12]*m[1]*m[6]  + m[12]*m[2]*m[5];
        inv[3]  = -m[1]*m[6] *m[11] + m[1]*m[7] *m[10] + m[5]*m[2]*m[11] - m[5]*m[3]*m[10] - m[9] *m[2]*m[7]  + m[9] *m[3]*m[6];
        inv[7]  =  m[0]*m[6] *m[11] - m[0]*m[7] *m[10] - m[4]*m[2]*m[11] + m[4]*m[3]*m[10] + m[8] *m[2]*m[7]  - m[8] *m[3]*m[6];
        inv[11] = -m[0]*m[5] *m[11] + m[0]*m[7] *m[9]  + m[4]*m[1]*m[11] - m[4]*m[3]*m[9]  - m[8] *m[1]*m[7]  + m[8] *m[3]*m[5];
        inv[15] =  m[0]*m[5] *m[10] - m[0]*m[6] *m[9]  - m[4]*m[1]*m[10] + m[4]*m[2]*m[9]  + m[8] *m[1]*m[6]  - m[8] *m[2]*m[5];
        let det = m[0]*inv[0] + m[1]*inv[4] + m[2]*inv[8] + m[3]*inv[12];
        if (Math.abs(det) < 1e-9) return new Float32Array(16); // degenerate — caller unlikely to hit
        det = 1.0 / det;
        for (let i = 0; i < 16; i++) inv[i] = inv[i] * det;
        return inv;
    }
    private sub3(a: number[], b: number[]): number[] { return [a[0]-b[0], a[1]-b[1], a[2]-b[2]]; }
    private norm3(a: number[]): number[] {
        const l = Math.hypot(a[0], a[1], a[2]);
        if (l === 0) return [0, 0, 1];
        return [a[0]/l, a[1]/l, a[2]/l];
    }
    private cross3(a: number[], b: number[]): number[] {
        return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
    }
    private dot3(a: number[], b: number[]): number { return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]; }

    public destroy(): void {
        this.sceneColor?.destroy();
        this.sceneColorPrev?.destroy();
        this.oitAccum?.destroy();
        this.oitReveal?.destroy();
        this.depthTex?.destroy();
        this.depthTexCopy?.destroy();
        this.pickTex?.destroy();
        this.pickDepthTex?.destroy();
        this.godRaysMask?.destroy();
        this.godRaysBlur?.destroy();
        for (const t of this.bloomMips) t.destroy();
        for (const t of this.bloomUp)   t.destroy();
        for (const b of this.cameraBuffers) b?.destroy();
        for (const b of this.bloomParamBuffers) b?.destroy();
        this.pickBuffer?.destroy();
        this.simParamsBuffer?.destroy();
        this.particleBuffer?.destroy();
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
