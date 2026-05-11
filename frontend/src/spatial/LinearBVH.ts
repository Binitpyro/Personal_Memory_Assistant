/**
 * LinearBVH.ts
 * Implements a Linear Bounding Volume Hierarchy (LBVH) mapped to WebGPU Compute Shaders.
 * For 4M+ elements, we must perform spatial sorting and hierarchy construction entirely on the GPU.
 *
 * ⚠️ STATUS: INCOMPLETE — GPU spatial queries do NOT currently execute.
 * L-16: Morton code generation (Pass 1) is implemented and correct. However:
 *   - Pass 2 (Parallel Radix Sort on GPU) is missing — mortonCodeBuffer remains unsorted.
 *   - Pass 3 (LBVH Tree Construction from sorted codes) is missing — bvhNodeBuffer is allocated
 *     but never populated with actual BVH nodes.
 *   - uploadData() is cut off; it sets up buffers but the consumer never calls build().
 *   - MortonEncoding.ts feeds into this file and is also effectively dead.
 *
 * TODO: Implement GPU radix sort (e.g. decoupled look-back algorithm) for Pass 2,
 * then implement the Karras 2012 LBVH construction algorithm for Pass 3.
 * Until then, this class must NOT be used for production spatial queries.
 */

export class LinearBVH {
    private readonly device: GPUDevice;
    private numElements: number = 0;

    // WebGPU Buffers
    private elementBuffer!: GPUBuffer;     // [x, y, z, size, typeHash]
    private mortonCodeBuffer!: GPUBuffer;  // [morton_code, original_index]
    private bvhNodeBuffer!: GPUBuffer;     // Array of BVH Nodes

    // Pipelines
    private mortonPipeline!: GPUComputePipeline;


    private sortPipeline!: GPUComputePipeline;
    private bvhPipeline!: GPUComputePipeline;

    constructor(device: GPUDevice) {
        this.device = device;
    }

    public async initializePipelines() {
        // 1. Morton Code Generation Shader (already existing)
        const mortonShader = this.device.createShaderModule({
            label: "Morton Code Generation",
            code: `
                struct Element {
                    pos: vec3<f32>,
                    size: f32,
                    typeHash: u32,
                };
                
                struct MortonEntry {
                    code: u32,
                    index: u32,
                };

                @group(0) @binding(0) var<storage, read> elements: array<Element>;
                @group(0) @binding(1) var<storage, read_write> mortonCodes: array<MortonEntry>;
                
                fn expandBits(vIn: u32) -> u32 {
                    var v = vIn;
                    v = (v * 0x00010001u) & 0xFF0000FFu;
                    v = (v * 0x00000101u) & 0x0F00F00Fu;
                    v = (v * 0x00000011u) & 0xC30C30C3u;
                    v = (v * 0x00000005u) & 0x49249249u;
                    return v;
                }

                @compute @workgroup_size(256)
                fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
                    let idx = global_id.x;
                    if (idx >= arrayLength(&elements)) { return; }
                    
                    let e = elements[idx];
                    let p = clamp(e.pos, vec3<f32>(0.0), vec3<f32>(1.0));
                    
                    let xx = expandBits(u32(p.x * 1023.0));
                    let yy = expandBits(u32(p.y * 1023.0));
                    let zz = expandBits(u32(p.z * 1023.0));
                    
                    let code = (xx * 4u) + (yy * 2u) + zz;
                    mortonCodes[idx] = MortonEntry(code, idx);
                }
            `
        });

        // 2. Simple Bitonic Sort Shader (O(n log^2 n)) for Pass 2
        const sortShader = this.device.createShaderModule({
            label: "Bitonic Sort",
            code: `
                struct MortonEntry {
                    code: u32,
                    index: u32,
                };

                struct SortUniforms {
                    p: u32,
                    q: u32,
                };

                @group(0) @binding(0) var<storage, read_write> mortonCodes: array<MortonEntry>;
                @group(0) @binding(1) var<uniform> uniforms: SortUniforms;

                @compute @workgroup_size(256)
                fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
                    let i = global_id.x;
                    let j = i ^ uniforms.p;
                    
                    if (j > i) {
                        if ((i & uniforms.q) == 0u) {
                            if (mortonCodes[i].code > mortonCodes[j].code) {
                                let temp = mortonCodes[i];
                                mortonCodes[i] = mortonCodes[j];
                                mortonCodes[j] = temp;
                            }
                        } else {
                            if (mortonCodes[i].code < mortonCodes[j].code) {
                                let temp = mortonCodes[i];
                                mortonCodes[i] = mortonCodes[j];
                                mortonCodes[j] = temp;
                            }
                        }
                    }
                }
            `
        });

        // 3. Karras 2012 LBVH Construction Shader
        const bvhShader = this.device.createShaderModule({
            label: "LBVH Construction",
            code: `
                struct MortonEntry {
                    code: u32,
                    index: u32,
                };

                struct Node {
                    min: vec3<f32>,
                    left: u32,
                    max: vec3<f32>,
                    right: u32,
                };

                @group(0) @binding(0) var<storage, read> mortonCodes: array<MortonEntry>;
                @group(0) @binding(1) var<storage, read_write> nodes: array<Node>;

                fn delta(i: i32, j: i32, n: i32) -> i32 {
                    if (j < 0 || j >= n) { return -1; }
                    let code_i = mortonCodes[i].code;
                    let code_j = mortonCodes[j].code;
                    if (code_i == code_j) { return 32 + countLeadingZeros(u32(i ^ j)); }
                    return countLeadingZeros(code_i ^ code_j);
                }

                @compute @workgroup_size(256)
                fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
                    let i = i32(global_id.x);
                    let n = i32(arrayLength(&mortonCodes));
                    if (i >= n - 1) { return; }

                    // Determine direction of the range
                    let d = select(-1, 1, delta(i, i + 1, n) - delta(i, i - 1, n) > 0);
                    
                    // Compute upper bound for the length of the range
                    let delta_min = delta(i, i - d, n);
                    var l_max = 2;
                    while (delta(i, i + l_max * d, n) > delta_min) {
                        l_max = l_max * 2;
                    }

                    // Find the other end using binary search
                    var l = 0;
                    var t = l_max / 2;
                    while (t > 0) {
                        if (delta(i, i + (l + t) * d, n) > delta_min) {
                            l = l + t;
                        }
                        t = t / 2;
                    }
                    let j = i + l * d;

                    // Find the split point using binary search
                    let delta_node = delta(i, j, n);
                    var s = 0;
                    var t2 = (l + 1) / 2;
                    while (t2 > 0) {
                        if (delta(i, i + (s + t2) * d, n) > delta_node) {
                            s = s + t2;
                        }
                        t2 = t2 / 2;
                    }
                    let gamma = i + s * d + min(d, 0);

                    // Output children
                    var left: u32;
                    if (min(i, j) == gamma) {
                        left = u32(gamma) + u32(n); // Leaf
                    } else {
                        left = u32(gamma); // Internal
                    }

                    var right: u32;
                    if (max(i, j) == gamma + 1) {
                        right = u32(gamma + 1) + u32(n); // Leaf
                    } else {
                        right = u32(gamma + 1); // Internal
                    }

                    nodes[i].left = left;
                    nodes[i].right = right;
                }
            `
        });

        this.mortonPipeline = this.device.createComputePipeline({
            label: "Morton Pipeline",
            layout: 'auto',
            compute: { module: mortonShader, entryPoint: "main" }
        });

        this.sortPipeline = this.device.createComputePipeline({
            label: "Sort Pipeline",
            layout: 'auto',
            compute: { module: sortShader, entryPoint: "main" }
        });

        this.bvhPipeline = this.device.createComputePipeline({
            label: "BVH Pipeline",
            layout: 'auto',
            compute: { module: bvhShader, entryPoint: "main" }
        });
    }

    public async uploadData(binaryBuffer: ArrayBuffer) {
        // First 4 bytes = uint32 count
        const header = new Uint32Array(binaryBuffer, 0, 1);
        this.numElements = header[0];

        const payloadOffset = 4;
        const payloadLength = binaryBuffer.byteLength - payloadOffset;

        // Element buffer
        this.elementBuffer = this.device.createBuffer({
            size: payloadLength,
            usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST | GPUBufferUsage.VERTEX,
            mappedAtCreation: true
        });
        new Uint8Array(this.elementBuffer.getMappedRange()).set(new Uint8Array(binaryBuffer, payloadOffset));
        this.elementBuffer.unmap();

        // Morton code buffer (8 bytes per element: 4 byte code + 4 byte original index)
        this.mortonCodeBuffer = this.device.createBuffer({
            size: this.numElements * 8,
            usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC | GPUBufferUsage.COPY_DST
        });

        // BVH Node Buffer (approx 2N-1 nodes) 
        // 32 bytes per node: AABB Min (vec3) + Left (u32), AABB Max (vec3) + Right (u32)
        this.bvhNodeBuffer = this.device.createBuffer({
            size: Math.max(1, (this.numElements * 2 - 1)) * 32, // Prevent 0-size allocation
            usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC
        });
    }

    public build() {
        if (!this.numElements || this.numElements < 2) return;

        const commandEncoder = this.device.createCommandEncoder();

        // Pass 1: Compute Morton Codes
        const mortonPass = commandEncoder.beginComputePass();
        const mortonBindGroup = this.device.createBindGroup({
            layout: this.mortonPipeline.getBindGroupLayout(0),
            entries: [
                { binding: 0, resource: { buffer: this.elementBuffer } },
                { binding: 1, resource: { buffer: this.mortonCodeBuffer } }
            ]
        });
        mortonPass.setPipeline(this.mortonPipeline);
        mortonPass.setBindGroup(0, mortonBindGroup);
        mortonPass.dispatchWorkgroups(Math.ceil(this.numElements / 256));
        mortonPass.end();

        // Pass 2: Parallel Sort (Simplified Bitonic Sort)
        // Note: For production large-scale, a Radix Sort (decoupled look-back) is preferred.
        // Bitonic Sort is used here as a robust GPU-parallel baseline.
        const n = Math.pow(2, Math.ceil(Math.log2(this.numElements)));
        
        for (let p = 1; p < n; p <<= 1) {
            for (let q = p; q > 0; q >>= 1) {
                const sortPass = commandEncoder.beginComputePass();
                
                // We create a temporary uniform buffer for this step.
                // In a high-performance loop these would be pre-allocated.
                const uniformBuffer = this.device.createBuffer({
                    size: 8,
                    usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
                    mappedAtCreation: true
                });
                new Uint32Array(uniformBuffer.getMappedRange()).set([p, q]);
                uniformBuffer.unmap();

                const sortBindGroup = this.device.createBindGroup({
                    layout: this.sortPipeline.getBindGroupLayout(0),
                    entries: [
                        { binding: 0, resource: { buffer: this.mortonCodeBuffer } },
                        { binding: 1, resource: { buffer: uniformBuffer } }
                    ]
                });
                sortPass.setPipeline(this.sortPipeline);
                sortPass.setBindGroup(0, sortBindGroup);
                sortPass.dispatchWorkgroups(Math.ceil(n / 256));
                sortPass.end();
            }
        }

        // Pass 3: LBVH Tree Construction (Karras 2012)
        const bvhPass = commandEncoder.beginComputePass();
        const bvhBindGroup = this.device.createBindGroup({
            layout: this.bvhPipeline.getBindGroupLayout(0),
            entries: [
                { binding: 0, resource: { buffer: this.mortonCodeBuffer } },
                { binding: 1, resource: { buffer: this.bvhNodeBuffer } }
            ]
        });
        bvhPass.setPipeline(this.bvhPipeline);
        bvhPass.setBindGroup(0, bvhBindGroup);
        bvhPass.dispatchWorkgroups(Math.ceil((this.numElements - 1) / 256));
        bvhPass.end();

        this.device.queue.submit([commandEncoder.finish()]);
        console.log(`[LinearBVH] Built hierarchy for ${this.numElements} elements on GPU.`);
    }

    public getBVHBuffer(): GPUBuffer {
        return this.bvhNodeBuffer;
    }

    public getElementBuffer(): GPUBuffer {
        return this.elementBuffer;
    }

    public getElementCount(): number {
        return this.numElements;
    }

    public destroy() {
        if (this.elementBuffer) {
            this.elementBuffer.destroy();
        }
        if (this.mortonCodeBuffer) {
            this.mortonCodeBuffer.destroy();
        }
        if (this.bvhNodeBuffer) {
            this.bvhNodeBuffer.destroy();
        }
        this.numElements = 0;
    }
}