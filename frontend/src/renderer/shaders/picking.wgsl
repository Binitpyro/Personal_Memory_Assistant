// picking.wgsl — silhouette-tight GPU picking pass.
//
// This used to apply NO rotation at all, on the theory that rotating about a
// fixed axis preserves the bounding-sphere silhouette. That holds for a sphere,
// but build() normalises a crystal so its *longest* axis is radius 1 — an
// elongated prism therefore fills only a thin sliver of that sphere, and the
// un-rotated sliver frequently did not overlap the rotated one that was drawn.
// Clicks missed.
//
// So the transform now comes from crystal_xform()/crystal_local_pos() in
// common.wgsl, shared verbatim with crystal.wgsl. The two cannot drift apart.
//
// Bubbles share this pipeline but not the transform: they are icospheres, and
// the crystal path's non-uniform stretch would turn them into ellipsoids and
// break their pick silhouette. Bit 0 of inst_flags marks a folder (= crystal).

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
    var local = in.local_pos;
    if ((in.inst_flags & 1u) != 0u) {
        let x = crystal_xform(in.inst_type_hash, camera.time);
        local = crystal_local_pos(x, in.local_pos);
    }
    let world_pos = local * in.inst_radius + in.inst_position;
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
