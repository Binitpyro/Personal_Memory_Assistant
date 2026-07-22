// crystal.wgsl
// Opaque instanced crystal shader for collapsed folder nodes.
//
// Design decisions:
//   1. FLAT-SHADED icosahedron mesh: per-face normals via unshared vertices
//      (see icosahedron.ts). Sharp facet transitions are what makes a
//      "gem" read as faceted rather than smooth.
//
//   2. PER-INSTANCE ROTATION, not vertex jitter. The earlier version of
//      this shader displaced vertices along their normals — but that
//      changes the silhouette per instance, which then disagrees with the
//      picking shader (which has to use the un-jittered mesh, otherwise
//      the picking VBO would need per-instance jitter parameters and stay
//      in sync). Instead we rotate the base mesh by a hash-derived
//      quaternion. That keeps the silhouette identical (any rotation of
//      an icosahedron viewed from outside its bounding sphere fits the
//      same clickable disc from picking's point of view), but each
//      instance shows a different facet arrangement to the camera.
//
//   3. FAKE REFRACTION via screen-space UV offset of the previous frame,
//      with a per-channel offset for cheap chromatic dispersion. This is
//      NOT physically correct — it's the trick shipped games use for gems
//      (Genshin, Destiny) because real two-pass refraction is expensive
//      and often looks worse in motion. If the user really wants real
//      refraction that's a follow-up.

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


// Per-vertex layout (interleaved, stride 24 bytes):
struct VertexInput {
    @location(0) local_pos: vec3<f32>,
    @location(1) local_normal: vec3<f32>,
    // Per-instance layout (stride 32 bytes, matches Rust Node struct):
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
    // Flat-interpolated so all 3 vertices of a triangle share ONE face normal
    // in the fragment shader. This is what actually delivers the facet look;
    // interpolating normals would smooth them away.
    @location(1) @interpolate(flat) face_normal: vec3<f32>,
    @location(2) @interpolate(flat) type_hash: u32,
};

// Rodrigues' rotation formula: rotate a vector v around unit axis k by angle theta.
// Cheap to inline; used to give each crystal a unique orientation without
// storing a matrix per instance.
fn rotate_axis(v: vec3<f32>, k: vec3<f32>, theta: f32) -> vec3<f32> {
    let c = cos(theta);
    let s = sin(theta);
    return v * c + cross(k, v) * s + k * dot(k, v) * (1.0 - c);
}

@vertex
fn vs_main(in: VertexInput) -> VertexOutput {
    var out: VertexOutput;


    // Per-instance rotation seeded by type_hash. Golden-ratio multipliers keep
    // adjacent hashes visually distinct (this is a standard trick from procedural
    // graphics — small hash deltas produce large angle deltas).
    let h = f32(in.inst_type_hash);
    let axis = normalize(vec3<f32>(
        sin(h * 0.1618),
        cos(h * 0.2718),
        sin(h * 0.3141 + 1.0),
    ));
    let angle = h * 0.001; // enough variation without spinning

    let rot_pos    = rotate_axis(in.local_pos,    axis, angle);
    let rot_normal = rotate_axis(in.local_normal, axis, angle);

    let world_pos = rot_pos * in.inst_radius + in.inst_position;
    out.clip_pos = camera.viewProj * vec4<f32>(world_pos, 1.0);
    out.world_pos = world_pos;
    // Normal is a direction — rotation preserves length so no re-normalization needed,
    // but do it anyway to defend against floating-point drift.
    out.face_normal = normalize(rot_normal);
    out.type_hash = in.inst_type_hash;
    return out;
}

// Map type_hash to a hue in the cyan → violet → magenta range (gem palette).
// Kept simple: HSL-ish direct conversion, no LUT.
fn hash_to_crystal_color(h: u32) -> vec3<f32> {
    let hue = fract(f32(h % 360u) / 360.0 * 0.33 + 0.5);
    let c = 0.55;
    let x = c * (1.0 - abs(fract(hue * 6.0) * 2.0 - 1.0));
    let m = 0.35;
    let seg = u32(hue * 6.0) % 6u;
    var rgb: vec3<f32>;
    switch seg {
        case 0u: { rgb = vec3<f32>(c, x, 0.0); }
        case 1u: { rgb = vec3<f32>(x, c, 0.0); }
        case 2u: { rgb = vec3<f32>(0.0, c, x); }
        case 3u: { rgb = vec3<f32>(0.0, x, c); }
        case 4u: { rgb = vec3<f32>(x, 0.0, c); }
        default: { rgb = vec3<f32>(c, 0.0, x); }
    }
    return clamp(rgb + vec3<f32>(m), vec3<f32>(0.0), vec3<f32>(1.0));
}

struct FragOutput { @location(0) color: vec4<f32> };

@fragment
fn fs_main(in: VertexOutput, @builtin(front_facing) is_front: bool) -> FragOutput {
    let raw_N = in.face_normal;
    // Flip normal for back faces so lighting works correctly on the inside
    let N = select(-raw_N, raw_N, is_front);
    let V = normalize(camera.eyePosition.xyz - in.world_pos);
    let NdotV = max(dot(N, V), 0.0);

    let base = hash_to_crystal_color(in.type_hash);

    // Toon diffuse lighting
    let L = normalize(vec3<f32>(0.6, 1.0, 0.8));
    let NdotL = max(dot(N, L), 0.0);
    let derivative = fwidth(NdotL);
    let toonLightColor = toon_lighting(NdotL, derivative);

    // Emissive pulse (breathing glow)
    let type_hash_f = f32(in.type_hash % 100u);
    let pulse = (sin(camera.time * 0.5 + type_hash_f * 0.1) * 0.5 + 0.5) * 0.15;
    let glow = base * pulse;

    var final_rgb: vec3<f32>;

    if (is_front) {
        // Outer shell
        let fresnel = 0.04 + 0.96 * pow(1.0 - NdotV, 5.0);
        let H = normalize(L + V);
        let NdotH = max(dot(N, H), 0.0);
        let spec = toon_specular(NdotH, 0.92, fwidth(NdotH)) * 1.5;
        
        let rim = rim_glow(NdotV, vec3<f32>(0.4, 0.8, 1.0), 3.0, 0.8);
        
        // Multiply base by the Gooch-shifted light color
        let body = (base * toonLightColor) + (vec3<f32>(1.0) * fresnel * 0.2);
        final_rgb = clamp(body + vec3<f32>(spec) + glow + rim, vec3<f32>(0.0), vec3<f32>(2.0));
    } else {
        // Inner shell
        // Darker base to contrast with the bright bubbles inside
        let body = (base * 0.5) * toonLightColor;
        
        // Inner rim (glows towards the edge from inside)
        let rim = rim_glow(NdotV, PAL_CORE, 2.0, 0.5);
        
        // Dense core emission (gets brighter as NdotV approaches 1, meaning looking straight through the center)
        let core = PAL_CORE * pow(NdotV, 4.0) * 0.6;
        
        final_rgb = clamp(body + glow + rim + core, vec3<f32>(0.0), vec3<f32>(2.0));
    }

    // Distance-based atmospheric fog
    let view_depth = in.clip_pos.w;
    let fogged_rgb = atmospheric_fog(final_rgb, view_depth, camera.fogDensity, camera.fogColor.xyz);

    var out: FragOutput;
    out.color = vec4<f32>(fogged_rgb, 1.0);
    return out;
}
