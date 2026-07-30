// aurora_sky.wgsl — Fullscreen dreamscape sky dome
// Renders BEFORE anything else, filling SceneColor with:
//   • A vertical dual-gradient (deep indigo horizon → violet zenith)
//   • Twinkling star field (hash21 · smoothstep, size proportional to log-luminance)
//   • Nebula: 3-octave fBm modulated by curl noise for wispy strands
//   • Slow orbital "aurora" ribbons — sine over reconstructed view-ray angle
//   • Subtle ground fog against -Y — sells the fact that the crystals sit on nothing
//
// Reconstructs the world-space view ray from the inverse view-proj so the
// sky rotates correctly with the camera. Runs as a single fullscreen tri
// with no vertex buffer — depth writes disabled, depthCompare = always.

@group(0) @binding(0) var<uniform> camera: CameraUniform;

struct VOut {
    @builtin(position) clip_pos: vec4<f32>,
    @location(0) uv: vec2<f32>,
    @location(1) view_dir: vec3<f32>,
};

@vertex
fn vs_main(@builtin(vertex_index) vi: u32) -> VOut {
    var out: VOut;
    out.clip_pos = fullscreen_tri(vi);
    out.uv = fullscreen_uv(vi);
    // Reconstruct world-space ray direction from NDC.
    let ndc = vec2<f32>(out.uv.x * 2.0 - 1.0, (1.0 - out.uv.y) * 2.0 - 1.0);
    let far  = camera.invViewProj * vec4<f32>(ndc, 1.0, 1.0);
    let near = camera.invViewProj * vec4<f32>(ndc, 0.0, 1.0);
    out.view_dir = normalize(far.xyz / far.w - near.xyz / near.w);
    return out;
}

// Layer 1 — background wash: sky_gradient() now lives in common.wgsl, because
// the crystals reflect it. Layer 4 (aurora) moved there for the same reason.

// Layer 2 — stars. Cell-based random placement so each cell contributes at
// most one star; magnitude drawn from a power distribution for realistic
// bright-star scarcity. Twinkling is a slow sinusoid keyed on cell hash.
fn star_field(dir: vec3<f32>, t: f32) -> vec3<f32> {
    // Project ray onto a large sphere and quantize into cells
    let uv = vec2<f32>(atan2(dir.z, dir.x) * INV_PI * 0.5 + 0.5, asin(dir.y) * INV_PI + 0.5);
    let cellsPerAxis = 380.0;
    let cell = floor(uv * cellsPerAxis);
    let sub  = fract(uv * cellsPerAxis);
    let r    = hash21(cell);
    // Only ~4% of cells get a star; brightness follows r^4 for scarcity.
    if (r < 0.96) { return vec3<f32>(0.0); }
    let pos = vec2<f32>(hash21(cell + 17.0), hash21(cell + 91.0));
    let d   = distance(sub, pos);
    let sz  = 0.02 + 0.05 * pow(hash21(cell + 3.0), 6.0);
    let core = smoothstep(sz, 0.0, d);
    let halo = smoothstep(sz * 4.0, 0.0, d) * 0.15;
    let twinkle = 0.55 + 0.45 * sin(t * (1.5 + hash21(cell + 7.0) * 3.0) + hash21(cell + 5.0) * TWO_PI);
    // Color temperature — bluer for brighter (Wien-ish), warmer for dim.
    let bright = pow(hash21(cell + 13.0), 4.0);
    let tint = mix(vec3<f32>(1.0, 0.85, 0.72), vec3<f32>(0.78, 0.88, 1.0), bright);
    return (core + halo) * twinkle * tint * (0.6 + bright * 1.4);
}

// Layer 3 — nebula strands. 3-octave fBm coordinate-warped by curl-noise so
// strands feel volumetric instead of "blob-noise slapped on a sphere".
fn nebula(dir: vec3<f32>, t: f32) -> vec3<f32> {
    let p0 = dir * 2.7 + vec3<f32>(0.0, t * 0.008, 0.0);
    let warp = curlNoise(p0 * 0.6) * 0.6;
    let p = p0 + warp;
    let n = fbm3(p, 4u);
    let s = smoothstep(0.42, 0.85, n);
    // Two-tone gradient: teal-cyan drifting into magenta.
    let cA = vec3<f32>(0.10, 0.35, 0.60);
    let cB = vec3<f32>(0.55, 0.18, 0.50);
    let col = mix(cA, cB, smoothstep(0.4, 0.9, fbm3(p * 0.5 + 7.7, 2u)));
    // Fade nebula toward the horizon — it lives in the "high atmosphere"
    let hFade = smoothstep(-0.05, 0.55, dir.y);
    return col * s * 0.55 * hFade;
}

// Layer 4 — aurora ribbons: see common.wgsl (shared with the crystal shader).

// Layer 5 — ground haze. A soft, near-black wash sitting below the horizon
// so the crystal cluster doesn't feel like it's floating on nothing.
fn ground_haze(dir: vec3<f32>) -> vec3<f32> {
    let below = smoothstep(0.1, -0.25, dir.y);
    return vec3<f32>(0.015, 0.010, 0.028) * below;
}

@fragment
fn fs_main(in: VOut) -> @location(0) vec4<f32> {
    let dir = normalize(in.view_dir);
    let t   = camera.time;

    var col = sky_gradient(dir);
    col = col + nebula(dir, t);
    col = col + aurora(dir, t);
    col = col + star_field(dir, t);
    col = col + ground_haze(dir);

    // Sky is already linear HDR — bloom will pick up the bright stars/aurora.
    return vec4<f32>(col, 1.0);
}
