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
    eyePosition: vec3<f32>,
    _pad: f32,
    time: f32,
    screenWidth: f32,
    screenHeight: f32,
    _pad2: f32,
};

@group(0) @binding(0) var<uniform> camera: CameraUniform;
@group(1) @binding(0) var prevFrameTex: texture_2d<f32>;
@group(1) @binding(1) var linearSampler: sampler;

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
fn fs_main(in: VertexOutput) -> FragOutput {
    let N = in.face_normal;
    let V = normalize(camera.eyePosition - in.world_pos);
    let NdotV = max(dot(N, V), 0.0);

    // Schlick Fresnel — steep exponent for that sharp gem edge highlight.
    let fresnel = 0.04 + 0.96 * pow(1.0 - NdotV, 5.0);

    let base = hash_to_crystal_color(in.type_hash);

    // Fake refraction: sample previous frame at an offset UV. clip_pos in the
    // fragment shader is in framebuffer coordinates (WGSL spec) so dividing
    // by (screenWidth, screenHeight) gives us [0,1] UVs directly.
    //
    // Refraction direction: the vector -V refracted through N with η=1/1.5
    // (air→glass), projected onto the screen plane by taking .xy.
    let uv = in.clip_pos.xy / vec2<f32>(camera.screenWidth, camera.screenHeight);
    let refr_dir = refract(-V, N, 0.67);
    // Scale by clip depth so distant crystals distort less than near ones
    // (bigger perspective foreshortening for near objects).
    let offset_scale = 0.02 * in.clip_pos.w;
    let refr_uv = uv + refr_dir.xy * offset_scale;

    // Chromatic dispersion: shift R and B channels by ±1px.
    let px = vec2<f32>(1.0 / camera.screenWidth, 0.0);
    let refr_r = textureSample(prevFrameTex, linearSampler, refr_uv + px).r;
    let refr_g = textureSample(prevFrameTex, linearSampler, refr_uv     ).g;
    let refr_b = textureSample(prevFrameTex, linearSampler, refr_uv - px).b;
    let refr = vec3<f32>(refr_r, refr_g, refr_b);

    // Blinn-Phong specular for a sharp key light highlight.
    let L = normalize(vec3<f32>(0.6, 1.0, 0.8));
    let H = normalize(L + V);
    let spec = pow(max(dot(N, H), 0.0), 128.0) * 2.5;

    // Faint emissive core so crystals glow even against a dark scene.
    let glow = base * (f32(in.type_hash % 100u) / 100.0) * 0.12;

    // Composite: refraction tinted by the crystal's own hue at low fresnel,
    // washed out to white at grazing angles for the sparkle rim.
    let body = mix(refr, base, 0.35) * (1.0 - fresnel) + vec3<f32>(1.0) * fresnel;
    let final_rgb = clamp(body + vec3<f32>(spec) + glow, vec3<f32>(0.0), vec3<f32>(2.0));

    var out: FragOutput;
    out.color = vec4<f32>(final_rgb, 1.0);
    return out;
}
