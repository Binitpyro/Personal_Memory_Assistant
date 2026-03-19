// culling.wgsl
// Frustum culling compute shader

struct CameraUniform {
    viewProj: mat4x4<f32>,
    planes: array<vec4<f32>, 6>,
    eyePosition: vec3<f32>,
};

@group(0) @binding(0) var<uniform> camera: CameraUniform;

struct InstanceData {
    pos: vec3<f32>,
    size: f32,
    typeHash: u32,
};

@group(0) @binding(1) var<storage, read> allInstances: array<InstanceData>;
@group(0) @binding(2) var<storage, read_write> visibleInstances: array<InstanceData>;

// Indirect draw parameters
struct DrawIndirectArgs {
    vertexCount: u32,
    instanceCount: atomic<u32>,
    firstVertex: u32,
    firstInstance: u32,
};
@group(0) @binding(3) var<storage, read_write> drawArgs: DrawIndirectArgs;

@compute @workgroup_size(256)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let idx = global_id.x;
    if (idx >= arrayLength(&allInstances)) {
        return;
    }

    let inst = allInstances[idx];
    let pos = inst.pos;
    let radius = inst.size;

    // Check against 6 frustum planes
    var visible = true;
    for (var i = 0u; i < 6u; i = i + 1u) {
        let plane = camera.planes[i];
        if (dot(plane.xyz, pos) + plane.w < -radius) {
            visible = false;
            break;
        }
    }

    if (visible) {
        let writeIdx = atomicAdd(&drawArgs.instanceCount, 1u);
        visibleInstances[writeIdx] = inst;
    }
}
