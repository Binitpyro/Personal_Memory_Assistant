// particles_update.wgsl — Compute pass that advects the firefly/dust cloud.
//
// Buffer layout (32 bytes per particle):
//   vec3 position (12) + f32 life (4) + vec3 velocity (12) + f32 seed (4)
//
// Total N = 65,536 particles, workgroup size 64 → 1024 dispatches.
// Each particle is advected by:
//   • curl-noise flow field (divergence-free — no unnatural clumping)
//   • weak radial pull toward camera.focus (keeps them close to the scene)
//   • buoyancy along +Y
// Life decays; when it hits 0 the particle respawns in a sphere around focus.

struct Particle {
    position: vec3<f32>,
    life:     f32,
    velocity: vec3<f32>,
    seed:     f32,
};

struct SimParams {
    dt:       f32,
    time:     f32,
    focus:    vec3<f32>,
    radius:   f32,   // spawn radius around focus
    _pad0:    f32,
    _pad1:    f32,
    _pad2:    f32,
    _pad3:    f32,
};

@group(0) @binding(0) var<storage, read_write> particles: array<Particle>;
@group(0) @binding(1) var<uniform> params: SimParams;

fn respawn(p: Particle, gid: u32) -> Particle {
    let s = f32(gid) * 0.001 + params.time * 0.13;
    // Fibonacci sphere point — uniform distribution without trig-heavy sqrt.
    let phi   = TWO_PI * fract(s * 0.61803398875);
    let costh = 1.0 - 2.0 * fract(s * 0.31830988618);
    let sinth = sqrt(max(0.0, 1.0 - costh * costh));
    let dir = vec3<f32>(sinth * cos(phi), costh, sinth * sin(phi));
    let r = params.radius * (0.4 + 0.6 * hash11(s + 7.7));
    var q: Particle;
    q.position = params.focus + dir * r;
    q.life     = 1.0 + hash11(s + 11.3) * 3.0;   // 1–4 seconds
    q.velocity = curlNoise(q.position * 0.02) * 0.5;
    q.seed     = hash11(s + 91.1);
    return q;
}

@compute @workgroup_size(64)
fn cs_main(@builtin(global_invocation_id) gid_v: vec3<u32>) {
    let gid = gid_v.x;
    if (gid >= arrayLength(&particles)) { return; }
    var p = particles[gid];

    // Respawn hook — either dead OR seeded with life 0 at startup.
    if (p.life <= 0.0) {
        p = respawn(p, gid);
        particles[gid] = p;
        return;
    }

    // Curl-noise-driven turbulence (frame-rate independent).
    let flow = curlNoise(p.position * 0.015 + vec3<f32>(0.0, params.time * 0.05, 0.0));
    // Weak spring toward focus so the swarm stays with the camera.
    let toFocus = params.focus - p.position;
    let dist = length(toFocus);
    let spring = toFocus / max(dist, 0.001) * clamp((dist - params.radius) * 0.03, 0.0, 1.0);
    // Buoyancy (fireflies rise slowly).
    let lift = vec3<f32>(0.0, 0.12, 0.0);

    // Integrate — semi-implicit Euler.
    p.velocity = p.velocity * 0.985 + (flow * 1.6 + spring + lift) * params.dt;
    p.position = p.position + p.velocity * params.dt;
    p.life     = p.life - params.dt;

    particles[gid] = p;
}
