// particles_draw.wgsl — Additive point-sprite draw for the particle cloud.
//
// One draw call, N particles × 6 verts (two triangles per quad) — no vertex
// buffer needed, positions read from the storage buffer using vertex_index.
// Depth-tested vs SceneDepth but never writes depth (particles are additive
// contributions to color only). Soft-particles fade out when their z is
// within `soft_edge` of the surface below to avoid the hard "cut" look.

struct Particle {
    position: vec3<f32>,
    life:     f32,
    velocity: vec3<f32>,
    seed:     f32,
};

@group(0) @binding(0) var<uniform> camera: CameraUniform;
@group(0) @binding(1) var<storage, read> particles: array<Particle>;
@group(0) @binding(2) var scene_depth: texture_depth_2d;
@group(0) @binding(3) var linear_sampler: sampler;

struct VOut {
    @builtin(position) clip_pos: vec4<f32>,
    @location(0) uv:       vec2<f32>,
    @location(1) color:    vec3<f32>,
    @location(2) alpha:    f32,
    @location(3) view_z:   f32,
};

// Per-vertex offset table for the point-sprite quad (two tris).
const CORNERS = array<vec2<f32>, 6>(
    vec2<f32>(-1.0, -1.0),
    vec2<f32>( 1.0, -1.0),
    vec2<f32>(-1.0,  1.0),
    vec2<f32>(-1.0,  1.0),
    vec2<f32>( 1.0, -1.0),
    vec2<f32>( 1.0,  1.0),
);

@vertex
fn vs_main(@builtin(vertex_index) vi: u32) -> VOut {
    let pid  = vi / 6u;
    let cid  = vi % 6u;
    var corners = CORNERS;
    let corner = corners[cid];
    let p = particles[pid];

    // Kill dead particles by collapsing their quad — cheaper than a branch on discard.
    let alive = step(0.001, p.life);

    // World-space size — small, but flares up briefly when just spawned.
    let flare = smoothstep(3.5, 4.0, p.life);
    let size  = mix(0.6, 1.4, flare) * (0.8 + 0.4 * p.seed);

    // Face the camera via view-space billboard.
    let world = vec4<f32>(p.position, 1.0);
    let clip  = camera.viewProj * world;
    let size_world = size * 4.0;
    // Perspective billboard: a world-space half-size s at clip depth w lands at
    // NDC offset s * P[i][i] / w, so the clip-space offset is that times w.
    // The previous form multiplied by w and divided by P[1][1] — inverted on
    // both counts — which cancelled the w entirely and left every sprite ~108x
    // oversized and screen-filling. 65k of those hung the GPU outright
    // (DXGI_ERROR_DEVICE_HUNG). It also read P out of viewProj, which folds in
    // camera pitch; the projection alone belongs here.
    let aspect = camera.screenWidth / max(camera.screenHeight, 1.0);
    let w = max(clip.w, 1e-3);
    // Cap the on-screen radius so a particle drifting into the near field can
    // not walk back off the overdraw cliff.
    let ndc_h = min(size_world * camera.projScaleY / w, 0.12);
    let clip_offset = vec4<f32>(corner * ndc_h * vec2<f32>(1.0 / aspect, 1.0) * w, 0.0, 0.0);

    var o: VOut;
    o.clip_pos = clip + clip_offset * alive;
    o.uv       = corner * 0.5 + vec2<f32>(0.5);
    // Hue: 90% warm amber (fireflies), 10% cool cyan (dust motes) — dictated
    // by seed. Amplitude modulated by life so freshly-born particles glow.
    let cool = step(0.90, p.seed);
    let warm = PMA_MOTE_WARM;
    let cyan = PMA_MOTE_COOL;
    o.color  = mix(warm, cyan, cool) * (0.6 + 0.4 * sin(camera.time * 4.0 + p.seed * TWO_PI));
    // Alpha: strong at spawn, fades with life.
    let normLife = clamp(p.life / 4.0, 0.0, 1.0);
    o.alpha  = alive * (0.5 + 0.5 * sin(camera.time * 2.5 + p.seed * TWO_PI)) * pow(normLife, 0.5) * 0.75;
    o.view_z = clip.w;
    return o;
}

@fragment
fn fs_main(in: VOut) -> @location(0) vec4<f32> {
    // Gaussian falloff — classic soft point sprite.
    let d = length(in.uv - vec2<f32>(0.5)) * 2.0;
    if (d > 1.0) { discard; }
    let core = exp(-d * d * 4.5);

    // Soft particle: fade near opaque geometry.
    // Depth is read with textureLoad — depth textures are non-filterable, so
    // they can't share the filtering `linear_sampler` with the color targets.
    // clip_pos.xy is already in framebuffer pixels, so it doubles as the texel.
    let depth_dims = vec2<i32>(textureDimensions(scene_depth));
    let depth_px = clamp(vec2<i32>(in.clip_pos.xy), vec2<i32>(0, 0), depth_dims - vec2<i32>(1, 1));
    let scene_z_ndc = textureLoad(scene_depth, depth_px, 0);
    // Linearize both depths for a meaningful world-space comparison.
    // near = 0.1, far = 100000 — matching projection in WebGPURenderer.
    let near = 0.1;
    let far  = 100000.0;
    let nf   = near * far;
    let fmn  = far - near;
    let scene_z_lin = nf / (far - scene_z_ndc * fmn);
    let frag_z_lin  = nf / (far - in.clip_pos.z * fmn);
    let soft = clamp((scene_z_lin - frag_z_lin) / 8.0, 0.0, 1.0);

    let rgb = in.color * core * 3.5;  // >1 → gets picked up by bloom
    return vec4<f32>(rgb, in.alpha * core * soft);
}
