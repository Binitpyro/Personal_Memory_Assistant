import { LinearBVH } from "../spatial/LinearBVH";

// We import WGSL as raw strings using Vite's supported ?raw query

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
    // Bypassing Culling
    // private visibleInstancesBuffer!: GPUBuffer;
    // private drawArgsBuffer!: GPUBuffer;
    // private cullingPipeline!: GPUComputePipeline;
    // private cullingBindGroup!: GPUBindGroup;

    private bubblePipeline!: GPURenderPipeline;
    private resolvePipeline!: GPURenderPipeline;

    private renderBindGroup!: GPUBindGroup;
    private resolveBindGroup!: GPUBindGroup;

    private bvh!: LinearBVH;

    private rotationX = 0.5;
    private rotationY = 0.5;
    private zoom = 250;

    constructor(canvas: HTMLCanvasElement) {
        this.canvas = canvas;
    }

    public async init() {
        if (!navigator.gpu) {
            throw new Error("WebGPU not supported on this browser.");
        }

        const adapter = await navigator.gpu.requestAdapter({ powerPreference: "high-performance" });
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
        await this.setupTextures();
        await this.setupPipelines();
    }

    private async createGeometryBuffer() {
        // Unit Cube: 36 vertices (6 faces * 2 triangles * 3 vertices)
        const v = new Float32Array([
            // +Z
            -0.5, -0.5,  0.5,  0, 0, 1,   0.5, -0.5,  0.5,  0, 0, 1,   0.5,  0.5,  0.5,  0, 0, 1,
            -0.5, -0.5,  0.5,  0, 0, 1,   0.5,  0.5,  0.5,  0, 0, 1,  -0.5,  0.5,  0.5,  0, 0, 1,
            // -Z
            -0.5, -0.5, -0.5,  0, 0,-1,   0.5,  0.5, -0.5,  0, 0,-1,   0.5, -0.5, -0.5,  0, 0,-1,
            -0.5, -0.5, -0.5,  0, 0,-1,  -0.5,  0.5, -0.5,  0, 0,-1,   0.5,  0.5, -0.5,  0, 0,-1,
            // +X
             0.5, -0.5, -0.5,  1, 0, 0,   0.5,  0.5,  0.5,  1, 0, 0,   0.5, -0.5,  0.5,  1, 0, 0,
             0.5, -0.5, -0.5,  1, 0, 0,   0.5,  0.5, -0.5,  1, 0, 0,   0.5,  0.5,  0.5,  1, 0, 0,
            // -X
            -0.5, -0.5, -0.5, -1, 0, 0,  -0.5, -0.5,  0.5, -1, 0, 0,  -0.5,  0.5,  0.5, -1, 0, 0,
            -0.5, -0.5, -0.5, -1, 0, 0,  -0.5,  0.5,  0.5, -1, 0, 0,  -0.5,  0.5, -0.5, -1, 0, 0,
            // +Y
            -0.5,  0.5, -0.5,  0, 1, 0,   0.5,  0.5,  0.5,  0, 1, 0,   0.5,  0.5, -0.5,  0, 1, 0,
            -0.5,  0.5, -0.5,  0, 1, 0,  -0.5,  0.5,  0.5,  0, 1, 0,   0.5,  0.5,  0.5,  0, 1, 0,
            // -Y
            -0.5, -0.5, -0.5,  0,-1, 0,   0.5, -0.5, -0.5,  0,-1, 0,   0.5, -0.5,  0.5,  0,-1, 0,
            -0.5, -0.5, -0.5,  0,-1, 0,   0.5, -0.5,  0.5,  0,-1, 0,  -0.5, -0.5,  0.5,  0,-1, 0,
        ]);

        this.geometryBuffer = this.device.createBuffer({
            size: v.byteLength,
            usage: GPUBufferUsage.VERTEX,
            mappedAtCreation: true
        });
        new Float32Array(this.geometryBuffer.getMappedRange()).set(v);
        this.geometryBuffer.unmap();
    }

    private async setupTextures() {
        const size = { width: this.canvas.width, height: this.canvas.height };
        this.momentTexture = this.device.createTexture({
            size, format: 'rgba32float',
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

    private async setupPipelines() {
        await this.bvh.initializePipelines();
        
        // Culling compute shader is bypassed
        // const cullModule = this.device.createShaderModule({ code: cullingShaderCode });
        // this.cullingPipeline = this.device.createComputePipeline({
        //     layout: 'auto', compute: { module: cullModule, entryPoint: "main" }
        // });

        const bubbleModule = this.device.createShaderModule({ code: bubbleShaderCode });
        this.bubblePipeline = this.device.createRenderPipeline({
            layout: 'auto',
            vertex: {
                module: bubbleModule, entryPoint: "vs_main",
                buffers: [
                    { arrayStride: 24, attributes: [{ shaderLocation: 0, offset: 0, format: 'float32x3' }, { shaderLocation: 1, offset: 12, format: 'float32x3' }] },
                    { arrayStride: 20, stepMode: 'instance', attributes: [{ shaderLocation: 2, offset: 0, format: 'float32x3' }, { shaderLocation: 3, offset: 12, format: 'float32' }, { shaderLocation: 4, offset: 16, format: 'uint32' }] },
                ]
            },
            fragment: {
                module: bubbleModule, entryPoint: "fs_main",
                targets: [{ format: 'rgba32float', blend: { color: { srcFactor: 'one', dstFactor: 'one', operation: 'add' }, alpha: { srcFactor: 'one', dstFactor: 'one', operation: 'add' } } },
                          { format: 'rgba16float', blend: { color: { srcFactor: 'one', dstFactor: 'one', operation: 'add' }, alpha: { srcFactor: 'one', dstFactor: 'one', operation: 'add' } } }]
            },
            primitive: { topology: 'triangle-list' },
            depthStencil: { depthWriteEnabled: false, depthCompare: 'less-equal', format: 'depth32float' },
        });

        const resolveModule = this.device.createShaderModule({ code: resolveShaderCode });
        this.resolvePipeline = this.device.createRenderPipeline({
            layout: 'auto',
            vertex: { module: resolveModule, entryPoint: "vs_main" },
            fragment: {
                module: resolveModule, entryPoint: "fs_main",
                targets: [{ format: this.format, blend: { color: { srcFactor: 'src-alpha', dstFactor: 'one-minus-src-alpha', operation: 'add' }, alpha: { srcFactor: 'one', dstFactor: 'one-minus-src-alpha', operation: 'add' } } }]
            }
        });
    }

    public async loadData(data: ArrayBuffer) {
        await this.bvh.uploadData(data);
        this.bvh.build();
        
        this.cameraBuffer = this.device.createBuffer({ size: 256, usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST });
        this.updateCamera();
        
        this.renderBindGroup = this.device.createBindGroup({ layout: this.bubblePipeline.getBindGroupLayout(0), entries: [{ binding: 0, resource: { buffer: this.cameraBuffer } }] });
        const sampler = this.device.createSampler();
        this.resolveBindGroup = this.device.createBindGroup({
            layout: this.resolvePipeline.getBindGroupLayout(0),
            entries: [{ binding: 0, resource: this.momentTexture.createView() }, { binding: 1, resource: this.colorTexture.createView() }, { binding: 2, resource: sampler }]
        });
    }

    public handleMouseMove(dx: number, dy: number) {
        this.rotationY -= dx * 0.01;
        this.rotationX -= dy * 0.01;
        this.rotationX = Math.max(-Math.PI / 2 + 0.1, Math.min(Math.PI / 2 - 0.1, this.rotationX));
    }

    public handleZoom(delta: number) {
        this.zoom = Math.max(10, Math.min(200, this.zoom + delta * 0.05));
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
        this.device.queue.writeBuffer(this.cameraBuffer, 64, new Float32Array(24)); // Planes
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
        for (let i = 0; i < 4; i++) {
            for (let j = 0; j < 4; j++) {
                out[i * 4 + j] = a[i * 4 + 0] * b[0 * 4 + j] + a[i * 4 + 1] * b[1 * 4 + j] + a[i * 4 + 2] * b[2 * 4 + j] + a[i * 4 + 3] * b[3 * 4 + j];
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
        if (!this.bvh.getElementCount() || !this.cameraBuffer) return;
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
        
        // Use the raw BVH element buffer directly
        renderPass.setVertexBuffer(1, this.bvh.getElementBuffer());
        
        // Direct draw: 36 vertices (cube), N instances
        renderPass.draw(36, this.bvh.getElementCount(), 0, 0);
        renderPass.end();

        const resolvePass = commandEncoder.beginRenderPass({
            colorAttachments: [{ view: this.context.getCurrentTexture().createView(), loadOp: 'clear', clearValue: [0.945, 0.96, 0.878, 1], storeOp: 'store' }]
        });
        resolvePass.setPipeline(this.resolvePipeline);
        resolvePass.setBindGroup(0, this.resolveBindGroup);
        resolvePass.draw(6);
        resolvePass.end();
        this.device.queue.submit([commandEncoder.finish()]);
    }
}
