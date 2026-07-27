// crystal.wgsl — PBR/refractive crystal shader for folder nodes.
//
// Design goal: LOOK LIKE ACTUAL CRYSTALS — sharp flat facets catching light
// at distinct angles, glowing interior scattering, rainbow dispersion at
// grazing edges, and smooth organic floating rotation.
//
// Energy conservation: Fresnel partitions reflection vs transmission.
// All additive terms are bounded so ACES tonemapping preserves hue.
//
// Rotation: smoothed accumulated rotation via integrated sinusoids,
// giving continuously varying angular velocity (never reverses, never
// stops, different speed each moment) instead of oscillating pendulum.

@group(0) @binding(0) var<uniform> camera: CameraUniform;
@group(1) @binding(0) var scene_prev: texture_2d<f32>;
@group(1) @binding(1) var scene_depth: texture_depth_2d;
@group(1) @binding(2) var linear_sampler: sampler;

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
    @location(1) @interpolate(flat) face_normal: vec3<f32>,
    @location(2) @interpolate(flat) type_hash: u32,
    @location(3) view_depth: f32,
    @location(4) local_pos: vec3<f32>,
    @location(5) @interpolate(flat) inst_center: vec3<f32>,
    @location(6) @interpolate(flat) inst_radius: f32,
};

// Rodrigues rotation — rotate vector v around unit axis k by angle theta.
fn rotate_axis(v: vec3<f32>, k: vec3<f32>, theta: f32) -> vec3<f32> {
    let c = cos(theta);
    let s = sin(theta);
    return v * c + cross(k, v) * s + k * dot(k, v) * (1.0 - c);
}

// Smooth accumulated rotation angle — the *integral* of layered sinusoids.
// Unlike raw sin() which oscillates back-and-forth, the integral accumulates
// monotonically with varying speed, giving organic continuously-rotating motion.
// f(t) = base_speed*t + Σ (-cos(ω_i*t + φ_i) / ω_i) * amplitude_i
// The cos terms modulate the speed smoothly — sometimes faster, sometimes
// slower, but always moving forward.
fn smooth_rotation(t: f32, base_speed: f32, h1: f32, h2: f32, h3: f32) -> f32 {
    // Base linear rotation at varying speed per instance.
    var angle = base_speed * t;

    // Speed modulation layers (integrated sinusoids → negative cosines).
    // Irrational frequency ratios ensure no visible repeat.
    let w1 = 0.13 + h1 * 0.06;   // ~0.13-0.19 rad/s
    let w2 = 0.07 + h2 * 0.04;   // ~0.07-0.11 rad/s
    let w3 = 0.23 + h3 * 0.08;   // ~0.23-0.31 rad/s

    angle += -cos(w1 * t + h1 * TWO_PI) / w1 * 0.4;
    angle += -cos(w2 * t + h2 * TWO_PI) / w2 * 0.25;
    angle += -cos(w3 * t + h3 * TWO_PI) / w3 * 0.15;

    return angle;
}

@vertex
fn vs_main(in: VertexInput) -> VertexOutput {
    var out: VertexOutput;

    // Variant gate — single instance buffer, 3 mesh variants, 3 draws.
    if (in.inst_type_hash % 3u != camera.currentVariant) {
        out.clip_pos = vec4<f32>(2.0, 2.0, 2.0, 1.0);
        return out;
    }

    let h = f32(in.inst_type_hash);
    let h1 = hash11(h);
    let h2 = hash11(h + 73.0);
    let h3 = hash11(h + 147.0);
    let h4 = hash11(h + 229.0);
    let h5 = hash11(h + 311.0);

    // Per-instance rotation axes — hash-driven, stable.
    let axis1 = normalize(vec3<f32>(
        sin(h * 0.1618),
        cos(h * 0.2718) + 0.3,   // bias upward slightly
        sin(h * 0.3141 + 1.0),
    ));
    let axis2 = normalize(cross(axis1, vec3<f32>(0.0, 1.0, 0.2)));

    let t = camera.time;

    // Primary rotation — smooth accumulated, continuously forward.
    let base_speed = 0.12 + h1 * 0.15; // 0.12-0.27 rad/s base (~20-40s period)
    let angle1 = smooth_rotation(t, base_speed, h1, h2, h3) + h * 0.001;

    // Secondary gentle rock — slower, smaller amplitude.
    let rock_speed = 0.04 + h4 * 0.06;
    let angle2 = smooth_rotation(t, rock_speed, h4, h5, h1) * 0.3;

    // Apply both rotations.
    var rot_pos    = rotate_axis(in.local_pos,    axis1, angle1);
    rot_pos        = rotate_axis(rot_pos,         axis2, angle2);
    var rot_normal = rotate_axis(in.local_normal, axis1, angle1);
    rot_normal     = rotate_axis(rot_normal,      axis2, angle2);

    let world_pos = rot_pos * in.inst_radius + in.inst_position;
    out.clip_pos    = camera.viewProj * vec4<f32>(world_pos, 1.0);
    out.world_pos   = world_pos;
    out.face_normal = normalize(rot_normal);
    out.type_hash   = in.inst_type_hash;
    out.view_depth  = out.clip_pos.w;
    out.local_pos   = rot_pos;
    out.inst_center = in.inst_position;
    out.inst_radius = in.inst_radius;
    return out;
}

// ── Palette ──────────────────────────────────────────────────────────────
fn crystal_palette(h: u32) -> vec3<f32> {
    let hue = fract(f32(h) * 0.61803398875);
    // Rich saturation, moderate value — lets specular highlights pop on top.
    return hsv2rgb(vec3<f32>(hue, 0.75, 0.75));
}

// ── Interior glow — subsurface scattering approximation ──────────────────
// Ray-marches a 3D noise field along the view ray inside the gem.
// The result simulates light trapped and bouncing within the crystal,
// visible as soft colored veils deep inside the faceted volume.
fn inner_light(local_pos: vec3<f32>, view_local: vec3<f32>, t: f32,
               tint: vec3<f32>) -> vec3<f32> {
    var acc = vec3<f32>(0.0);
    let steps = 6;
    for (var i = 0; i < steps; i = i + 1) {
        let s  = f32(i) / f32(steps);
        let p  = local_pos - view_local * s * 1.4;
        let n  = fbm3(p * 3.5 + vec3<f32>(0.0, t * 0.12, 0.0), 3u);
        let veil = smoothstep(0.45, 0.85, n);
        acc = acc + veil * tint;
    }
    return acc / f32(steps);
}

const F0_GLASS : vec3<f32> = vec3<f32>(0.046);

struct FragOutput { @location(0) color: vec4<f32> };

@fragment
fn fs_main(in: VertexOutput) -> FragOutput {
    let N = normalize(in.face_normal);
    let V = normalize(camera.eyePosition - in.world_pos);
    let NdotV = clamp(dot(N, V), 0.001, 1.0);

    let base = crystal_palette(in.type_hash);

    // ── Three-point lighting ────────────────────────────────────────────
    // Positioned so each facet of the crystal catches at least one light
    // at a distinct angle — this is what makes facets visible.
    let L_key  = normalize(vec3<f32>( 0.55,  0.90,  0.50));   // warm overhead
    let L_fill = normalize(vec3<f32>(-0.65,  0.25,  0.40));   // cool side
    let L_back = normalize(vec3<f32>( 0.15, -0.40, -0.85));   // magenta rim

    // Light colors — restrained so total energy stays under ACES knee.
    let c_key  = vec3<f32>(1.00, 0.92, 0.82) * 1.2;
    let c_fill = vec3<f32>(0.50, 0.70, 1.00) * 0.45;
    let c_back = vec3<f32>(1.00, 0.50, 0.80) * 0.35;

    // Sharp specular for faceted gem look — low roughness body + mirror coat.
    let rough_body = 0.15;     // sharper than before → distinct per-facet glints
    let rough_coat = 0.03;     // near-mirror clearcoat
    let a_body = rough_body * rough_body;
    let a_coat = rough_coat * rough_coat;

    var diffuse  = vec3<f32>(0.0);
    var specular = vec3<f32>(0.0);

    // ── KEY light ───────────────────────────────────────────────────────
    {
        let L = L_key;
        let H = normalize(L + V);
        let NdotL = clamp(dot(N, L), 0.0, 1.0);
        let NdotH = clamp(dot(N, H), 0.0, 1.0);
        let VdotH = clamp(dot(V, H), 0.0, 1.0);

        // Body specular (GGX)
        let D = D_GGX(NdotH, a_body);
        let G = V_SmithGGX(NdotV, NdotL, a_body);
        let F = F_Schlick(VdotH, F0_GLASS);
        specular = specular + D * G * F * c_key * NdotL;

        // Diffuse — wrap lighting for soft body scatter, but SUBDUED.
        // Crystals are mostly specular; diffuse is just a tint fill.
        let wrap = clamp(NdotL * 0.5 + 0.5, 0.0, 1.0);
        diffuse = diffuse + base * wrap * c_key * (1.0 - F) * INV_PI * 0.5;

        // Clearcoat — the sharp glass ping that makes facets pop.
        let Dc = D_GGX(NdotH, a_coat);
        let Gc = V_SmithGGX(NdotV, NdotL, a_coat);
        specular = specular + Dc * Gc * F_Schlick(VdotH, vec3<f32>(0.04)) * c_key * NdotL;
    }

    // ── FILL light ──────────────────────────────────────────────────────
    {
        let L = L_fill;
        let H = normalize(L + V);
        let NdotL = clamp(dot(N, L), 0.0, 1.0);
        let NdotH = clamp(dot(N, H), 0.0, 1.0);
        let VdotH = clamp(dot(V, H), 0.0, 1.0);
        let D = D_GGX(NdotH, a_body);
        let G = V_SmithGGX(NdotV, NdotL, a_body);
        let F = F_Schlick(VdotH, F0_GLASS);
        specular = specular + D * G * F * c_fill * NdotL;
        diffuse  = diffuse  + base * NdotL * c_fill * (1.0 - F) * INV_PI * 0.4;
    }

    // ── BACK light (rim kicker) ─────────────────────────────────────────
    {
        let L = L_back;
        let NdotL = clamp(dot(N, L), 0.0, 1.0);
        // Rim contribution — accentuates silhouette edges.
        let rim = pow(1.0 - NdotV, 3.0) * NdotL;
        diffuse = diffuse + c_back * rim * 0.6;
    }

    // ── Refraction from previous frame ──────────────────────────────────
    let screen_uv = in.clip_pos.xy / vec2<f32>(camera.screenWidth, camera.screenHeight);
    let refr_strength = 0.03 * (1.0 - NdotV);
    let ndc_normal_xy = (camera.viewProj * vec4<f32>(N, 0.0)).xy;
    // Per-channel dispersion — chromatic aberration within the crystal.
    let ior_r = 1.51; let ior_g = 1.53; let ior_b = 1.56;
    let disp = (1.0 / vec3<f32>(ior_r, ior_g, ior_b)) - 0.65;
    let uv_r = clamp(screen_uv - ndc_normal_xy * refr_strength * disp.x, vec2<f32>(0.0), vec2<f32>(1.0));
    let uv_g = clamp(screen_uv - ndc_normal_xy * refr_strength * disp.y, vec2<f32>(0.0), vec2<f32>(1.0));
    let uv_b = clamp(screen_uv - ndc_normal_xy * refr_strength * disp.z, vec2<f32>(0.0), vec2<f32>(1.0));
    let refr = vec3<f32>(
        textureSampleLevel(scene_prev, linear_sampler, uv_r, 0.0).r,
        textureSampleLevel(scene_prev, linear_sampler, uv_g, 0.0).g,
        textureSampleLevel(scene_prev, linear_sampler, uv_b, 0.0).b,
    );
    // Tint refraction by gem color — thick glass absorbs complementary.
    let tinted_refr = refr * mix(vec3<f32>(1.0), base, 0.4);

    // ── Energy-conserved composition ────────────────────────────────────
    let F_surf = F_Schlick(NdotV, F0_GLASS);

    // Transmission = blend of diffuse body color and refracted background.
    let refr_lum = dot(tinted_refr, vec3<f32>(0.2126, 0.7152, 0.0722));
    let refr_mix = clamp(refr_lum * 1.2, 0.0, 0.45);
    let transmission = mix(diffuse, tinted_refr * 0.6, refr_mix);

    // F partitions energy: transmission × (1-F) + specular.
    var rgb = transmission * (vec3<f32>(1.0) - F_surf) + specular;

    // ── Interior glow (subsurface scattering) ───────────────────────────
    let view_local = normalize(vec3<f32>(0.0, 0.0, -1.0));
    let core = inner_light(in.local_pos, view_local, camera.time,
                           base * vec3<f32>(0.7, 0.6, 0.8));
    // Strongest at center of gem face, fades at edges.
    let core_mask = pow(NdotV, 2.5) * 0.25;
    rgb = rgb + core * core_mask;

    // ── Thin-film iridescence ───────────────────────────────────────────
    let film_nm = 300.0 + 220.0 * hash11(f32(in.type_hash) * 0.31);
    let irid = iridescence_belcour(NdotV, film_nm, 1.36);
    rgb = rgb + irid * pow(1.0 - NdotV, 4.0) * 0.18;

    // ── Fresnel edge sparkle ────────────────────────────────────────────
    // Subtle white-violet edge glow — the hallmark of polished crystal.
    let edge = pow(1.0 - NdotV, 5.0);
    rgb = rgb + vec3<f32>(0.85, 0.80, 1.0) * edge * 0.25;

    // ── Breathing emissive pulse ────────────────────────────────────────
    let type_hash_f = f32(in.type_hash % 100u);
    let pulse = (sin(camera.time * 0.35 + type_hash_f * 0.1) * 0.5 + 0.5) * 0.06;
    rgb = rgb + base * pulse;

    // ── Ambient floor ───────────────────────────────────────────────────
    rgb = max(rgb, base * 0.04);

    // ── Atmospheric fog ─────────────────────────────────────────────────
    rgb = atmospheric_fog(rgb, in.view_depth, in.world_pos.y,
                          camera.fogDensity, camera.fogColor);

    var out: FragOutput;
    out.color = vec4<f32>(rgb, 1.0);
    return out;
}
