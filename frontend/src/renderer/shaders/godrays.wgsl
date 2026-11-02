// godrays.wgsl — Radial-blur volumetric shafts.
//
// Two-pass:
//   Pass A (occlusion): renders SceneColor luminance masked by SceneDepth
//     into a half-res r16f target. Bright pixels only survive if they're
//     at "sky depth" (deep). This is our "light source" texture.
//   Pass B (radial):    64-tap radial blur toward focus_ndc, with
//     exponential decay per tap.
//
// The god-ray anchor is `camera.focus` projected to NDC — so rays emanate
// from the currently-focused folder, reinforcing the drill-down UX.

@group(0) @binding(0) var<uniform> camera: CameraUniform;
@group(0) @binding(1) var scene_color: texture_2d<f32>;
@group(0) @binding(2) var scene_depth: texture_depth_2d;
@group(0) @binding(3) var linear_sampler: sampler;

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

// Pass A — occlusion mask. Output goes into a half-res r16f target.
@fragment
fn fs_occlusion(in: VOut) -> @location(0) vec4<f32> {
    // textureLoad, not textureSampleLevel: depth textures are non-filterable
    // and can't share the filtering `linear_sampler` used for scene_color.
    let depth_dims = vec2<i32>(textureDimensions(scene_depth));
    let depth_px = clamp(vec2<i32>(in.uv * vec2<f32>(depth_dims)), vec2<i32>(0, 0), depth_dims - vec2<i32>(1, 1));
    let d = textureLoad(scene_depth, depth_px, 0);
    // Only fragments at the far plane contribute (i.e., sky pixels).
    // Everything opaque gets zeroed → shafts appear from behind geometry.
    let sky_mask = smoothstep(0.995, 1.0, d);
    let c = textureSampleLevel(scene_color, linear_sampler, in.uv, 0.0).rgb;
    // Weighted luminance (Rec.709).
    let luma = dot(c, vec3<f32>(0.2126, 0.7152, 0.0722));
    // Keep only genuinely bright highlights. At the old 0.4 threshold the whole
    // aurora band qualified, so the radial blur smeared it into one hard shaft
    // converging on the focus point instead of subtle shafts behind geometry.
    let lit = max(luma - 1.1, 0.0) * sky_mask;
    return vec4<f32>(vec3<f32>(lit), 1.0);
}

// Pass B — radial blur toward the sun position in screen space.
@fragment
fn fs_radial(in: VOut) -> @location(0) vec4<f32> {
    // Project focus to NDC then to UV.
    let focus_clip = camera.viewProj * vec4<f32>(camera.focus, 1.0);
    let focus_ndc  = focus_clip.xy / max(focus_clip.w, 1e-4);
    let focus_uv   = vec2<f32>(focus_ndc.x * 0.5 + 0.5, 0.5 - focus_ndc.y * 0.5);

    let dir = in.uv - focus_uv;
    let TAPS = 24;
    let density = 0.94;
    let decay   = 0.95;
    let weight  = 0.65;
    let exposure = 0.16;

    var w = 1.0;
    var acc = vec3<f32>(0.0);
    let step_dir = dir * (density / f32(TAPS));

    // Per-pixel jitter to trade banding for imperceptible dither
    let jitter = hash21(in.uv * vec2<f32>(camera.screenWidth, camera.screenHeight) + camera.time * 91.7);
    var uv = in.uv - step_dir * jitter * 0.5;
    for (var i = 0; i < TAPS; i = i + 1) {
        uv = uv - step_dir;
        let s = textureSampleLevel(scene_color, linear_sampler, clamp(uv, vec2<f32>(0.0), vec2<f32>(1.0)), 0.0).rgb;
        acc = acc + s * w * weight;
        w = w * decay;
    }

    // Tint god-rays warm amber to match the sky's aurora highlights.
    let tint = PMA_GODRAY_TINT;
    return vec4<f32>(acc * tint * exposure, 1.0);
}
