// oit_resolve.wgsl — Resolve Weighted-Blended OIT into SceneColor.
//
// Runs as a fullscreen triangle. Blend state on the pipeline is:
//   src * (1 - dstA) + dst * dstA           (i.e., "over" op with revealage)
//
// This is the McGuire/Bavoil compositing step:
//   final = accum.rgb / max(accum.a, ε)     ← average color, weighted
//   src   = final,  srcA = 1 - reveal
// then blended over SceneColor.

@group(0) @binding(0) var t_accum:  texture_2d<f32>;
@group(0) @binding(1) var t_reveal: texture_2d<f32>;
@group(0) @binding(2) var s_point:  sampler;

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

@fragment
fn fs_main(in: VOut) -> @location(0) vec4<f32> {
    let accum = textureSampleLevel(t_accum,  s_point, in.uv, 0.0);
    let reveal = textureSampleLevel(t_reveal, s_point, in.uv, 0.0).r;

    // If nothing was drawn to this pixel, punch through with alpha=0.
    let alpha = 1.0 - reveal;
    if (alpha < 1e-4) {
        return vec4<f32>(0.0, 0.0, 0.0, 0.0);
    }
    let avg_color = accum.rgb / max(accum.a, 1e-4);
    return vec4<f32>(avg_color, alpha);
}
