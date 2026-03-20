struct CameraUniform {
    viewProj: mat4x4<f32>,
    eyePosition: vec3<f32>,
};

@group(0) @binding(0) var<uniform> camera: CameraUniform;

struct VertexInput {
    @builtin(instance_index) instance_idx: u32,
    @location(0) quad_pos: vec2<f32>,
    @location(1) instance_position: vec3<f32>,
    @location(2) instance_radius: f32,
    @location(3) instance_flags: u32,
    @location(4) instance_type_hash: u32,
};

struct VertexOutput {
    @builtin(position) clip_position: vec4<f32>,
    @location(0) local_uv: vec2<f32>,
    @location(1) @interpolate(flat) type_hash: u32,
    @location(2) billboardWorldPos: vec3<f32>,
    @location(3) forward: vec3<f32>,
    @location(4) sphere_radius: f32,
};

@vertex
fn vs_main(in: VertexInput) -> VertexOutput {
    var out: VertexOutput;

    let actual_size = in.instance_radius;
    let instancePos = in.instance_position;

    var viewDir = camera.eyePosition - instancePos;
    let dist = length(viewDir);
    if dist < 0.001 {
        viewDir = vec3<f32>(0.0, 0.0, 1.0);
    } else {
        viewDir = viewDir / dist;
    }
    let forward = viewDir;
    
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
    out.type_hash = in.instance_type_hash;
    out.billboardWorldPos = worldPos;
    out.forward = forward;
    out.sphere_radius = actual_size;

    return out;
}

struct FragmentOutput {
    @location(0) hash: u32,
    @builtin(frag_depth) depth: f32,
};

@fragment
fn fs_main(in: VertexOutput) -> FragmentOutput {
    let distSq = dot(in.local_uv, in.local_uv);
    if distSq > 1.0 { discard; }

    let z = sqrt(1.0 - distSq);
    let trueWorldPos = in.billboardWorldPos + in.forward * (z * in.sphere_radius);
    let trueClipPos = camera.viewProj * vec4<f32>(trueWorldPos, 1.0);

    var out: FragmentOutput;
    out.hash = in.type_hash;
    out.depth = trueClipPos.z / trueClipPos.w;

    return out;
}
