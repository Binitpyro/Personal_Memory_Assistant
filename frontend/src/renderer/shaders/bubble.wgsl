// bubble.wgsl — Overhauled iridescent bubble shader for file nodes.
//
// Writes into Weighted-Blended OIT targets (McGuire & Bavoil 2013) instead
// of the old two-pass alpha blend. That kills the "bubble in front of
// bubble" sort artifacts and lets us render bubbles in ANY order.
//
// The classic WBOIT contract:
//   accum target  = rgba16f  (premultiplied color · weight, weight)
//   reveal target = r8       (1 - alpha)  → resolve does 1 - reveal
//
// Weight = alpha · z-dependent function. We use the "Meshkin" variant:
//   w(z, α) = α · clamp(10 / (1e-5 + pow(depth, 3.0) + pow(depth * 0.1, 3.0)))
// which gives a smooth falloff without blowing up near/far.

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
    @location(0) world_pos: vec3<f32>,
    @location(1) world_normal: vec3<f32>,
    @location(2) @interpolate(flat) type_hash: u32,
    @location(3) view_depth: f32,
    @location(4) @interpolate(flat) inst_radius: f32,
    @location(5) @interpolate(flat) inst_hash: u32,
};

// Small Gerstner-inspired surface wobble — sum of two sine waves along
// orthogonal directions makes the sphere breathe rather than pulse.
fn surface_wobble(local_pos: vec3<f32>, seed: f32, t: f32) -> f32 {
    let s1 = sin(t * 1.3 + seed * 0.7  + local_pos.x * 4.0 + local_pos.y * 3.1);
    let s2 = sin(t * 0.9 + seed * 0.31 + local_pos.z * 5.0 - local_pos.y * 2.2);
    return (s1 + s2) * 0.5;
}

@vertex
fn vs_main(in: VertexInput) -> VertexOutput {
    var out: VertexOutput;
    let seed = f32(in.inst_idx * 1234u + in.inst_type_hash % 100u);
    let t = camera.time;

    // Idle drift — sinusoidal along all three axes, seeded per-instance.
    let drift_amp = in.inst_radius * 0.02;
    let drift = vec3<f32>(
        sin(t * 0.4 + seed * 0.11) * drift_amp,
        cos(t * 0.3 + seed * 0.13) * drift_amp,
        sin(t * 0.5 + seed * 0.09) * drift_amp,
    );

    // Breathing scale + surface wobble → soap-bubble life.
    let breathe = 1.0 + sin(t * 1.5 + seed * 0.2) * 0.025;
    let wob = surface_wobble(in.local_pos, seed, t) * 0.025;
    let displaced = in.local_pos * (breathe + wob);

    let world_pos = displaced * in.inst_radius + in.inst_position + drift;
    out.clip_pos    = camera.viewProj * vec4<f32>(world_pos, 1.0);
    out.world_pos   = world_pos;
    out.world_normal = normalize(in.local_normal); // sphere: normal = position
    out.type_hash   = in.inst_type_hash;
    out.view_depth  = out.clip_pos.w;
    out.inst_radius = in.inst_radius;
    out.inst_hash   = in.inst_type_hash;
    return out;
}

// WBOIT weight function (Meshkin variant, normalized to scene scale)
fn wboit_weight(alpha: f32, view_z: f32) -> f32 {
    let z_norm = clamp(view_z / 2000.0, 0.0, 1.0);
    let w = max(1e-2, 3e3 * pow(1.0 - z_norm, 3.0));
    return alpha * w;
}

// Two dual outputs — accum & reveal.
struct FragOut {
    @location(0) accum:  vec4<f32>,
    @location(1) reveal: f32,
};

@fragment
fn fs_main(in: VertexOutput) -> FragOut {
    let N = normalize(in.world_normal);
    let V = normalize(camera.eyePosition - in.world_pos);
    let NdotV = clamp(dot(N, V), 0.0, 1.0);

    // Per-instance hue — files get pastel palette (rose/peach/mint/lilac)
    let hue = fract(f32(in.inst_hash) * 0.61803398875 + 0.05);
    let base_full = hsv2rgb(vec3<f32>(hue, 0.35, 1.0));
    // Pull toward warm-pastel to keep the whole cluster harmonic.
    let base = mix(PMA_BUBBLE_BASE, base_full, 0.55);

    // Three-point lighting matching the crystals (identical directions so
    // the two mesh types read as being in the same world).
    let L_key  = normalize(vec3<f32>( 0.60,  0.95,  0.55));
    let L_fill = normalize(vec3<f32>(-0.70,  0.20,  0.30));

    let NdotL_key  = clamp(dot(N, L_key ), 0.0, 1.0);
    let NdotL_fill = clamp(dot(N, L_fill), 0.0, 1.0);

    // Soft wrap diffuse — bubble surface is basically a scattering film.
    let wrap_key  = clamp(NdotL_key  * 0.5 + 0.5, 0.0, 1.0);
    let wrap_fill = clamp(NdotL_fill * 0.5 + 0.5, 0.0, 1.0);

    let sun_col = PMA_KEY * 1.6;
    let sky_col = PMA_FILL * 0.55;

    let F_surf = F_Schlick(NdotV, vec3<f32>(0.04));
    var diffuse = base * (wrap_key * sun_col + wrap_fill * sky_col) * (vec3<f32>(1.0) - F_surf) * INV_PI;

    // Blinn-Phong-ish shiny highlight — bubbles are ~perfectly smooth.
    let H = normalize(L_key + V);
    let NdotH = clamp(dot(N, H), 0.0, 1.0);
    let spec = pow(NdotH, 96.0) * 2.0;

    // Thin-film iridescence — the point of a bubble.
    // Vary thickness across the surface with a slow noise so hues shift
    // as you rotate around.
    let film_nm = 350.0
                + 180.0 * sin(camera.time * 0.35 + f32(in.inst_hash) * 0.017)
                + 120.0 * fbm3(N * 3.5 + vec3<f32>(0.0, camera.time * 0.1, 0.0), 3u);
    let irid = iridescence_belcour(NdotV, film_nm, 1.33);

    // Rim glow — makes bubbles read as glass balls even at low resolution.
    let rim = pow(1.0 - NdotV, 3.0);
    let inner_glow = pow(NdotV, 2.0) * 0.35;

    // Inner tint — very slight backlight because file "bubbles" contain
    // "code content" — hint of amber-warm inside.
    let inner_tint = PMA_BUBBLE_INNER;

    var rgb = diffuse
            + vec3<f32>(spec)
            + irid * (rim * 0.85 + 0.15)     // iridescence peaks at the rim
            + inner_tint * inner_glow;

    // Distance-based atmosphere.
    rgb = atmospheric_fog(rgb, in.view_depth, in.world_pos.y,
                          camera.fogDensity, camera.fogColor);

    // Alpha: mostly transparent center, opaque-ish rim (soap-bubble law).
    // Also modulated by the "depth" of the bubble in the tree via inst_flags
    // isn't available in a simple way, so we lean on rim/NdotV alone.
    let alpha = clamp(rim * 0.65 + 0.10, 0.03, 0.92);

    // Premultiplied color for WBOIT.
    let premult = vec4<f32>(rgb * alpha, alpha);
    let w = wboit_weight(alpha, in.view_depth);

    var o: FragOut;
    o.accum  = vec4<f32>(premult.rgb * w, premult.a * w);
    o.reveal = 1.0 - alpha;
    return o;
}
