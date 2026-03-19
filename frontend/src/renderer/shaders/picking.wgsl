struct CameraUniform {
    viewProj: mat4x4<f32>,
    padding: array<vec4<f32>, 6>, 
    eyePosition: vec3<f32>,
};

struct Node {
    position: vec3<f32>,
    radius: f32,
    parent_index: u32,
    flags: u32,
    type_hash: u32,
    pad: u32,
}

@group(0) @binding(0) var<uniform> camera: CameraUniform;
@group(0) @binding(1) var<storage, read> nodes: array<Node>;
@group(0) @binding(2) var<storage, read> visible_indices: array<u32>;

struct VertexInput {
    @builtin(instance_index) instance_idx: u32,
    @location(0) quad_pos: vec2<f32>,      
};

struct VertexOutput {
    @builtin(position) clip_position: vec4<f32>,
    @location(0) local_uv: vec2<f32>,      
    @location(1) @interpolate(flat) type_hash: u32,
};

@vertex
fn vs_main(in: VertexInput) -> VertexOutput {
    var out: VertexOutput;
    
    let actual_idx = visible_indices[in.instance_idx];
    let node = nodes[actual_idx];
    
    let actual_size = node.radius;
    let instancePos = node.position;
    
    let forward = normalize(camera.eyePosition - instancePos);
    let world_up = select(
        vec3<f32>(0.0, 1.0, 0.0),
        vec3<f32>(1.0, 0.0, 0.0),
        abs(forward.y) > 0.99
    );
    let right = normalize(cross(world_up, forward));
    let up = cross(forward, right);
    
    let localOffset = (right * in.quad_pos.x + up * in.quad_pos.y) * actual_size;
    let worldPos = instancePos + localOffset;
    
    out.clip_position = camera.viewProj * vec4<f32>(worldPos, 1.0);
    out.local_uv = in.quad_pos;
    out.type_hash = node.type_hash;
    
    return out;
}

@fragment
fn fs_main(in: VertexOutput) -> @location(0) u32 {
    let distSq = dot(in.local_uv, in.local_uv);
    if (distSq > 1.0) { discard; }
    
    return in.type_hash;
}
