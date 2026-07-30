// crystal.wgsl — PBR/refractive crystal shader for folder nodes.
//
// Design goal: LOOK LIKE ACTUAL CRYSTALS — sharp flat facets catching light
// at distinct angles, glowing interior scattering, rainbow dispersion at
// grazing edges, and smooth organic floating rotation.
//
// Energy conservation: Fresnel partitions reflection vs transmission.
// All additive terms are bounded so ACES tonemapping preserves hue.
//
// Rotation: torque-free symmetric-top tumble — see crystal_xform() in
// common.wgsl. Constant spin and precession rates, so angular velocity never
// reverses and never stops; being incommensurate, the pose never repeats.
//
// Everything per-instance is keyed off an INTEGER hash of type_hash. The old
// hash11(f32(type_hash)) collapsed to exactly 0.0 for 98.7% of real nodes
// (f32 has no fractional bits left above ~8.1e7), so almost every crystal in
// the scene shared one hue, one rotation axis and one speed.

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
    // Position in the crystal's OWN frame (un-rotated) so the interior noise
    // field stays anchored to the stone instead of sliding through it.
    @location(4) body_pos: vec3<f32>,
    @location(5) @interpolate(flat) inst_center: vec3<f32>,
    @location(6) @interpolate(flat) inst_radius: f32,
    // View direction carried back into that same body frame, so the interior
    // ray-march parallaxes correctly as the camera orbits.
    @location(7) view_body: vec3<f32>,
};

// rotate_axis(), crystal_xform() and friends live in common.wgsl — picking.wgsl
// applies the identical transform, so the pick silhouette matches what is drawn.

@vertex
fn vs_main(in: VertexInput) -> VertexOutput {
    var out: VertexOutput;

    // No variant gate any more — the renderer sorts instances by
    // `type_hash % CRYSTAL_VARIANTS` and issues one draw per contiguous run,
    // so every instance reaching this shader already belongs to the bound mesh.
    let x = crystal_xform(in.inst_type_hash, camera.time);

    let body_pos  = in.local_pos * x.scl;
    let rot_pos   = crystal_rotate(x, body_pos);
    let world_pos = rot_pos * in.inst_radius + in.inst_position;

    out.clip_pos    = camera.viewProj * vec4<f32>(world_pos, 1.0);
    out.world_pos   = world_pos;
    out.face_normal = crystal_normal(x, in.local_normal);
    out.type_hash   = in.inst_type_hash;
    out.view_depth  = out.clip_pos.w;
    out.body_pos    = body_pos;
    out.inst_center = in.inst_position;
    out.inst_radius = in.inst_radius;
    out.view_body   = crystal_rotate_inv(x, normalize(camera.eyePosition - world_pos));
    return out;
}

// ── Palette ──────────────────────────────────────────────────────────────
fn crystal_palette(h: u32) -> vec3<f32> {
    // NB: this used fract(f32(h) * 0.61803398875). type_hash is a full-range
    // u32, and above ~1.4e7 an f32 has no fractional bits left, so that
    // returned exactly 0.0 — one identical red — for 99.8% of real nodes.
    // Golden-ratio spacing only maximises separation for *sequential* n
    // anyway; against a random u32 it buys nothing over a good hash.
    let r0 = urand(h, 0x2545F491u);
    let r1 = urand(h, 0x7FEB352Du);
    let r2 = urand(h, 0x846CA68Bu);
    // A dominant cool band (cyan -> blue -> violet) drawn from the aurora sky
    // these float in, plus a sparse warm accent so the field is not monotonous.
    // A designed set reads as one scene; a full hue wheel reads as a stock
    // gem asset pack. Hue is decorative here — there is no colour legend.
    var hue = 0.50 + 0.28 * r0;
    if (r1 > 0.85) { hue = fract(0.93 + 0.06 * r2); }
    return hsv2rgb(vec3<f32>(hue, 0.40 + 0.28 * r1, 0.60 + 0.24 * r2));
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
    // Normalised: (viewProj * vec4(N,0)).xy drops w, which for a direction is
    // -z_view and is generally nonzero, so the raw magnitude scales with view
    // depth. Left unnormalised the refraction offset changed with camera
    // distance and skewed toward the screen edges.
    // Y-flip: viewProj has Y-up, @builtin(position) has Y-down in fragment shader
    let ndc_raw = (camera.viewProj * vec4<f32>(N, 0.0)).xy;
    let ndc_normal_xy = normalize(ndc_raw * vec2<f32>(1.0, -1.0) + vec2<f32>(1e-6));
    // Monotonic dispersion scale — larger offset for shorter wavelengths (blue > green > red)
    let disp = vec3<f32>(1.0, 1.06, 1.12);
    let base_offset = ndc_normal_xy * refr_strength;
    let uv_r = clamp(screen_uv - base_offset * disp.x, vec2<f32>(0.0), vec2<f32>(1.0));
    let uv_g = clamp(screen_uv - base_offset * disp.y, vec2<f32>(0.0), vec2<f32>(1.0));
    let uv_b = clamp(screen_uv - base_offset * disp.z, vec2<f32>(0.0), vec2<f32>(1.0));
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
    // Smooth saturation instead of clamp(refr_lum * 1.2, 0, 0.45): identical
    // slope at the origin and the same 0.45 asymptote, but no C1 knee. The
    // clamp saturated at refr_lum = 0.375, which the HDR sky exceeds across
    // much of the frame, so its hard edge was visible and — being fed by the
    // *previous* frame — swam a frame behind the camera.
    let refr_mix = 0.45 * (1.0 - exp(-refr_lum * 2.6667));
    let transmission = mix(diffuse, tinted_refr * 0.6, refr_mix);

    // ── Environment reflection ──────────────────────────────────────────
    // Each flat facet mirrors a different part of the aurora sky. This is the
    // difference between "crystal" and "plastic": three hardcoded lights can
    // only ever produce three highlights, whereas a real faceted stone shows
    // the whole sky broken up across its faces, shifting as you orbit.
    // (The WebGL2 tier has had an envMap all along, which is most of why the
    // two renderers looked so unlike each other.)
    let R   = reflect(-V, N);
    let env = sky_reflection(R, camera.time);

    // F partitions energy: (1-F) transmits, F reflects. The environment term
    // belongs INSIDE that split — added on top it would blow the crystals out,
    // and the bloom pass downstream would smear the result.
    var rgb = transmission * (vec3<f32>(1.0) - F_surf) + specular + env * F_surf;

    // ── Interior glow (subsurface scattering) ───────────────────────────
    // Both arguments are now in the crystal's own body frame, so the veils sit
    // inside the stone and parallax as you orbit. Previously this marched along
    // a hardcoded world -Z through an already-rotated position, so the interior
    // counter-rotated with the body and read as pasted on.
    let core = inner_light(in.body_pos, normalize(in.view_body), camera.time,
                           base * vec3<f32>(0.7, 0.6, 0.8));
    // Strongest at center of gem face, fades at edges.
    let core_mask = pow(NdotV, 2.5) * 0.25;
    rgb = rgb + core * core_mask;

    // ── Thin-film iridescence ───────────────────────────────────────────
    // Integer hash — hash11(f32(type_hash) * 0.31) pinned to 0 (a flat 300nm
    // film on every crystal) for the same f32-precision reason as the palette.
    let film_nm = 300.0 + 220.0 * urand(in.type_hash, 0x9E3779B1u);
    let irid = iridescence_belcour(NdotV, film_nm, 1.36);
    // Trimmed 0.18 -> 0.11: the environment term above already delivers a lot
    // of energy at grazing angles, where this and the edge sparkle also peak.
    rgb = rgb + irid * pow(1.0 - NdotV, 4.0) * 0.11;

    // ── Fresnel edge sparkle ────────────────────────────────────────────
    // Subtle white-violet edge glow — the hallmark of polished crystal.
    let edge = pow(1.0 - NdotV, 5.0);
    rgb = rgb + vec3<f32>(0.85, 0.80, 1.0) * edge * 0.12;

    // ── Breathing emissive pulse ────────────────────────────────────────
    let type_hash_f = f32(in.type_hash % 100u);
    let pulse = (sin(camera.time * 0.35 + type_hash_f * 0.1) * 0.5 + 0.5) * 0.06;
    rgb = rgb + base * pulse;

    // ── Ambient ─────────────────────────────────────────────────────────
    // Additive hemispheric term, not max(rgb, base * 0.04). That max was
    // applied per-channel against a *coloured* floor, so the three channels
    // crossed at three different places — up to three hard contours with a hue
    // shift between them, cutting across facets rather than along any edge.
    rgb = rgb + base * 0.030 * (0.55 + 0.45 * N.y);

    // ── Atmospheric fog ─────────────────────────────────────────────────
    rgb = atmospheric_fog(rgb, in.view_depth, in.world_pos.y,
                          camera.fogDensity, camera.fogColor);

    var out: FragOutput;
    out.color = vec4<f32>(rgb, 1.0);
    return out;
}
