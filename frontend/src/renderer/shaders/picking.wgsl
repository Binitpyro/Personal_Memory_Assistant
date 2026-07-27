// picking.wgsl — Unchanged silhouette-tight GPU picking pass.
//
// Deliberately kept in lockstep with the crystal & bubble vertex transforms
// MINUS all per-instance jitter (drift, wobble, breathing). Rotation around
// a fixed axis preserves the bounding-sphere silhouette so the picking
// projection stays accurate to a pixel or two — that's the trade the
// original renderer made and this rewrite preserves.
//
// If we ever add vertex-displacement effects that DO change the silhouette
// (e.g. Gerstner peaks that push past the bounding sphere), the picking
// pass has to grow the matching term too — right now it doesn't need to.

@group(0) @binding(0) var<uniform> camera: CameraUniform;

struct VertexInput {
    @location(0) local_pos: vec3<f32>,
    @location(1) local_normal: vec3<f32>,
    @location(2) inst_position: vec3<f32>,
    @location(3) inst_radius: f32,
    @location(4) inst_parent_index: u32,
    @location(5) inst_flags: u32,
    @location(6) inst_type_hash: u32,
    @location(7) inst_pad: u32,
    @builtin(instance_index) inst_idx: u32,
};

struct VertexOutput {
    @builtin(position) clip_pos: vec4<f32>,
    @location(0) @interpolate(flat) pick_id: u32,
};

@vertex
fn vs_main(in: VertexInput) -> VertexOutput {
    var out: VertexOutput;
    let world_pos = in.local_pos * in.inst_radius + in.inst_position;
    out.clip_pos = camera.viewProj * vec4<f32>(world_pos, 1.0);
    out.pick_id = in.inst_idx;
    return out;
}

struct FragOutput { @location(0) id: u32 };

@fragment
fn fs_main(in: VertexOutput) -> FragOutput {
    var out: FragOutput;
    out.id = in.pick_id;
    return out;
}
