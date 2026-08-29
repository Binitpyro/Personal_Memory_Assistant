// common.wgsl — Aurora shared library
// Everything downstream (crystal, bubble, sky, particles, post) is stitched
// on top of this preamble via string-concat in WebGPURenderer.ts. Keep it
// side-effect-free (no bindings, no entry points) — this file must be safe
// to prepend to any shader module.

// ── Camera UBO layout ────────────────────────────────────────────────────
// 112 bytes total; identical to the legacy layout so the TS uploader is
// unchanged. Field order matters — WGSL std140 rules apply.
struct CameraUniform {
    viewProj:        mat4x4<f32>,   //  0
    invViewProj:     mat4x4<f32>,   // 64  NEW — for sky ray-recon & god-rays
    eyePosition:     vec3<f32>,     //128
    // Was `currentVariant`, the discriminant for the old 3-draw crystal gate.
    // Crystals are bucketed by variant on the CPU now, but the field stays so
    // the 192-byte layout and every offset below it are untouched — and it is
    // load-bearing padding for eyePosition's 16-byte alignment regardless.
    _pad0:           u32,           //140
    time:            f32,           //144
    screenWidth:     f32,           //148
    screenHeight:    f32,           //152
    fogDensity:      f32,           //156
    fogColor:        vec3<f32>,     //160
    exposure:        f32,           //172  NEW — HDR key
    focus:           vec3<f32>,     //176  NEW — sun/god-ray anchor
    // P[1][1] = 1/tan(fovy/2). The projection matrix is not otherwise visible
    // to shaders, and viewProj[1][1] is NOT a substitute — it carries the view
    // rotation too, so anything sized by it swings with camera pitch.
    projScaleY:      f32,           //188
};                                  //192 bytes — was 112. See renderer patch.

// Compact for post/particles/sky which don't need the full struct: shaders
// that only care about the UBO fields declare their own alias.

// ── Constants ────────────────────────────────────────────────────────────
const PI       : f32 = 3.14159265358979;
const TWO_PI   : f32 = 6.28318530717958;
const INV_PI   : f32 = 0.31830988618379;
const EPS      : f32 = 1e-5;

// ── Hashing (interleaved-gradient / iq / hash21) ─────────────────────────
// Cheap, no-texture, spectrally-flat-enough hashes used by particles,
// stars, noise, and TAA jitter. Do not swap for LCG — visibly patterned.
fn hash11(p: f32) -> f32 {
    var x = fract(p * 0.1031);
    x = x * (x + 33.33);
    return fract(x * x * 2.0);
}

// ── Integer hashing — REQUIRED for anything keyed on type_hash ───────────
// hash11() takes an f32 and opens with fract(p * 0.1031). Once p exceeds
// 2^23/0.1031 ~= 8.1e7 an f32 has no fractional bits left, so fract() returns
// exactly 0.0. node type_hash is a full-range u32 (rust_core/src/lib.rs:330
// masks a SipHash to 32 bits), so hash11(f32(type_hash)) collapses to 0.0 for
// 98.7% of real nodes — every affected crystal ended up sharing one hue, one
// rotation axis and one speed. Decorrelate in INTEGER space, before any cast.
fn pcg_hash(x: u32) -> u32 {
    var v = x * 747796405u + 2891336453u;
    let s = (v >> 28u) + 4u;
    v = (v ^ (v >> s)) * 277803737u;
    return v ^ (v >> 22u);
}
// Uniform in [0,1). The >>8 keeps 24 bits, which f32 represents exactly —
// f32(pcg_hash(x)) / 4294967296.0 would round the low bits away again.
fn urand(x: u32, salt: u32) -> f32 {
    return f32(pcg_hash(x ^ salt) >> 8u) * (1.0 / 16777216.0);
}
fn hash21(p: vec2<f32>) -> f32 {
    var p3 = fract(vec3<f32>(p.xyx) * 0.1031);
    p3 = p3 + dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
fn hash31(p: vec3<f32>) -> f32 {
    var p3 = fract(p * 0.1031);
    p3 = p3 + dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
fn hash33(p: vec3<f32>) -> vec3<f32> {
    var p3 = fract(p * vec3<f32>(0.1031, 0.1030, 0.0973));
    p3 = p3 + dot(p3, p3.yxz + 33.33);
    return fract((p3.xxy + p3.yxx) * p3.zyx);
}

// ── Value noise + fBm ────────────────────────────────────────────────────
fn vnoise3(p: vec3<f32>) -> f32 {
    let i = floor(p);
    let f = fract(p);
    let u = f * f * (3.0 - 2.0 * f);
    let n000 = hash31(i + vec3<f32>(0.0, 0.0, 0.0));
    let n100 = hash31(i + vec3<f32>(1.0, 0.0, 0.0));
    let n010 = hash31(i + vec3<f32>(0.0, 1.0, 0.0));
    let n110 = hash31(i + vec3<f32>(1.0, 1.0, 0.0));
    let n001 = hash31(i + vec3<f32>(0.0, 0.0, 1.0));
    let n101 = hash31(i + vec3<f32>(1.0, 0.0, 1.0));
    let n011 = hash31(i + vec3<f32>(0.0, 1.0, 1.0));
    let n111 = hash31(i + vec3<f32>(1.0, 1.0, 1.0));
    let a = mix(n000, n100, u.x);
    let b = mix(n010, n110, u.x);
    let c = mix(n001, n101, u.x);
    let d = mix(n011, n111, u.x);
    return mix(mix(a, b, u.y), mix(c, d, u.y), u.z);
}
fn fbm3(p_in: vec3<f32>, octaves: u32) -> f32 {
    var p = p_in;
    var amp = 0.5;
    var sum = 0.0;
    var maxAmp = 0.0;
    for (var i = 0u; i < octaves; i = i + 1u) {
        sum = sum + amp * vnoise3(p);
        maxAmp = maxAmp + amp;
        p = p * 2.03 + vec3<f32>(11.7, 3.1, 5.9);
        amp = amp * 0.5;
    }
    return sum / max(maxAmp, EPS);
}

// Curl of a divergence-free vector field — used to advect particles along
// smooth, incompressible flow. Fast finite-diff on fbm3 (Bridson 2007).
fn curlNoise(p: vec3<f32>) -> vec3<f32> {
    let e = 0.35;
    let dx = vec3<f32>(e, 0.0, 0.0);
    let dy = vec3<f32>(0.0, e, 0.0);
    let dz = vec3<f32>(0.0, 0.0, e);
    let px0 = fbm3(p - dx, 3u); let px1 = fbm3(p + dx, 3u);
    let py0 = fbm3(p - dy, 3u); let py1 = fbm3(p + dy, 3u);
    let pz0 = fbm3(p - dz, 3u); let pz1 = fbm3(p + dz, 3u);
    let x = (py1 - py0) - (pz1 - pz0);
    let y = (pz1 - pz0) - (px1 - px0);
    let z = (px1 - px0) - (py1 - py0);
    return vec3<f32>(x, y, z) / (2.0 * e);
}

// ── Color transforms — HDR linear pipeline ───────────────────────────────
// The swap-chain is bgra8unorm (sRGB) but every intermediate render target
// is rgba16f in *linear* space. sRGB<->linear only happens at write-out.

fn srgb_to_linear(c: vec3<f32>) -> vec3<f32> {
    let cutoff = step(vec3<f32>(0.04045), c);
    let hi = pow((c + 0.055) / 1.055, vec3<f32>(2.4));
    let lo = c / 12.92;
    return mix(lo, hi, cutoff);
}
fn linear_to_srgb(c: vec3<f32>) -> vec3<f32> {
    let cutoff = step(vec3<f32>(0.0031308), c);
    let hi = 1.055 * pow(c, vec3<f32>(1.0 / 2.4)) - 0.055;
    let lo = c * 12.92;
    return mix(lo, hi, cutoff);
}

// ACES filmic — Krzysztof Narkowicz 2015 fit. Fastest ACES that stays true
// to the reference tone-mapping curve for the mid-tones we care about.
fn aces(x: vec3<f32>) -> vec3<f32> {
    let a = 2.51;
    let b = 0.03;
    let c = 2.43;
    let d = 0.59;
    let e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), vec3<f32>(0.0), vec3<f32>(1.0));
}

// HSV → RGB — for hue-shifted crystal palettes without maintaining a LUT.
fn hsv2rgb(hsv: vec3<f32>) -> vec3<f32> {
    let K = vec4<f32>(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
    let p = abs(fract(vec3<f32>(hsv.x) + K.xyz) * 6.0 - vec3<f32>(K.w));
    return hsv.z * mix(vec3<f32>(K.x), clamp(p - vec3<f32>(K.x), vec3<f32>(0.0), vec3<f32>(1.0)), hsv.y);
}

// ── Sky ──────────────────────────────────────────────────────────────────
// These two layers live here, rather than in aurora_sky.wgsl, because the
// crystals reflect them. common.wgsl is prepended to aurora_sky.wgsl as well,
// so this is a MOVE — leaving copies behind would be a duplicate-symbol error.

// Background wash. Two-stop vertical gradient in linear RGB.
fn sky_gradient(dir: vec3<f32>) -> vec3<f32> {
    let y = clamp(dir.y * 0.5 + 0.5, 0.0, 1.0);
    // Deep indigo #0a0a2e → violet #2a1a5e → soft magenta zenith #5a2e7a
    let horizon = PMA_SKY_HORIZON;
    let mid     = PMA_SKY_MID;
    let zenith  = PMA_SKY_ZENITH;
    let a = mix(horizon, mid, smoothstep(0.0, 0.55, y));
    return mix(a, zenith, smoothstep(0.45, 1.0, y));
}

// Aurora ribbons — slow-rolling sine sheets in the upper hemisphere.
fn aurora(dir: vec3<f32>, t: f32) -> vec3<f32> {
    if (dir.y < 0.05) { return vec3<f32>(0.0); }
    let uv = vec2<f32>(atan2(dir.z, dir.x), dir.y);
    let ribbon =
        sin(uv.x * 4.0 + t * 0.35) * 0.5 +
        sin(uv.x * 7.3 - t * 0.22) * 0.3 +
        sin(uv.x * 11.1 + t * 0.11) * 0.2;
    let band = 0.55 + ribbon * 0.15;
    let d = abs(uv.y - band);
    let intensity = exp(-d * 22.0) * smoothstep(0.02, 0.4, uv.y);
    // Aurora green-magenta color shift
    let hue = 0.35 + 0.25 * sin(uv.x * 2.0 + t * 0.15);
    let col = hsv2rgb(vec3<f32>(hue, 0.7, 1.0));
    return col * intensity * 0.6;
}

// Cheap sky for reflection lookups — deliberately NOT the full sky.
//
//   • nebula() is excluded on cost: it calls curlNoise (6x fbm3(3u)) plus
//     fbm3(4u) plus fbm3(2u) — about 24 vnoise3, ~2000 ALU. The sky already
//     pays that once per pixel; paying it again for every crystal pixel would
//     multiply the frame's noise budget several times over.
//   • star_field() is excluded on quality, not cost. Crystal normals are FLAT
//     per facet, so the reflection vector is near-constant across a facet and
//     380-cells-per-axis stars would pop on and off whole facets at once.
//
// Gradient + aurora is ~50 ALU and carries the scene's colour identity, which
// is all a reflection needs to sell.
fn sky_reflection(dir: vec3<f32>, t: f32) -> vec3<f32> {
    return sky_gradient(dir) + aurora(dir, t);
}

// ── PBR building blocks (Cook-Torrance / GGX) ────────────────────────────
fn D_GGX(NdotH: f32, a2: f32) -> f32 {
    let d = (NdotH * a2 - NdotH) * NdotH + 1.0;
    return a2 / (PI * d * d + EPS);
}
fn V_SmithGGX(NdotV: f32, NdotL: f32, a2: f32) -> f32 {
    let ggxV = NdotL * sqrt(NdotV * NdotV * (1.0 - a2) + a2);
    let ggxL = NdotV * sqrt(NdotL * NdotL * (1.0 - a2) + a2);
    return 0.5 / max(ggxV + ggxL, EPS);
}
fn F_Schlick(VdotH: f32, F0: vec3<f32>) -> vec3<f32> {
    let f = pow(clamp(1.0 - VdotH, 0.0, 1.0), 5.0);
    return F0 + (vec3<f32>(1.0) - F0) * f;
}

// Thin-film iridescence — Belcour & Barla 2017 (simplified 3-wavelength).
// dNM is film thickness in nanometres (300–800 → visible spectrum wobble).
fn iridescence_belcour(cosTheta: f32, dNM: f32, ior_film: f32) -> vec3<f32> {
    // Optical path difference for each RGB wavelength (approx. 650/550/440 nm)
    let opd_r = 2.0 * ior_film * dNM * cosTheta;
    let opd_g = opd_r;
    let opd_b = opd_r;
    let phase_r = opd_r * TWO_PI / 650.0;
    let phase_g = opd_g * TWO_PI / 550.0;
    let phase_b = opd_b * TWO_PI / 440.0;
    // Two-wave interference: 0.5 + 0.5·cos(phase) is the intensity envelope
    return vec3<f32>(
        0.5 + 0.5 * cos(phase_r),
        0.5 + 0.5 * cos(phase_g),
        0.5 + 0.5 * cos(phase_b),
    );
}

// ── Fog / haze ───────────────────────────────────────────────────────────
// Smooth exponential distance fog with height-based density. Uses linear
// distance (not squared) so crystals remain visible at typical zoom levels
// (200-2000 units) instead of being 100% fogged to the background.
fn atmospheric_fog(color: vec3<f32>, view_depth: f32, height: f32,
                   fog_density: f32, fog_color: vec3<f32>) -> vec3<f32> {
    // Height attenuation: fog thins as height increases above ground plane.
    let height_atten = exp(-(height + 50.0) * 0.002);
    let dens = fog_density * height_atten;
    // Linear distance falloff — gentle enough that near objects stay clear
    // while distant ones fade gracefully into the sky.
    let f = 1.0 - exp(-view_depth * dens);
    return mix(color, fog_color, clamp(f, 0.0, 0.85));
}

// ── Crystal instance transform ───────────────────────────────────────────
// Shared verbatim by crystal.wgsl and picking.wgsl. Picking draws the same
// silhouette the camera sees only if both apply the identical transform, and
// picking.wgsl's header has always asked for that lockstep — keeping the one
// implementation here makes it structural rather than a promise.

// Rodrigues rotation — rotate v around unit axis k by angle theta.
fn rotate_axis(v: vec3<f32>, k: vec3<f32>, theta: f32) -> vec3<f32> {
    let c = cos(theta);
    let s = sin(theta);
    return v * c + cross(k, v) * s + k * dot(k, v) * (1.0 - c);
}

struct CrystalXform {
    L:     vec3<f32>,   // angular momentum axis — fixed in world space
    perp:  vec3<f32>,   // nutation axis, perpendicular to L
    theta: f32,         // nutation (cone half-angle)
    psi:   f32,         // spin about the body symmetry axis
    phi:   f32,         // precession about L
    scl:   vec3<f32>,   // per-instance shape stretch, growth axis is local +Y
};

// Torque-free symmetric top: the motion of a rigid body with nothing acting on
// it. Both rates are CONSTANT, so angular velocity never reverses and never
// stops, and because they are incommensurate the orientation never repeats.
//
// The old smooth_rotation() summed sinusoids into the *accumulated angle*, so
// its derivative swung through [-0.46, +0.56] rad/s — it spent nearly half its
// time rotating backwards, which reads as hesitant and mechanical.
fn crystal_xform(type_hash: u32, t: f32) -> CrystalXform {
    let r0 = urand(type_hash, 0x9E3779B9u);
    let r1 = urand(type_hash, 0x85EBCA6Bu);
    let r2 = urand(type_hash, 0xC2B2AE35u);
    let r3 = urand(type_hash, 0x27D4EB2Fu);
    let r4 = urand(type_hash, 0x165667B1u);
    let r5 = urand(type_hash, 0xD3A2646Cu);

    // L uniform on the sphere. A vertical bias would make every crystal
    // pirouette about "up", which reads as a spinning top rather than drift.
    let az = r0 * TWO_PI;
    let cz = r1 * 2.0 - 1.0;
    let sz = sqrt(max(1.0 - cz * cz, 0.0));
    let L  = vec3<f32>(sz * cos(az), cz, sz * sin(az));
    let rv = select(vec3<f32>(0.0, 1.0, 0.0), vec3<f32>(1.0, 0.0, 0.0), abs(L.y) > 0.9);

    var x: CrystalXform;
    x.L    = L;
    x.perp = normalize(cross(L, rv));

    let phi_rate = 0.055 + r2 * 0.085;        // 0.055-0.14 rad/s precession
    let theta0   = 0.35  + r3 * 0.55;         // 0.35-0.90 rad cone
    let inertia  = 1.30  + r4 * 1.10;         // I1/I3; >1 for a prolate body
    // Torque-free constraint rather than an independently invented number.
    let psi_rate = phi_rate * cos(theta0) * (inertia - 1.0);

    // A pure symmetric top traces a perfect circle at constant rate, which can
    // itself read as machined. Modulating theta de-circularises the polhode —
    // and theta is a POSITION, not an accumulated angle, so unlike the old code
    // this can never drive the angular velocity negative.
    let w_nut = 0.021 + r0 * 0.017;
    x.theta = theta0 + 0.16 * sin(w_nut * t + r1 * TWO_PI);

    // Phases must stay BOUNDED. The old `+ f32(type_hash) * 0.001` reached
    // ~4e6 rad, where an f32 ulp is 0.25 rad — the angle quantised into ~14
    // degree detents and the crystals visibly ticked between them.
    x.psi = psi_rate * t + r2 * TWO_PI;
    x.phi = phi_rate * t + r3 * TWO_PI;

    // Per-instance shape variation: a volume-preserving stretch along the
    // growth axis, which growHabit() puts at local +Y by construction.
    let sy = 0.82 + r5 * 0.48;
    let sxz = inverseSqrt(sy);
    x.scl = vec3<f32>(sxz, sy, sxz);
    return x;
}

// Body -> world orientation. Order is load-bearing: spin is a BODY rotation and
// must be applied first, then the cone tilt, then precession about the
// space-fixed axis. Reversing it spins the body about the space axis instead
// and drags the cone around with it — a top on a stick, not a tumbling body.
fn crystal_rotate(x: CrystalXform, p: vec3<f32>) -> vec3<f32> {
    var q = rotate_axis(p, x.L,    x.psi);
    q     = rotate_axis(q, x.perp, x.theta);
    return  rotate_axis(q, x.L,    x.phi);
}

/** Inverse orientation — world direction back into the crystal's body frame. */
fn crystal_rotate_inv(x: CrystalXform, p: vec3<f32>) -> vec3<f32> {
    var q = rotate_axis(p, x.L,    -x.phi);
    q     = rotate_axis(q, x.perp, -x.theta);
    return  rotate_axis(q, x.L,    -x.psi);
}

/** Local position -> world-oriented position (before radius scale + translate). */
fn crystal_local_pos(x: CrystalXform, local_pos: vec3<f32>) -> vec3<f32> {
    return crystal_rotate(x, local_pos * x.scl);
}

/** Local normal -> world normal. Reciprocal scale = inverse-transpose of a
 *  diagonal; the rotation is orthogonal so it needs no correction of its own. */
fn crystal_normal(x: CrystalXform, local_normal: vec3<f32>) -> vec3<f32> {
    return normalize(crystal_rotate(x, normalize(local_normal / x.scl)));
}

// ── Fullscreen triangle helper ───────────────────────────────────────────
fn fullscreen_tri(vertexIndex: u32) -> vec4<f32> {
    let uv = vec2<f32>(
        f32((vertexIndex << 1u) & 2u),
        f32(vertexIndex & 2u),
    );
    return vec4<f32>(uv * 2.0 - vec2<f32>(1.0), 0.0, 1.0);
}
fn fullscreen_uv(vertexIndex: u32) -> vec2<f32> {
    let uv = vec2<f32>(
        f32((vertexIndex << 1u) & 2u),
        f32(vertexIndex & 2u),
    );
    return vec2<f32>(uv.x, 1.0 - uv.y);
}
