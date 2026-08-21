// tonemap.wgsl — Final composite / tonemap / grade pass.
//
// Inputs (in bind group order):
//   0 : CameraUniform (exposure, time, screen res)
//   1 : scene_color   (rgba16f)  — HDR linear
//   2 : bloom         (rgba16f)  — bloom upsample result
//   3 : godrays       (rgba16f)  — half-res radial-blur output
//   4 : linear_sampler
//
// The chain: exposure → chromatic aberration → additive bloom + god-rays
//         → ACES tonemap → linear→sRGB → vignette → grain → subtle sharpen.
// Writes directly to the swapchain (bgra8unorm-srgb), so the linear→sRGB
// step is manual (not the hardware one) since we want it before grain.

@group(0) @binding(0) var<uniform> camera: CameraUniform;
@group(0) @binding(1) var scene_color: texture_2d<f32>;
@group(0) @binding(2) var bloom_tex:   texture_2d<f32>;
@group(0) @binding(3) var godrays_tex: texture_2d<f32>;
@group(0) @binding(4) var linear_sampler: sampler;

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

// Barrel chromatic aberration — samples R/G/B with different radial offsets.
// Magnitude grows quadratically with distance from center → strong at corners.
fn chromatic_aberration(uv: vec2<f32>) -> vec3<f32> {
    let center = vec2<f32>(0.5);
    let dir = uv - center;
    let d2  = dot(dir, dir);
    let mag = 0.0035 + d2 * 0.020;
    let r = textureSampleLevel(scene_color, linear_sampler, uv - dir * mag        , 0.0).r;
    let g = textureSampleLevel(scene_color, linear_sampler, uv                    , 0.0).g;
    let b = textureSampleLevel(scene_color, linear_sampler, uv + dir * mag        , 0.0).b;
    return vec3<f32>(r, g, b);
}

// Blue-noise-ish grain via hash. Enough to break banding, not enough to distract.
fn grain(uv: vec2<f32>, t: f32) -> f32 {
    return hash21(uv * vec2<f32>(camera.screenWidth, camera.screenHeight) + fract(t) * 1000.0) - 0.5;
}

fn vignette(uv: vec2<f32>) -> f32 {
    let d = distance(uv, vec2<f32>(0.5)) * 1.4;
    return smoothstep(1.15, 0.35, d);
}

@fragment
fn fs_main(in: VOut) -> @location(0) vec4<f32> {
    let center = vec2<f32>(0.5);
    let dir = in.uv - center;
    let d2  = dot(dir, dir);
    let mag = 0.0035 + d2 * 0.020;
    let px = 1.0 / vec2<f32>(camera.screenWidth, camera.screenHeight);

    // CA Taps & Center Sample
    let g_sample = textureSampleLevel(scene_color, linear_sampler, in.uv, 0.0).rgb;
    let r = textureSampleLevel(scene_color, linear_sampler, in.uv - dir * mag, 0.0).r;
    let b = textureSampleLevel(scene_color, linear_sampler, in.uv + dir * mag, 0.0).b;
    var hdr = vec3<f32>(r, g_sample.g, b);

    // Sharpen (unsharp mask) using center sample and 4 orthogonal taps
    let s1 = textureSampleLevel(scene_color, linear_sampler, in.uv + vec2<f32>( px.x, 0.0), 0.0).rgb;
    let s2 = textureSampleLevel(scene_color, linear_sampler, in.uv - vec2<f32>( px.x, 0.0), 0.0).rgb;
    let s3 = textureSampleLevel(scene_color, linear_sampler, in.uv + vec2<f32>(0.0,  px.y), 0.0).rgb;
    let s4 = textureSampleLevel(scene_color, linear_sampler, in.uv - vec2<f32>(0.0,  px.y), 0.0).rgb;
    let blurred = (s1 + s2 + s3 + s4) * 0.25;
    let sharp_detail = (g_sample - blurred) * 0.14;
    hdr = hdr + sharp_detail;

    // ── Composite Bloom & GodRays ──────────────────────────────────────
    let bl  = textureSampleLevel(bloom_tex,   linear_sampler, in.uv, 0.0).rgb;
    let gr  = textureSampleLevel(godrays_tex, linear_sampler, in.uv, 0.0).rgb;

    hdr = hdr + bl * 0.35;
    hdr = hdr + gr * (1.0 - hdr) * 0.22;
    hdr = hdr * camera.exposure;

    // ── Tonemap (ACES) & Linear → sRGB ─────────────────────────────────
    var sdr = linear_to_srgb(aces(hdr));

    // ── Vignette ───────────────────────────────────────────────────────
    sdr = sdr * mix(0.7, 1.0, vignette(in.uv));

    // ── Grain ──────────────────────────────────────────────────────────
    sdr = sdr + vec3<f32>(grain(in.uv, camera.time)) * 0.025;

    return vec4<f32>(clamp(sdr, vec3<f32>(0.0), vec3<f32>(1.0)), 1.0);
}
