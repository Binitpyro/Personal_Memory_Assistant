// bubble.wgsl
// Transparent instanced bubble shader for file nodes.
// Uses smooth-shaded icosphere. Two-pass draw:
//   pass 1: cullMode 'front' (interior back faces)
//   pass 2: cullMode 'back'  (exterior front faces)
// Standard alpha blending, back-to-front CPU sorted per frame.

struct CameraUniform {
    viewProj: mat4x4<f32>,
    eyePosition: vec4<f32>,
    fogColor: vec4<f32>,
    currentVariant: u32,
    time: f32,
    screenWidth: f32,
    screenHeight: f32,
    fogDensity: f32,
    _pad1: f32,
    _pad2: f32,
    _pad3: f32,
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

    let seed = f32(in.inst_idx * 1234u + in.inst_type_hash % 100u);
    let time_val = camera.time;

    // Slow idle drift: sinusoidal wobble
    let drift_amp = in.inst_radius * 0.015;
    let drift = vec3<f32>(
        sin(time_val * 0.4 + seed * 0.11) * drift_amp,
        cos(time_val * 0.3 + seed * 0.13) * drift_amp,
        sin(time_val * 0.5 + seed * 0.09) * drift_amp,
    );

    // Breathing size wobble (vertex position scaled)
    let breathe = 1.0 + sin(time_val * 1.5 + seed * 0.2) * 0.02;

    let world_pos = (in.local_pos * breathe) * in.inst_radius + in.inst_position + drift;
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
fn fs_main(in: VertexOutput, @builtin(front_facing) is_front: bool) -> FragOutput {
    let raw_N = normalize(in.world_normal);
    let N = select(-raw_N, raw_N, is_front);
    let V = normalize(camera.eyePosition.xyz - in.world_pos);
    let NdotV = max(dot(N, V), 0.0);

    // Base color for bubbles
    let hue_shift = hash_to_hue(in.type_hash) * 0.1 - 0.05; // ±5% shift
    // e8a0bf (232, 160, 191) -> ~ vec3(0.91, 0.63, 0.75)
    let base = vec3<f32>(0.91 + hue_shift, 0.63, 0.75 - hue_shift);

    // Iridescence based on viewing angle
    let iri = iridescence(NdotV, hash_to_hue(in.type_hash));

    // Toon diffuse
    let L = normalize(vec3<f32>(0.5, 1.2, 0.7));
    let NdotL = max(dot(N, L), 0.0);
    let derivative = fwidth(NdotL);
    let toonLightColor = toon_lighting(NdotL, derivative);

    // Toon specular
    let H = normalize(L + V);
    let NdotH = max(dot(N, H), 0.0);
    let specDerivative = fwidth(NdotH);
    
    var final_rgb: vec3<f32>;
    var alpha: f32;

    if (is_front) {
        let spec = toon_specular(NdotH, 0.85, specDerivative) * 1.5;
        let rim = rim_glow(NdotV, vec3<f32>(1.0, 0.56, 0.67), 2.5, 0.8);
        // Mix base with iridescence
        let body = (base * 0.7 + iri * 0.3) * toonLightColor;
        let inner = vec3<f32>(1.0, 0.84, 0.88) * (pow(NdotV, 2.0) * 0.4);
        
        final_rgb = body + rim + vec3<f32>(spec) + inner;
        
        // Alpha for front faces: stronger at rims, slightly transparent in center
        let rim_factor = pow(1.0 - NdotV, 2.0);
        alpha = clamp(rim_factor * 0.7 + 0.1, 0.0, 1.0);
    } else {
        // Back face (interior)
        let spec = toon_specular(NdotH, 0.90, specDerivative) * 0.5; // weaker specular
        let rim = rim_glow(NdotV, vec3<f32>(1.0, 0.56, 0.67), 2.0, 0.4); // weaker rim
        let body = (base * 0.5 + iri * 0.2) * toonLightColor;
        let inner = vec3<f32>(1.0, 0.84, 0.88) * (pow(NdotV, 3.0) * 0.2); // weaker inner
        
        final_rgb = body + rim + vec3<f32>(spec) + inner;
        
        // Alpha for back faces: much lower opacity, giving it a translucent layer feel
        let rim_factor = pow(1.0 - NdotV, 3.0);
        alpha = clamp(rim_factor * 0.4 + 0.05, 0.0, 1.0);
    }

    // Distance-based atmospheric fog
    let fogged_rgb = atmospheric_fog(final_rgb, in.view_depth, camera.fogDensity, camera.fogColor.xyz);

    var out: FragOutput;
    out.color = vec4<f32>(clamp(fogged_rgb, vec3<f32>(0.0), vec3<f32>(2.0)), alpha);
    return out;
}
