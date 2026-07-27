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
    currentVariant:  u32,           //140
    time:            f32,           //144
    screenWidth:     f32,           //148
    screenHeight:    f32,           //152
    fogDensity:      f32,           //156
    fogColor:        vec3<f32>,     //160
    exposure:        f32,           //172  NEW — HDR key
    focus:           vec3<f32>,     //176  NEW — sun/god-ray anchor
    _pad:            f32,           //188
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
    let height_atten = exp(-max(height + 50.0, 0.0) * 0.002);
    let dens = fog_density * height_atten;
    // Linear distance falloff — gentle enough that near objects stay clear
    // while distant ones fade gracefully into the sky.
    let f = 1.0 - exp(-view_depth * dens);
    return mix(color, fog_color, clamp(f, 0.0, 0.85));
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
