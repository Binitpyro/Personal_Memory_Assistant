// picking.wgsl
// GPU picking pass for instanced crystal and bubble meshes.
//
// Renders both mesh types opaque with depth test into an r32uint texture.
// The frontmost fragment writes its INSTANCE INDEX (unique per node) as
// the pick ID, so the CPU-side pick() can look up the exact node clicked.
//
// This is a change from the earlier version, which wrote type_hash — but
// type_hash is derived from a file's extension, so many nodes share the
// same value. That made it impossible to pick a specific .py file out of
// several. Instance index is per-node-unique by construction.
//
// The renderer must pass the SAME compacted per-frame instance buffer that
// the crystal/bubble render pipelines use, so instance_index here matches
// the visible-set slot the user sees. The CPU then maps that back to the
// original source-buffer node index via VisibleSet.crystalIndices /
// bubbleIndices in NavigationController.

struct CameraUniform {
    viewProj: mat4x4<f32>,
    eyePosition: vec3<f32>,
    currentVariant: u32,
    time: f32,
    screenWidth: f32,
    screenHeight: f32,
    fogDensity: f32,
    fogColor: vec3<f32>,
    _pad2: f32,
};

@group(0) @binding(0) var<uniform> camera: CameraUniform;

// Per-vertex (stride 24 bytes: xyz + normal)
struct VertexInput {
    @location(0) local_pos: vec3<f32>,
    @location(1) local_normal: vec3<f32>,
    // Per-instance (stride 32 bytes matching Node struct).
    // We only actually need position + radius here — the rest of the
    // fields are declared so the vertex-buffer layout matches the render
    // pipelines and we can reuse the same instance VBO without rebinding.
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
    // `flat` interpolation guarantees the fragment sees the exact instance
    // integer with no interpolation artefacts. Critical for a pick ID.
    @location(0) @interpolate(flat) pick_id: u32,
};

@vertex
fn vs_main(in: VertexInput) -> VertexOutput {
    var out: VertexOutput;

    // IMPORTANT: keep this transform IDENTICAL to what the crystal / bubble
    // pipelines use, minus per-instance jitter/drift. The tighter this
    // matches the rendered silhouette, the more accurate picking near
    // object edges will be.
    //
    // We deliberately do NOT reproduce the vertex jitter that the crystal
    // shader used to have; the crystal shader has been rewritten to use a
    // per-instance rotation instead (rotation preserves the base mesh
    // silhouette from any viewing angle, so picking without the rotation
    // is still tight to a pixel or two — good enough).
    let world_pos = in.local_pos * in.inst_radius + in.inst_position;
    out.clip_pos = camera.viewProj * vec4<f32>(world_pos, 1.0);
    // `inst_idx` is the compacted-buffer slot index, which the CPU maps
    // back to the source node index via VisibleSet.*Indices.
    out.pick_id = in.inst_idx;
    return out;
}

struct FragOutput {
    @location(0) id: u32,
};

@fragment
fn fs_main(in: VertexOutput) -> FragOutput {
    var out: FragOutput;
    out.id = in.pick_id;
    return out;
}
