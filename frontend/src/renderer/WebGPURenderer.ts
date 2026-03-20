import bubbleShaderCode from './shaders/bubble_mboit.wgsl?raw';
import resolveShaderCode from './shaders/oit_resolve.wgsl?raw';
import pickingShaderCode from './shaders/picking.wgsl?raw';

export class WebGPURenderer {
    private readonly canvas: HTMLCanvasElement;
    private device!: GPUDevice;
    private context!: GPUCanvasContext;
    private format!: GPUTextureFormat;

    private momentTexture!: GPUTexture;
    private colorTexture!: GPUTexture;
    private depthTexture!: GPUTexture;
    private pickingTexture!: GPUTexture;

    private cameraBuffer!: GPUBuffer;
    private geometryBuffer!: GPUBuffer;
    private pickBuffer!: GPUBuffer;

    private nodeBuffer?: GPUBuffer;

    private bubblePipeline!: GPURenderPipeline;
    private resolvePipeline!: GPURenderPipeline;
    private pickingPipeline!: GPURenderPipeline;

    private renderBindGroup!: GPUBindGroup;
    private resolveBindGroup!: GPUBindGroup;
    private pickingBindGroup?: GPUBindGroup;

    private nodeCount = 0;

    private rotationX = 0.5;
    private rotationY = 0.5;
    private zoom = 550;

    public focusPosition: number[] | null = null;
    public cameraPosition: number[] = [0, 0, 0];

    constructor(canvas: HTMLCanvasElement) {
        this.canvas = canvas;
    }

    public async init() {
        if (!navigator.gpu) {
            throw new Error("WebGPU not supported on this browser.");
        }

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

        this.pickBuffer = this.device.createBuffer({
            size: 256,
            usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ
        });

        await this.createGeometryBuffer();

        this.canvas.width = Math.max(1, this.canvas.clientWidth);
        this.canvas.height = Math.max(1, this.canvas.clientHeight);

        this.setupTextures();
        await this.setupPipelines();
    }

    private async createGeometryBuffer() {
        const v = new Float32Array([
            -1.0, -1.0, 1.0, -1.0, -1.0, 1.0,
            -1.0, 1.0, 1.0, -1.0, 1.0, 1.0,
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
        this.pickingTexture = this.device.createTexture({
            size, format: 'r32uint',
            usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.COPY_SRC
        });
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

    public resize(width: number, height: number) {
        const w = Math.max(1, width);
        const h = Math.max(1, height);

        if (this.canvas.width === w && this.canvas.height === h) return;

        this.canvas.width = w;
        this.canvas.height = h;

        if (this.momentTexture) this.momentTexture.destroy();
        if (this.colorTexture) this.colorTexture.destroy();
        if (this.depthTexture) this.depthTexture.destroy();
        if (this.pickingTexture) this.pickingTexture.destroy();

        this.setupTextures();
    }

    private async setupPipelines() {
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
                        arrayStride: 32, // matches the Rust Node layout
                        stepMode: 'instance',
                        attributes: [
                            { shaderLocation: 1, offset: 0, format: 'float32x3' }, // position
                            { shaderLocation: 2, offset: 12, format: 'float32' },  // radius
                            { shaderLocation: 3, offset: 20, format: 'uint32' },   // flags
                            { shaderLocation: 4, offset: 24, format: 'uint32' }    // type_hash
                        ]
                    }
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
            depthStencil: { depthWriteEnabled: true, depthCompare: 'less', format: 'depth32float' }, // WBOIT requires true depth to sort intersections properly
        });

        const pickingModule = this.device.createShaderModule({ code: pickingShaderCode });
        this.pickingPipeline = this.device.createRenderPipeline({
            layout: 'auto',
            vertex: {
                module: pickingModule,
                entryPoint: "vs_main",
                buffers: [
                    {
                        arrayStride: 8,
                        attributes: [{ shaderLocation: 0, offset: 0, format: 'float32x2' }]
                    },
                    {
                        arrayStride: 32,
                        stepMode: 'instance',
                        attributes: [
                            { shaderLocation: 1, offset: 0, format: 'float32x3' }, // position
                            { shaderLocation: 2, offset: 12, format: 'float32' },  // radius
                            { shaderLocation: 3, offset: 20, format: 'uint32' },   // flags
                            { shaderLocation: 4, offset: 24, format: 'uint32' }    // type_hash
                        ]
                    }
                ]
            },
            fragment: {
                module: pickingModule,
                entryPoint: "fs_main",
                targets: [{ format: 'r32uint' }]
            },
            primitive: { topology: 'triangle-list' },
            depthStencil: { depthWriteEnabled: true, depthCompare: 'less-equal', format: 'depth32float' }, // picking needs depth!
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
        this.nodeCount = Math.floor(data.byteLength / 32);
        if (this.nodeCount === 0) return;

        if (this.nodeBuffer) this.nodeBuffer.destroy();

        this.nodeBuffer = this.device.createBuffer({
            size: Math.max(32, data.byteLength),
            usage: GPUBufferUsage.VERTEX | GPUBufferUsage.COPY_DST,
        });
        if (data.byteLength > 0) {
            this.device.queue.writeBuffer(this.nodeBuffer, 0, data);
        }

        if (!this.cameraBuffer) {
            this.cameraBuffer = this.device.createBuffer({ size: 80, usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST });
        }
        this.updateCamera();

        this.renderBindGroup = this.device.createBindGroup({
            layout: this.bubblePipeline.getBindGroupLayout(0),
            entries: [
                { binding: 0, resource: { buffer: this.cameraBuffer } }
            ]
        });

        this.pickingBindGroup = this.device.createBindGroup({
            layout: this.pickingPipeline.getBindGroupLayout(0),
            entries: [
                { binding: 0, resource: { buffer: this.cameraBuffer } }
            ]
        });

        // Texture recreation was causing unnecessary GPU memory pressure, removed.
    }

    public async pick(x: number, y: number): Promise<number | null> {
        if (this.nodeCount === 0 || !this.cameraBuffer || !this.pickingTexture) return null;

        const px = Math.max(0, Math.min(Math.floor(x), this.canvas.width - 1));
        const py = Math.max(0, Math.min(Math.floor(y), this.canvas.height - 1));

        this.updateCamera();

        const commandEncoder = this.device.createCommandEncoder();



        const renderPass = commandEncoder.beginRenderPass({
            colorAttachments: [{
                view: this.pickingTexture.createView(),
                loadOp: 'clear',
                clearValue: { r: 0xFFFFFFFF, g: 0, b: 0, a: 0 },
                storeOp: 'store'
            }],
            depthStencilAttachment: { view: this.depthTexture.createView(), depthClearValue: 1.0, depthLoadOp: 'clear', depthStoreOp: 'store' }
        });

        renderPass.setPipeline(this.pickingPipeline);
        renderPass.setScissorRect(px, py, 1, 1);
        if (this.pickingBindGroup) renderPass.setBindGroup(0, this.pickingBindGroup);
        if (this.geometryBuffer) renderPass.setVertexBuffer(0, this.geometryBuffer);
        if (this.nodeBuffer) renderPass.setVertexBuffer(1, this.nodeBuffer);

        renderPass.draw(6, this.nodeCount, 0, 0);
        renderPass.end();

        commandEncoder.copyTextureToBuffer(
            { texture: this.pickingTexture, origin: [px, py, 0] },
            { buffer: this.pickBuffer, bytesPerRow: 256 },
            [1, 1, 1]
        );

        this.device.queue.submit([commandEncoder.finish()]);

        await this.pickBuffer.mapAsync(GPUMapMode.READ);
        const arrayBuffer = this.pickBuffer.getMappedRange();
        const data = new Uint32Array(arrayBuffer);
        const hash = data[0];

        // Copy out the data so we can unmap the buffer safely
        const result = hash === 0xFFFFFFFF ? null : hash;
        this.pickBuffer.unmap();

        return result;
    }

    public handleMouseMove(dx: number, dy: number) {
        this.rotationY -= dx * 0.01;
        this.rotationX -= dy * 0.01;
        this.rotationX = Math.max(-Math.PI / 2 + 0.1, Math.min(Math.PI / 2 - 0.1, this.rotationX));
    }

    public handleZoom(delta: number) {
        let speed = Math.max(10, this.zoom * 0.05);
        this.zoom = Math.max(5, this.zoom + (delta > 0 ? speed : -speed));
    }

    private updateCamera() {
        const aspect = this.canvas.width / this.canvas.height;
        const projection = this.perspective(45 * Math.PI / 180, aspect, 0.1, 100000);

        let target = this.focusPosition || [0, 0, 0];

        const eyeX = target[0] + this.zoom * Math.cos(this.rotationX) * Math.sin(this.rotationY);
        const eyeY = target[1] + this.zoom * Math.sin(this.rotationX);
        const eyeZ = target[2] + this.zoom * Math.cos(this.rotationX) * Math.cos(this.rotationY);

        // Fluid interpolation point
        this.cameraPosition[0] += (eyeX - this.cameraPosition[0]) * 0.1;
        this.cameraPosition[1] += (eyeY - this.cameraPosition[1]) * 0.1;
        this.cameraPosition[2] += (eyeZ - this.cameraPosition[2]) * 0.1;

        const view = this.lookAt(this.cameraPosition, target, [0, 1, 0]);
        const vpMatrix = this.multiply(projection, view);

        if (this.cameraBuffer) {
            // CameraUniform has:
            // mat4x4<f32> viewProj; // 64 bytes (16 floats)
            // vec3<f32> eyePosition; // 12 bytes + 4 bytes padding (4 floats)
            // Total = 80 bytes (20 floats)
            const uniformData = new Float32Array(20);
            uniformData.set(vpMatrix, 0);
            uniformData.set([this.cameraPosition[0], this.cameraPosition[1], this.cameraPosition[2], 0], 16);
            this.device.queue.writeBuffer(this.cameraBuffer, 0, uniformData);
        }
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
        if (len === 0) return [0, 0, 1];
        return [a[0] / len, a[1] / len, a[2] / len];
    }
    private cross(a: number[], b: number[]) {
        return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    }
    private dot(a: number[], b: number[]) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

    public render() {
        if (this.nodeCount === 0 || !this.cameraBuffer) {
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
        if (this.renderBindGroup) renderPass.setBindGroup(0, this.renderBindGroup);
        if (this.geometryBuffer) renderPass.setVertexBuffer(0, this.geometryBuffer);
        if (this.nodeBuffer) renderPass.setVertexBuffer(1, this.nodeBuffer);

        renderPass.draw(6, this.nodeCount, 0, 0);
        renderPass.end();

        const resolvePass = commandEncoder.beginRenderPass({
            colorAttachments: [{ view: this.context.getCurrentTexture().createView(), loadOp: 'clear', clearValue: [0.945, 0.96, 0.878, 1], storeOp: 'store' }]
        });
        resolvePass.setPipeline(this.resolvePipeline);
        if (this.resolveBindGroup) resolvePass.setBindGroup(0, this.resolveBindGroup);
        resolvePass.draw(3);
        resolvePass.end();

        this.device.queue.submit([commandEncoder.finish()]);
    }

    public destroy() {
        if (this.momentTexture) this.momentTexture.destroy();
        if (this.colorTexture) this.colorTexture.destroy();
        if (this.depthTexture) this.depthTexture.destroy();
        if (this.pickingTexture) this.pickingTexture.destroy();

        if (this.cameraBuffer) this.cameraBuffer.destroy();
        if (this.geometryBuffer) this.geometryBuffer.destroy();
        if (this.pickBuffer) this.pickBuffer.destroy();
        if (this.nodeBuffer) this.nodeBuffer.destroy();

        if (this.device) this.device.destroy();
        if (this.context) this.context.unconfigure();
    }
}