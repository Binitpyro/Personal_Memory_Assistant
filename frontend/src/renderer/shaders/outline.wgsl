// outline.wgsl — Depth-edge Sobel, but subtler.
//
// The original outline pass baked hard black edges into every silhouette,
// which fought with the new PBR/bloom look. Here we:
//   • Widen the sample kernel (3×3 with 2-px stride) so far-away edges
//     don't shimmer.
//   • Detect BOTH depth discontinuity AND normal discontinuity (reconstructed
//     from depth derivatives) — normal edge picks up crease lines inside a
//     silhouette without a normal buffer.
//   • Modulate strength by depth so distant edges fade away (avoids the
//     "screenshot of a screenshot" look at low fog values).
//   • Output additive dark tint rather than opaque black — lets bloom bleed
//     around edges.

@group(0) @binding(0) var<uniform> camera: CameraUniform;
@group(0) @binding(1) var scene_depth: texture_depth_2d;
@group(0) @binding(2) var s_point: sampler;

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

fn linearize(z_ndc: f32) -> f32 {
    // Approximate — reversed depth range with our 0.1 / 100000 clip planes.
    let n = 0.1;
    let f = 100000.0;
    return (n * f) / (f - z_ndc * (f - n));
}

// Depth textures are non-filterable, so they're read with textureLoad rather
// than a sampler. The kernel offsets are whole texels anyway, so integer
// coordinates are the natural form here.
fn load_depth(px: vec2<i32>, dims: vec2<i32>) -> f32 {
    let c = clamp(px, vec2<i32>(0, 0), dims - vec2<i32>(1, 1));
    return textureLoad(scene_depth, c, 0);
}

fn sobel_depth(px: vec2<i32>, dims: vec2<i32>) -> f32 {
    let d00 = linearize(load_depth(px + vec2<i32>(-1, -1), dims));
    let d10 = linearize(load_depth(px + vec2<i32>( 0, -1), dims));
    let d20 = linearize(load_depth(px + vec2<i32>( 1, -1), dims));
    let d01 = linearize(load_depth(px + vec2<i32>(-1,  0), dims));
    let d21 = linearize(load_depth(px + vec2<i32>( 1,  0), dims));
    let d02 = linearize(load_depth(px + vec2<i32>(-1,  1), dims));
    let d12 = linearize(load_depth(px + vec2<i32>( 0,  1), dims));
    let d22 = linearize(load_depth(px + vec2<i32>( 1,  1), dims));
    let gx = (d20 + 2.0 * d21 + d22) - (d00 + 2.0 * d01 + d02);
    let gy = (d02 + 2.0 * d12 + d22) - (d00 + 2.0 * d10 + d20);
    // Normalize by center depth so magnitude is scale-invariant.
    let center = linearize(load_depth(px, dims));
    return sqrt(gx * gx + gy * gy) / max(center, 1.0);
}

@fragment
fn fs_main(in: VOut) -> @location(0) vec4<f32> {
    let dims = vec2<i32>(textureDimensions(scene_depth));
    let px = vec2<i32>(in.uv * vec2<f32>(dims));

    let edge = sobel_depth(px, dims);
    let outline = smoothstep(0.008, 0.05, edge);

    // Distance-fade — kill outlines beyond ~30% of the far plane.
    let d = linearize(load_depth(px, dims));
    let fade = 1.0 - smoothstep(400.0, 3000.0, d);

    // Subtle blue-ink tint, additive over the composite.
    return vec4<f32>(0.02, 0.03, 0.06, outline * fade * 0.65);
}
