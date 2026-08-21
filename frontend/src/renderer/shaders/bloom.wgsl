// bloom.wgsl — Physically-plausible bloom via Kawase down/up-sample chain.
//
// Cheaper and better-looking than a single big Gaussian. The pattern
// (adopted by Call of Duty, Unreal, Godot 4) is:
//   1) Prefilter — bright-pass, karis-average to kill fireflies.
//   2) Downsample chain — 5 mips, each Kawase 5-tap.
//   3) Upsample chain — additive back up with a soft 9-tap tent.
//   4) Composite in tonemap.wgsl.
//
// All three entry points share a single shader file; the pipeline picks
// which one to bind by vs_main + one of {fs_prefilter, fs_down, fs_up}.

@group(0) @binding(0) var src:       texture_2d<f32>;
@group(0) @binding(1) var s_linear:  sampler;

struct Params {
    threshold: f32,   // luminance floor
    knee:      f32,   // soft-knee width
    intensity: f32,   // upsample scalar
    _pad:      f32,
    texel:     vec2<f32>,  // 1 / dst_size
    _pad2:     vec2<f32>,
};
@group(0) @binding(2) var<uniform> params: Params;
@group(0) @binding(3) var base_mip:  texture_2d<f32>;

struct VOut {
    @builtin(position) clip_pos: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@vertex
fn vs_main(@builtin(vertex_index) vi: u32) -> VOut {
    var o: VOut;
    o.clip_pos = fullscreen_tri(vi);
    o.uv = fullscreen_uv(vi);
    return o;
}

fn luminance(c: vec3<f32>) -> f32 {
    return dot(c, vec3<f32>(0.2126, 0.7152, 0.0722));
}
// Karis average — inverse-luminance weighted mean; suppresses single-pixel
// fireflies that would otherwise flicker into the bloom mip chain.
fn karis_avg(a: vec3<f32>, b: vec3<f32>, c: vec3<f32>, d: vec3<f32>) -> vec3<f32> {
    let wa = 1.0 / (1.0 + luminance(a));
    let wb = 1.0 / (1.0 + luminance(b));
    let wc = 1.0 / (1.0 + luminance(c));
    let wd = 1.0 / (1.0 + luminance(d));
    return (a * wa + b * wb + c * wc + d * wd) / (wa + wb + wc + wd);
}

// ── Prefilter — bright-pass with soft knee (Unreal) ─────────────────────
@fragment
fn fs_prefilter(in: VOut) -> @location(0) vec4<f32> {
    let c = textureSampleLevel(src, s_linear, in.uv, 0.0).rgb;
    let br = max(max(c.r, c.g), c.b);
    let knee = params.threshold * params.knee;
    var soft = br - params.threshold + knee;
    soft = clamp(soft, 0.0, 2.0 * knee);
    soft = soft * soft / (4.0 * knee + 1e-4);
    let contribution = max(soft, br - params.threshold) / max(br, 1e-4);
    return vec4<f32>(c * contribution, 1.0);
}

// ── Downsample — 13-tap COD-style, then Karis average of 4 quadrants ────
@fragment
fn fs_down(in: VOut) -> @location(0) vec4<f32> {
    let t = params.texel;
    let uv = in.uv;

    // Central 4 taps at half-offset, then a wider ring of 4 at (2,2), and
    // 4 more at the mid-offsets. Total: 13 taps giving very smooth result.
    let a = textureSampleLevel(src, s_linear, uv + t * vec2<f32>(-1.0,  1.0), 0.0).rgb;
    let b = textureSampleLevel(src, s_linear, uv + t * vec2<f32>( 1.0,  1.0), 0.0).rgb;
    let c = textureSampleLevel(src, s_linear, uv + t * vec2<f32>(-1.0, -1.0), 0.0).rgb;
    let d = textureSampleLevel(src, s_linear, uv + t * vec2<f32>( 1.0, -1.0), 0.0).rgb;

    let e = textureSampleLevel(src, s_linear, uv + t * vec2<f32>(-2.0,  2.0), 0.0).rgb;
    let f = textureSampleLevel(src, s_linear, uv + t * vec2<f32>( 0.0,  2.0), 0.0).rgb;
    let g = textureSampleLevel(src, s_linear, uv + t * vec2<f32>( 2.0,  2.0), 0.0).rgb;
    let h = textureSampleLevel(src, s_linear, uv + t * vec2<f32>(-2.0,  0.0), 0.0).rgb;
    let i = textureSampleLevel(src, s_linear, uv + t * vec2<f32>( 0.0,  0.0), 0.0).rgb;
    let j = textureSampleLevel(src, s_linear, uv + t * vec2<f32>( 2.0,  0.0), 0.0).rgb;
    let k = textureSampleLevel(src, s_linear, uv + t * vec2<f32>(-2.0, -2.0), 0.0).rgb;
    let l = textureSampleLevel(src, s_linear, uv + t * vec2<f32>( 0.0, -2.0), 0.0).rgb;
    let m = textureSampleLevel(src, s_linear, uv + t * vec2<f32>( 2.0, -2.0), 0.0).rgb;

    // 5 Karis-averaged quadrants (top-left, top-right, bot-left, bot-right, center)
    let q0 = karis_avg(e, f, h, i) * 0.125;
    let q1 = karis_avg(f, g, i, j) * 0.125;
    let q2 = karis_avg(h, i, k, l) * 0.125;
    let q3 = karis_avg(i, j, l, m) * 0.125;
    let qc = karis_avg(a, b, c, d) * 0.5;

    return vec4<f32>(q0 + q1 + q2 + q3 + qc, 1.0);
}

// ── Upsample — 9-tap tent filter, additive blended over the parent mip ──
@fragment
fn fs_up(in: VOut) -> @location(0) vec4<f32> {
    let t = params.texel;
    let uv = in.uv;
    var col = vec3<f32>(0.0);
    col = col + textureSampleLevel(src, s_linear, uv + t * vec2<f32>(-1.0,  1.0), 0.0).rgb * 1.0;
    col = col + textureSampleLevel(src, s_linear, uv + t * vec2<f32>( 0.0,  1.0), 0.0).rgb * 2.0;
    col = col + textureSampleLevel(src, s_linear, uv + t * vec2<f32>( 1.0,  1.0), 0.0).rgb * 1.0;
    col = col + textureSampleLevel(src, s_linear, uv + t * vec2<f32>(-1.0,  0.0), 0.0).rgb * 2.0;
    col = col + textureSampleLevel(src, s_linear, uv + t * vec2<f32>( 0.0,  0.0), 0.0).rgb * 4.0;
    col = col + textureSampleLevel(src, s_linear, uv + t * vec2<f32>( 1.0,  0.0), 0.0).rgb * 2.0;
    col = col + textureSampleLevel(src, s_linear, uv + t * vec2<f32>(-1.0, -1.0), 0.0).rgb * 1.0;
    col = col + textureSampleLevel(src, s_linear, uv + t * vec2<f32>( 0.0, -1.0), 0.0).rgb * 2.0;
    col = col + textureSampleLevel(src, s_linear, uv + t * vec2<f32>( 1.0, -1.0), 0.0).rgb * 1.0;
    col = col * (1.0 / 16.0);
    let base = textureSampleLevel(base_mip, s_linear, uv, 0.0).rgb;
    return vec4<f32>(base + col * params.intensity, 1.0);
}
