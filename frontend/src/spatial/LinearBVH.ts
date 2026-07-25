/**
 * LinearBVH.ts
 * Implements a Linear Bounding Volume Hierarchy (LBVH) mapped to WebGPU Compute Shaders.
 * For 4M+ elements, we perform spatial sorting and hierarchy construction entirely on the GPU.
 *
 * STATUS: COMPLETE
 *   - Pass 1: Morton code generation
 *   - Pass 2: Parallel 3-Phase 8-bit Radix Sort
 *   - Pass 3: LBVH Tree Topology Construction (Karras 2012)
 *   - Pass 4: Bottom-up AABB calculation via atomic flags
 */

export class LinearBVH {
    private readonly device: GPUDevice;
    private numElements: number = 0;

    // WebGPU Buffers
    private elementBuffer!: GPUBuffer;       // [x, y, z, size, typeHash]
    private mortonCodeBuffer!: GPUBuffer;    // [morton_code, original_index]
    private mortonCodeBufferAlt!: GPUBuffer; // Ping-pong buffer for Radix Sort
    private histogramBuffer!: GPUBuffer;     // 64 workgroups * 256 buckets
    private bvhNodeBuffer!: GPUBuffer;       // Array of BVH Nodes
    private parentBuffer!: GPUBuffer;        // Parent pointers
    private flagBuffer!: GPUBuffer;          // Atomic flags for AABB bottom-up

    private sortUniformBuffers: GPUBuffer[] = [];

    // Pipelines
    private mortonPipeline!: GPUComputePipeline;
    private histogramPipeline!: GPUComputePipeline;
    private scanPipeline!: GPUComputePipeline;
    private scatterPipeline!: GPUComputePipeline;
    private bvhPipeline!: GPUComputePipeline;
    private aabbPipeline!: GPUComputePipeline;

    constructor(device: GPUDevice) {
        this.device = device;
    }

    public async initializePipelines() {
        // 1. Morton Code Generation Shader
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
                    
                    // Fixed morton code expansion alignment using SHL
                    let code = (xx << 2u) | (yy << 1u) | zz;
                    mortonCodes[idx] = MortonEntry(code, idx);
                }
            `
        });

        const radixSharedDefs = `
            struct MortonEntry {
                code: u32,
                index: u32,
            };
            struct SortUniforms {
                shift: u32,
                num_elements: u32,
            };
        `;

        // 2a. Radix Sort - Histogram Pass
        const histogramShader = this.device.createShaderModule({
            label: "Radix Histogram",
            code: `
                ${radixSharedDefs}
                @group(0) @binding(0) var<storage, read> src_elements: array<MortonEntry>;
                @group(0) @binding(1) var<storage, read_write> global_hist: array<u32>;
                @group(0) @binding(2) var<uniform> uniforms: SortUniforms;

                var<workgroup> local_hist: array<atomic<u32>, 256>;

                @compute @workgroup_size(256)
                fn main(
                    @builtin(workgroup_id) wg_id: vec3<u32>,
                    @builtin(local_invocation_id) local_id: vec3<u32>
                ) {
                    if (local_id.x < 256u) {
                        atomicStore(&local_hist[local_id.x], 0u);
                    }
                    workgroupBarrier();

                    let wg_idx = wg_id.x;
                    let n = uniforms.num_elements;
                    let elements_per_wg = (n + 63u) / 64u;
                    let start_idx = wg_idx * elements_per_wg;
                    let end_idx = min(start_idx + elements_per_wg, n);

                    for (var i = start_idx + local_id.x; i < end_idx; i += 256u) {
                        let code = src_elements[i].code;
                        let digit = (code >> uniforms.shift) & 0xFFu;
                        atomicAdd(&local_hist[digit], 1u);
                    }

                    workgroupBarrier();

                    if (local_id.x < 256u) {
                        let count = atomicLoad(&local_hist[local_id.x]);
                        global_hist[local_id.x * 64u + wg_idx] = count;
                    }
                }
            `
        });

        // 2b. Radix Sort - Scan Pass (Parallel Exclusive Prefix Sum)
        const scanShader = this.device.createShaderModule({
            label: "Radix Scan",
            code: `
                @group(0) @binding(0) var<storage, read_write> global_hist: array<u32>;

                var<workgroup> temp: array<u32, 256>;

                @compute @workgroup_size(256)
                fn main(
                    @builtin(local_invocation_id) local_id: vec3<u32>
                ) {
                    let tid = local_id.x;
                    
                    // 1. Thread-local exclusive scan over 64 elements
                    var sum = 0u;
                    let start_idx = tid * 64u;
                    for (var i = 0u; i < 64u; i++) {
                        let val = global_hist[start_idx + i];
                        global_hist[start_idx + i] = sum;
                        sum += val;
                    }
                    
                    temp[tid] = sum;
                    workgroupBarrier();

                    // 2. Workgroup-wide inclusive scan over the 256 block totals (Kogge-Stone)
                    for (var offset = 1u; offset < 256u; offset *= 2u) {
                        var t = 0u;
                        if (tid >= offset) {
                            t = temp[tid - offset];
                        }
                        workgroupBarrier();
                        if (tid >= offset) {
                            temp[tid] += t;
                        }
                        workgroupBarrier();
                    }

                    // 3. Convert inclusive to exclusive scan for the block offset
                    var block_offset = 0u;
                    if (tid > 0u) {
                        block_offset = temp[tid - 1u];
                    }

                    // 4. Add block_offset to the thread's locally scanned elements
                    for (var i = 0u; i < 64u; i++) {
                        global_hist[start_idx + i] += block_offset;
                    }
                }
            `
        });

        // 2c. Radix Sort - Scatter Pass
        const scatterShader = this.device.createShaderModule({
            label: "Radix Scatter",
            code: `
                ${radixSharedDefs}
                @group(0) @binding(0) var<storage, read> src_elements: array<MortonEntry>;
                @group(0) @binding(1) var<storage, read_write> dst_elements: array<MortonEntry>;
                @group(0) @binding(2) var<storage, read> global_hist: array<u32>;
                @group(0) @binding(3) var<uniform> uniforms: SortUniforms;

                var<workgroup> local_offset: array<atomic<u32>, 256>;

                @compute @workgroup_size(256)
                fn main(
                    @builtin(workgroup_id) wg_id: vec3<u32>,
                    @builtin(local_invocation_id) local_id: vec3<u32>
                ) {
                    let wg_idx = wg_id.x;
                    
                    if (local_id.x < 256u) {
                        let global_off = global_hist[local_id.x * 64u + wg_idx];
                        atomicStore(&local_offset[local_id.x], global_off);
                    }
                    workgroupBarrier();

                    let n = uniforms.num_elements;
                    let elements_per_wg = (n + 63u) / 64u;
                    let start_idx = wg_idx * elements_per_wg;
                    let end_idx = min(start_idx + elements_per_wg, n);

                    for (var i = start_idx + local_id.x; i < end_idx; i += 256u) {
                        let entry = src_elements[i];
                        let digit = (entry.code >> uniforms.shift) & 0xFFu;
                        let dst_idx = atomicAdd(&local_offset[digit], 1u);
                        dst_elements[dst_idx] = entry;
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
                @group(0) @binding(2) var<storage, read_write> parents: array<u32>;

                fn delta(i: i32, j: i32, n: i32) -> i32 {
                    if (j < 0 || j >= n) { return -1; }
                    let code_i = mortonCodes[i].code;
                    let code_j = mortonCodes[j].code;
                    // Note: countLeadingZeros is the WGSL spec standard, fallback to clz if using a very old Dawn version.
                    if (code_i == code_j) { return 32 + countLeadingZeros(u32(i ^ j)); }
                    return countLeadingZeros(code_i ^ code_j);
                }

                @compute @workgroup_size(256)
                fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
                    let i = i32(global_id.x);
                    let n = i32(arrayLength(&mortonCodes));
                    if (i >= n - 1) { return; }

                    let d = select(-1, 1, delta(i, i + 1, n) - delta(i, i - 1, n) > 0);
                    let delta_min = delta(i, i - d, n);
                    var l_max = 2;
                    while (delta(i, i + l_max * d, n) > delta_min) {
                        l_max = l_max * 2;
                    }

                    var l = 0;
                    var t = l_max / 2;
                    while (t > 0) {
                        if (delta(i, i + (l + t) * d, n) > delta_min) {
                            l = l + t;
                        }
                        t = t / 2;
                    }
                    let j = i + l * d;

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

                    var left: u32;
                    if (min(i, j) == gamma) {
                        left = u32(gamma) + u32(n);
                    } else {
                        left = u32(gamma);
                    }

                    var right: u32;
                    if (max(i, j) == gamma + 1) {
                        right = u32(gamma + 1) + u32(n);
                    } else {
                        right = u32(gamma + 1);
                    }

                    nodes[i].left = left;
                    nodes[i].right = right;
                    parents[left] = u32(i);
                    parents[right] = u32(i);
                }
            `
        });

        // 4. AABB Bottom-Up Pass
        const aabbShader = this.device.createShaderModule({
            label: "LBVH AABB Bottom-Up",
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

                struct Node {
                    min: vec3<f32>,
                    left: u32,
                    max: vec3<f32>,
                    right: u32,
                };

                @group(0) @binding(0) var<storage, read> elements: array<Element>;
                @group(0) @binding(1) var<storage, read> mortonCodes: array<MortonEntry>;
                @group(0) @binding(2) var<storage, read_write> nodes: array<Node>;
                @group(0) @binding(3) var<storage, read> parents: array<u32>;
                @group(0) @binding(4) var<storage, read_write> flags: array<atomic<u32>>;

                @compute @workgroup_size(256)
                fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
                    let leaf_idx = global_id.x;
                    let n = arrayLength(&mortonCodes);
                    if (leaf_idx >= n) { return; }

                    // 1. Initialize leaf node AABB
                    let original_idx = mortonCodes[leaf_idx].index;
                    let elem = elements[original_idx];
                    let half_size = vec3<f32>(elem.size * 0.5);
                    let leaf_node_idx = leaf_idx + n; // Leaves are placed at index N to 2N-1
                    
                    nodes[leaf_node_idx].min = elem.pos - half_size;
                    nodes[leaf_node_idx].max = elem.pos + half_size;
                    nodes[leaf_node_idx].left = 0xFFFFFFFFu;
                    nodes[leaf_node_idx].right = 0xFFFFFFFFu;

                    // 2. Bottom-up reduction loop
                    var current = leaf_node_idx;
                    while (current != 0u) {
                        let parent = parents[current];
                        
                        // Atomically mark that a child has arrived
                        let old_flag = atomicAdd(&flags[parent], 1u);
                        if (old_flag == 0u) {
                            // First child to arrive terminates.
                            break;
                        }
                        
                        // Second child computes the parent AABB
                        let left_child = nodes[parent].left;
                        let right_child = nodes[parent].right;
                        
                        let min_left = nodes[left_child].min;
                        let max_left = nodes[left_child].max;
                        let min_right = nodes[right_child].min;
                        let max_right = nodes[right_child].max;
                        
                        // Defensive bounds check (internal nodes should always have valid children)
                        if (left_child != 0xFFFFFFFFu && right_child != 0xFFFFFFFFu) {
                            nodes[parent].min = min(min_left, min_right);
                            nodes[parent].max = max(max_left, max_right);
                        }
                        
                        // If parent was 0 (the root), current becomes 0 and the loop terminates.
                        // Therefore, the root's AABB is correctly computed before exiting.
                        current = parent;
                    }
                }
            `
        });

        this.mortonPipeline = this.device.createComputePipeline({
            label: "Morton Pipeline",
            layout: 'auto',
            compute: { module: mortonShader, entryPoint: "main" }
        });

        this.histogramPipeline = this.device.createComputePipeline({
            label: "Histogram Pipeline",
            layout: 'auto',
            compute: { module: histogramShader, entryPoint: "main" }
        });

        this.scanPipeline = this.device.createComputePipeline({
            label: "Scan Pipeline",
            layout: 'auto',
            compute: { module: scanShader, entryPoint: "main" }
        });

        this.scatterPipeline = this.device.createComputePipeline({
            label: "Scatter Pipeline",
            layout: 'auto',
            compute: { module: scatterShader, entryPoint: "main" }
        });

        this.bvhPipeline = this.device.createComputePipeline({
            label: "BVH Pipeline",
            layout: 'auto',
            compute: { module: bvhShader, entryPoint: "main" }
        });

        this.aabbPipeline = this.device.createComputePipeline({
            label: "AABB Pipeline",
            layout: 'auto',
            compute: { module: aabbShader, entryPoint: "main" }
        });
    }

    public async uploadData(binaryBuffer: ArrayBuffer) {
        // Safe lifecycle cleanup
        this.destroyBuffers();

        const header = new Uint32Array(binaryBuffer, 0, 1);
        this.numElements = header[0];

        const payloadOffset = 4;
        const payloadLength = binaryBuffer.byteLength - payloadOffset;

        this.elementBuffer = this.device.createBuffer({
            size: payloadLength,
            usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST | GPUBufferUsage.VERTEX,
            mappedAtCreation: true
        });
        new Uint8Array(this.elementBuffer.getMappedRange()).set(new Uint8Array(binaryBuffer, payloadOffset));
        this.elementBuffer.unmap();

        // 8 bytes per element: 4 byte code + 4 byte index
        const mortonBufferSize = this.numElements * 8;
        this.mortonCodeBuffer = this.device.createBuffer({
            size: mortonBufferSize,
            usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC | GPUBufferUsage.COPY_DST
        });

        this.mortonCodeBufferAlt = this.device.createBuffer({
            size: mortonBufferSize,
            usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC | GPUBufferUsage.COPY_DST
        });

        // 64 workgroups * 256 buckets * 4 bytes = 65536 bytes
        this.histogramBuffer = this.device.createBuffer({
            size: 65536,
            usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC | GPUBufferUsage.COPY_DST
        });

        const maxNodes = Math.max(1, this.numElements * 2);
        
        // Node Struct: min (12) + left (4) + max (12) + right (4) = 32 bytes
        // Buffer sizes: 2N nodes allocated (internal: N-1, leaf: N, 1 slot wasted/padded)
        this.bvhNodeBuffer = this.device.createBuffer({
            size: maxNodes * 32,
            usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC | GPUBufferUsage.COPY_DST
        });

        this.parentBuffer = this.device.createBuffer({
            size: maxNodes * 4,
            usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST
        });

        this.flagBuffer = this.device.createBuffer({
            size: maxNodes * 4,
            usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST
        });

        // Pre-create sort uniform buffers to avoid dynamic allocation leaks
        for (let i = 0; i < 4; i++) {
            const buf = this.device.createBuffer({
                size: 8,
                usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST
            });
            this.device.queue.writeBuffer(buf, 0, new Uint32Array([i * 8, this.numElements]));
            this.sortUniformBuffers.push(buf);
        }
    }

    public async build() {
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

        // Pass 2: 3-Phase 8-bit Radix Sort (4 passes for 32-bit morton codes)
        let srcBuffer = this.mortonCodeBuffer;
        let dstBuffer = this.mortonCodeBufferAlt;

        for (let i = 0; i < 4; i++) {
            const radixPass = commandEncoder.beginComputePass();

            // 1. Histogram
            const histBindGroup = this.device.createBindGroup({
                layout: this.histogramPipeline.getBindGroupLayout(0),
                entries: [
                    { binding: 0, resource: { buffer: srcBuffer } },
                    { binding: 1, resource: { buffer: this.histogramBuffer } },
                    { binding: 2, resource: { buffer: this.sortUniformBuffers[i] } }
                ]
            });
            radixPass.setPipeline(this.histogramPipeline);
            radixPass.setBindGroup(0, histBindGroup);
            radixPass.dispatchWorkgroups(64);

            // 2. Scan
            const scanBindGroup = this.device.createBindGroup({
                layout: this.scanPipeline.getBindGroupLayout(0),
                entries: [
                    { binding: 0, resource: { buffer: this.histogramBuffer } }
                ]
            });
            radixPass.setPipeline(this.scanPipeline);
            radixPass.setBindGroup(0, scanBindGroup);
            radixPass.dispatchWorkgroups(1);

            // 3. Scatter
            const scatterBindGroup = this.device.createBindGroup({
                layout: this.scatterPipeline.getBindGroupLayout(0),
                entries: [
                    { binding: 0, resource: { buffer: srcBuffer } },
                    { binding: 1, resource: { buffer: dstBuffer } },
                    { binding: 2, resource: { buffer: this.histogramBuffer } },
                    { binding: 3, resource: { buffer: this.sortUniformBuffers[i] } }
                ]
            });
            radixPass.setPipeline(this.scatterPipeline);
            radixPass.setBindGroup(0, scatterBindGroup);
            radixPass.dispatchWorkgroups(64);

            radixPass.end();

            // Swap buffers
            const temp = srcBuffer;
            srcBuffer = dstBuffer;
            dstBuffer = temp;
        }

        // Pass 3: LBVH Tree Topology Construction
        const bvhPass = commandEncoder.beginComputePass();
        const bvhBindGroup = this.device.createBindGroup({
            layout: this.bvhPipeline.getBindGroupLayout(0),
            entries: [
                { binding: 0, resource: { buffer: srcBuffer } },
                { binding: 1, resource: { buffer: this.bvhNodeBuffer } },
                { binding: 2, resource: { buffer: this.parentBuffer } }
            ]
        });
        bvhPass.setPipeline(this.bvhPipeline);
        bvhPass.setBindGroup(0, bvhBindGroup);
        bvhPass.dispatchWorkgroups(Math.ceil((this.numElements - 1) / 256));
        bvhPass.end();

        // Pass 4: Bottom-up AABB calculation
        commandEncoder.clearBuffer(this.flagBuffer, 0, this.flagBuffer.size);

        const aabbPass = commandEncoder.beginComputePass();
        const aabbBindGroup = this.device.createBindGroup({
            layout: this.aabbPipeline.getBindGroupLayout(0),
            entries: [
                { binding: 0, resource: { buffer: this.elementBuffer } },
                { binding: 1, resource: { buffer: srcBuffer } },
                { binding: 2, resource: { buffer: this.bvhNodeBuffer } },
                { binding: 3, resource: { buffer: this.parentBuffer } },
                { binding: 4, resource: { buffer: this.flagBuffer } }
            ]
        });
        aabbPass.setPipeline(this.aabbPipeline);
        aabbPass.setBindGroup(0, aabbBindGroup);
        aabbPass.dispatchWorkgroups(Math.ceil(this.numElements / 256));
        aabbPass.end();

        this.device.queue.submit([commandEncoder.finish()]);

        // Await GPU execution for memory safety before passing control back
        await this.device.queue.onSubmittedWorkDone();
        console.log(`[LinearBVH] Built hierarchy with AABBs for ${this.numElements} elements on GPU.`);
    }

    public getBVHBuffer(): GPUBuffer {
        return this.bvhNodeBuffer;
    }

    public getParentBuffer(): GPUBuffer {
        return this.parentBuffer;
    }

    public getElementBuffer(): GPUBuffer {
        return this.elementBuffer;
    }

    public getElementCount(): number {
        return this.numElements;
    }

    private destroyBuffers() {
        if (this.elementBuffer) this.elementBuffer.destroy();
        if (this.mortonCodeBuffer) this.mortonCodeBuffer.destroy();
        if (this.mortonCodeBufferAlt) this.mortonCodeBufferAlt.destroy();
        if (this.histogramBuffer) this.histogramBuffer.destroy();
        if (this.bvhNodeBuffer) this.bvhNodeBuffer.destroy();
        if (this.parentBuffer) this.parentBuffer.destroy();
        if (this.flagBuffer) this.flagBuffer.destroy();
        
        for (const buf of this.sortUniformBuffers) {
            if (buf) buf.destroy();
        }
        this.sortUniformBuffers = [];
    }

    public destroy() {
        this.destroyBuffers();
        this.numElements = 0;
    }
}
