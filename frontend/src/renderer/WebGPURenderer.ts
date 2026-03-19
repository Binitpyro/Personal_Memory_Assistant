import { LinearBVH } from "../spatial/LinearBVH";

import bubbleShaderCode from './shaders/bubble_mboit.wgsl?raw';
import resolveShaderCode from './shaders/oit_resolve.wgsl?raw';

export class WebGPURenderer {
    private readonly canvas: HTMLCanvasElement;
    private device!: GPUDevice;
    private context!: GPUCanvasContext;
    private format!: GPUTextureFormat;

    private momentTexture!: GPUTexture;
    private colorTexture!: GPUTexture;
    private depthTexture!: GPUTexture;

    private cameraBuffer!: GPUBuffer;
    private geometryBuffer!: GPUBuffer;

    private bubblePipeline!: GPURenderPipeline;
    private resolvePipeline!: GPURenderPipeline;

    private renderBindGroup!: GPUBindGroup;
    private resolveBindGroup!: GPUBindGroup;

    private bvh!: LinearBVH;

    private rotationX = 0.5;
    private rotationY = 0.5;
    private zoom = 550;

    constructor(canvas: HTMLCanvasElement) {
        this.canvas = canvas;
    }

    public async init() {
        if (!navigator.gpu) {
            throw new Error("WebGPU not supported on this browser.");
        }

        // Fix: Removed powerPreference to silence the Chrome/Windows warning
        const adapter = await navigator.gpu.requestAdapter();
        if (!adapter) {
            throw new Error("No appropriate GPUAdapter found.");
        }

        this.device = await adapter.requestDevice({
            requiredLimits: {
                maxStorageBufferBindingSize: adapter.limits.maxStorageBufferBindingSize,
                maxComputeWorkgroupStorageSize: adapter.limits.maxComputeWorkgroupStorageSize,
                maxBufferSize: adapter.limits.maxBufferSize,
            }
        });

        this.context = this.canvas.getContext('webgpu') as GPUCanvasContext;
        this.format = navigator.gpu.getPreferredCanvasFormat();

        this.context.configure({
            device: this.device,
            format: this.format,
            alphaMode: 'premultiplied',
        });

        this.bvh = new LinearBVH(this.device);

        await this.createGeometryBuffer();
        
        // Force initial size to at least 1x1 to prevent WebGPU crashes
        this.canvas.width = Math.max(1, this.canvas.clientWidth);
        this.canvas.height = Math.max(1, this.canvas.clientHeight);
        
        this.setupTextures(); // Made synchronous
        await this.setupPipelines();
    }

    private async createGeometryBuffer() {
        // IMPOSTOR MAGIC: We no longer pass heavy 3D positions.
        // Just a flat 2D square (Quad) from -1.0 to 1.0. (6 vertices, X and Y)
        const v = new Float32Array([
            -1.0, -1.0,   1.0, -1.0,  -1.0,  1.0,  // Triangle 1
            -1.0,  1.0,   1.0, -1.0,   1.0,  1.0,  // Triangle 2
        ]);

        this.geometryBuffer = this.device.createBuffer({
            size: v.byteLength,
            usage: GPUBufferUsage.VERTEX,
            mappedAtCreation: true
        });
        new Float32Array(this.geometryBuffer.getMappedRange()).set(v);
        this.geometryBuffer.unmap();
    }

    private setupTextures() {
        const size = { width: this.canvas.width, height: this.canvas.height };
        this.momentTexture = this.device.createTexture({
            size, format: 'rgba16float',
            usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.TEXTURE_BINDING
        });
        this.colorTexture = this.device.createTexture({
            size, format: 'rgba16float',
            usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.TEXTURE_BINDING
        });
        this.depthTexture = this.device.createTexture({
            size, format: 'depth32float',
            usage: GPUTextureUsage.RENDER_ATTACHMENT
        });
    }

    // FIX: Core resizing logic tied to the React ResizeObserver
    public resize(width: number, height: number) {
        const w = Math.max(1, width);
        const h = Math.max(1, height);
        
        if (this.canvas.width === w && this.canvas.height === h) return;
        
        this.canvas.width = w;
        this.canvas.height = h;

        if (this.momentTexture) this.momentTexture.destroy();
        if (this.colorTexture) this.colorTexture.destroy();
        if (this.depthTexture) this.depthTexture.destroy();

        this.setupTextures();

        // Must rebind the new textures so the shader can see them
        if (this.resolvePipeline) {
            this.resolveBindGroup = this.device.createBindGroup({
                layout: this.resolvePipeline.getBindGroupLayout(0),
                entries: [
                    { binding: 0, resource: this.momentTexture.createView() }, 
                    { binding: 1, resource: this.colorTexture.createView() }
                ]
            });
        }
    }

    private async setupPipelines() {
        await this.bvh.initializePipelines();
        
        const bubbleModule = this.device.createShaderModule({ code: bubbleShaderCode });
        this.bubblePipeline = this.device.createRenderPipeline({
            layout: 'auto',
            vertex: {
                module: bubbleModule, 
                entryPoint: "vs_main",
                buffers: [
                    { 
                        arrayStride: 8, 
                        attributes: [{ shaderLocation: 0, offset: 0, format: 'float32x2' }] 
                    },
                    { 
                        arrayStride: 20, 
                        stepMode: 'instance', 
                        attributes: [
                            { shaderLocation: 1, offset: 0, format: 'float32x3' },
                            { shaderLocation: 2, offset: 12, format: 'float32' },
                            { shaderLocation: 3, offset: 16, format: 'uint32' }
                        ] 
                    },
                ]
            },
            fragment: {
                module: bubbleModule, 
                entryPoint: "fs_main",
                targets: [
                    { 
                        format: 'rgba16float', 
                        blend: { 
                            color: { srcFactor: 'one', dstFactor: 'one', operation: 'add' }, 
                            alpha: { srcFactor: 'one', dstFactor: 'one', operation: 'add' } 
                        } 
                    },
                    { 
                        format: 'rgba16float', 
                        blend: { 
                            color: { srcFactor: 'one', dstFactor: 'one', operation: 'add' }, 
                            alpha: { srcFactor: 'one', dstFactor: 'one', operation: 'add' } 
                        } 
                    }
                ]
            },
            primitive: { topology: 'triangle-list' },
            depthStencil: { depthWriteEnabled: false, depthCompare: 'less-equal', format: 'depth32float' },
        });

        const resolveModule = this.device.createShaderModule({ code: resolveShaderCode });
        this.resolvePipeline = this.device.createRenderPipeline({
            layout: 'auto',
            vertex: { module: resolveModule, entryPoint: "vs_main" },
            fragment: {
                module: resolveModule, 
                entryPoint: "fs_main",
                targets: [{ 
                    format: this.format, 
                    blend: { 
                        color: { srcFactor: 'src-alpha', dstFactor: 'one-minus-src-alpha', operation: 'add' }, 
                        alpha: { srcFactor: 'one', dstFactor: 'one-minus-src-alpha', operation: 'add' } 
                    } 
                }]
            }
        });
    }

    public async loadData(data: ArrayBuffer) {
        await this.bvh.uploadData(data);
        this.bvh.build();
        
        this.cameraBuffer = this.device.createBuffer({ size: 256, usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST });
        this.updateCamera();
        
        this.renderBindGroup = this.device.createBindGroup({ layout: this.bubblePipeline.getBindGroupLayout(0), entries: [{ binding: 0, resource: { buffer: this.cameraBuffer } }] });
        
        this.resolveBindGroup = this.device.createBindGroup({
            layout: this.resolvePipeline.getBindGroupLayout(0),
            entries: [{ binding: 0, resource: this.momentTexture.createView() }, { binding: 1, resource: this.colorTexture.createView() }]
        });
    }

    public handleMouseMove(dx: number, dy: number) {
        this.rotationY -= dx * 0.01;
        this.rotationX -= dy * 0.01;
        this.rotationX = Math.max(-Math.PI / 2 + 0.1, Math.min(Math.PI / 2 - 0.1, this.rotationX));
    }

    public handleZoom(delta: number) {
        this.zoom = Math.max(10, Math.min(500, this.zoom + delta * 0.05));
    }

    private updateCamera() {
        const aspect = this.canvas.width / this.canvas.height;
        const projection = this.perspective(45 * Math.PI / 180, aspect, 0.1, 5000);
        const eyeX = this.zoom * Math.cos(this.rotationX) * Math.sin(this.rotationY);
        const eyeY = this.zoom * Math.sin(this.rotationX);
        const eyeZ = this.zoom * Math.cos(this.rotationX) * Math.cos(this.rotationY);
        const view = this.lookAt([eyeX, eyeY, eyeZ], [0, 0, 0], [0, 1, 0]);
        const vpMatrix = this.multiply(projection, view);
        this.device.queue.writeBuffer(this.cameraBuffer, 0, vpMatrix);
        this.device.queue.writeBuffer(this.cameraBuffer, 64, new Float32Array(24)); 
        this.device.queue.writeBuffer(this.cameraBuffer, 160, new Float32Array([eyeX, eyeY, eyeZ]));
    }

    private perspective(fovy: number, aspect: number, near: number, far: number) {
        const f = 1.0 / Math.tan(fovy / 2);
        const out = new Float32Array(16);
        out[0] = f / aspect; out[5] = f; out[10] = far / (near - far); out[11] = -1; out[14] = (near * far) / (near - far);
        return out;
    }

    private lookAt(eye: number[], center: number[], up: number[]) {
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

    private multiply(a: Float32Array, b: Float32Array) {
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

    private subtract(a: number[], b: number[]) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
    private normalize(a: number[]) {
        const len = Math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]);
        return [a[0] / len, a[1] / len, a[2] / len];
    }
    private cross(a: number[], b: number[]) {
        return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    }
    private dot(a: number[], b: number[]) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

    public render() {
        if (!this.bvh.getElementCount() || !this.cameraBuffer) {
            const commandEncoder = this.device.createCommandEncoder();
            const clearPass = commandEncoder.beginRenderPass({
                colorAttachments: [{ 
                    view: this.context.getCurrentTexture().createView(), 
                    loadOp: 'clear', 
                    clearValue: [0.945, 0.96, 0.878, 1], 
                    storeOp: 'store' 
                }]
            });
            clearPass.end();
            this.device.queue.submit([commandEncoder.finish()]);
            return;
        }

        this.updateCamera();
        
        const commandEncoder = this.device.createCommandEncoder();

        const renderPass = commandEncoder.beginRenderPass({
            colorAttachments: [{ view: this.momentTexture.createView(), loadOp: 'clear', clearValue: [0, 0, 0, 0], storeOp: 'store' },
                              { view: this.colorTexture.createView(), loadOp: 'clear', clearValue: [0, 0, 0, 0], storeOp: 'store' }],
            depthStencilAttachment: { view: this.depthTexture.createView(), depthClearValue: 1.0, depthLoadOp: 'clear', depthStoreOp: 'store' }
        });
        
        renderPass.setPipeline(this.bubblePipeline);
        renderPass.setBindGroup(0, this.renderBindGroup);
        renderPass.setVertexBuffer(0, this.geometryBuffer);
        renderPass.setVertexBuffer(1, this.bvh.getElementBuffer());
        
        // FIX 1: Change 36 to 6 (We are drawing 6-vertex squares now, not 36-vertex cubes!)
        renderPass.draw(6, this.bvh.getElementCount(), 0, 0);
        renderPass.end();

        const resolvePass = commandEncoder.beginRenderPass({
            colorAttachments: [{ view: this.context.getCurrentTexture().createView(), loadOp: 'clear', clearValue: [0.945, 0.96, 0.878, 1], storeOp: 'store' }]
        });
        resolvePass.setPipeline(this.resolvePipeline);
        resolvePass.setBindGroup(0, this.resolveBindGroup);
        
        // FIX 2: Change 6 to 3 (The Giant Triangle trick uses exactly 3 vertices)
        resolvePass.draw(3);
        resolvePass.end();
        
        this.device.queue.submit([commandEncoder.finish()]);
    }
    public destroy() {
        if (this.momentTexture) this.momentTexture.destroy();
        if (this.colorTexture) this.colorTexture.destroy();
        if (this.depthTexture) this.depthTexture.destroy();
        
        if (this.cameraBuffer) this.cameraBuffer.destroy();
        if (this.geometryBuffer) this.geometryBuffer.destroy();
        
        if (this.bvh && typeof this.bvh.destroy === 'function') {
            this.bvh.destroy();
        }

        if (this.device) this.device.destroy();
    }
}