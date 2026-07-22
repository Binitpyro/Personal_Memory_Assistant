// bubble.wgsl
// Transparent instanced bubble shader for file nodes.
// Uses smooth-shaded icosphere. Two-pass draw:
//   pass 1: cullMode 'front' (interior back faces)
//   pass 2: cullMode 'back'  (exterior front faces)
// Standard alpha blending, back-to-front CPU sorted per frame.

struct CameraUniform {
    viewProj: mat4x4<f32>,
    eyePosition: vec3<f32>,
    _pad: f32,
    time: f32,
    screenWidth: f32,
    screenHeight: f32,
    _pad2: f32,
};

@group(0) @binding(0) var<uniform> camera: CameraUniform;

// Per-vertex: xyz + normal xyz (interleaved, 24 bytes)
struct VertexInput {
    @location(0) local_pos: vec3<f32>,
    @location(1) local_normal: vec3<f32>,
    // Per-instance (stride 32 bytes)
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
    @location(0) world_pos: vec3<f32>,
    @location(1) world_normal: vec3<f32>,
    @location(2) @interpolate(flat) type_hash: u32,
    @location(3) view_depth: f32,
};

@vertex
fn vs_main(in: VertexInput) -> VertexOutput {
    var out: VertexOutput;

    // Slow idle drift: sinusoidal wobble seeded by instance index + type_hash
    let seed = f32(in.inst_idx * 1234u + in.inst_type_hash % 100u);
    let drift_amp = in.inst_radius * 0.015;
    let drift = vec3<f32>(
        sin(camera.time * 0.4 + seed * 0.11) * drift_amp,
        cos(camera.time * 0.3 + seed * 0.13) * drift_amp,
        sin(camera.time * 0.5 + seed * 0.09) * drift_amp,
    );

    let world_pos = in.local_pos * in.inst_radius + in.inst_position + drift;
    out.clip_pos = camera.viewProj * vec4<f32>(world_pos, 1.0);
    out.world_pos = world_pos;
    out.world_normal = normalize(in.local_normal);
    out.type_hash = in.inst_type_hash;
    out.view_depth = out.clip_pos.w;
    return out;
}

// Map type_hash to a unique iridescence base hue
fn hash_to_hue(h: u32) -> f32 {
    return fract(f32(h % 360u) / 360.0);
}

// Thin-film iridescence: angle-dependent hue shift
fn iridescence(NdotV: f32, base_hue: f32) -> vec3<f32> {
    let phase = NdotV * 3.14159 * 2.0 + base_hue * 6.28318;
    let r = 0.5 + 0.5 * cos(phase);
    let g = 0.5 + 0.5 * cos(phase + 2.094);
    let b = 0.5 + 0.5 * cos(phase + 4.189);
    return vec3<f32>(r, g, b);
}

struct FragOutput {
    @location(0) color: vec4<f32>,
};

@fragment
fn fs_main(in: VertexOutput) -> FragOutput {
    let N = normalize(in.world_normal);
    let V = normalize(camera.eyePosition - in.world_pos);
    let NdotV = max(dot(N, V), 0.0);

    // Schlick Fresnel — pronounced rim effect for glass bubble
    let fresnel = 0.02 + 0.98 * pow(1.0 - NdotV, 4.0);

    // Thin-film iridescence on the rim
    let base_hue = hash_to_hue(in.type_hash);
    let irid = iridescence(NdotV, base_hue);

    // Specular highlight
    let L = normalize(vec3<f32>(0.5, 1.2, 0.7));
    let H = normalize(L + V);
    let spec = pow(max(dot(N, H), 0.0), 64.0) * 0.8;

    // Interior face gets a warm tint
    let interior_tint = mix(irid * 0.3, vec3<f32>(0.9, 0.95, 1.0), 0.5);

    let rgb = irid * fresnel * 1.2 + vec3<f32>(spec) + interior_tint * 0.1;
    // Alpha: strong at rim, almost invisible in center (true soap-bubble look)
    let alpha = clamp(fresnel * 0.85 + 0.04, 0.0, 0.92);

    var out: FragOutput;
    out.color = vec4<f32>(clamp(rgb, vec3<f32>(0.0), vec3<f32>(1.5)), alpha);
    return out;
}
