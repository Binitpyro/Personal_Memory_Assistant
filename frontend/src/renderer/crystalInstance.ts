/**
 * crystalInstance.ts — per-instance crystal motion and colour, in TypeScript.
 *
 * This is a deliberate mirror of the `pcg_hash` / `urand` / `crystal_xform` /
 * `crystal_palette` block in `shaders/common.wgsl` and `shaders/crystal.wgsl`.
 * The WebGPU tier evaluates it on the GPU; the WebGL2 tier has no equivalent
 * hook, so it evaluates the same functions here on the CPU and bakes the result
 * into each InstancedMesh matrix. **Keep the two in sync** — if you retune a
 * rate or a hue band in one, change the other in the same commit, or the two
 * renderers will visibly disagree.
 *
 * Why an integer hash rather than the shared `hash11(f32)`: node `type_hash` is
 * a full-range u32, and `hash11` opens with `fract(p * 0.1031)`, which has no
 * fractional bits left once p exceeds ~8.1e7. That returned exactly 0 for 98.7%
 * of real nodes — nearly every crystal ended up with the same hue and the same
 * rotation axis and speed. Decorrelate before any float cast.
 */

/** PCG output-mixed integer hash. */
export function pcgHash(x: number): number {
    let v = (Math.imul(x >>> 0, 747796405) + 2891336453) >>> 0;
    const s = ((v >>> 28) + 4) >>> 0;
    v = Math.imul(v ^ (v >>> s), 277803737) >>> 0;
    return (v ^ (v >>> 22)) >>> 0;
}

/** Uniform in [0,1). Keeps 24 bits, which a float represents exactly. */
export function urand(x: number, salt: number): number {
    return (pcgHash((x ^ salt) >>> 0) >>> 8) * (1 / 16777216);
}

export interface CrystalXform {
    /** Angular momentum axis, fixed in world space. */
    L: [number, number, number];
    /** Nutation axis, perpendicular to L. */
    perp: [number, number, number];
    /** Nutation (cone half-angle) at time t. */
    theta: number;
    /** Spin about the body symmetry axis. */
    psi: number;
    /** Precession about L. */
    phi: number;
    /** Volume-preserving shape stretch; growth axis is local +Y. */
    scale: [number, number, number];
}

/**
 * Torque-free symmetric top — the motion of a rigid body with nothing acting
 * on it. Both rates are constant, so angular velocity never reverses and never
 * stops, and being incommensurate the orientation never repeats.
 */
export function crystalXform(typeHash: number, t: number): CrystalXform {
    const r0 = urand(typeHash, 0x9e3779b9);
    const r1 = urand(typeHash, 0x85ebca6b);
    const r2 = urand(typeHash, 0xc2b2ae35);
    const r3 = urand(typeHash, 0x27d4eb2f);
    const r4 = urand(typeHash, 0x165667b1);
    const r5 = urand(typeHash, 0xd3a2646c);

    // L uniform on the sphere. A vertical bias would make every crystal
    // pirouette about "up", which reads as a spinning top rather than drift.
    const az = r0 * Math.PI * 2;
    const cz = r1 * 2 - 1;
    const sz = Math.sqrt(Math.max(1 - cz * cz, 0));
    const L: [number, number, number] = [sz * Math.cos(az), cz, sz * Math.sin(az)];

    const rv: [number, number, number] = Math.abs(L[1]) > 0.9 ? [1, 0, 0] : [0, 1, 0];
    let px = L[1] * rv[2] - L[2] * rv[1];
    let py = L[2] * rv[0] - L[0] * rv[2];
    let pz = L[0] * rv[1] - L[1] * rv[0];
    const pl = Math.hypot(px, py, pz) || 1;
    px /= pl; py /= pl; pz /= pl;

    const phiRate = 0.055 + r2 * 0.085;      // rad/s precession
    const theta0  = 0.35 + r3 * 0.55;        // rad cone
    const inertia = 1.30 + r4 * 1.10;        // I1/I3; > 1 for a prolate body
    // Torque-free constraint, rather than an independently invented rate.
    const psiRate = phiRate * Math.cos(theta0) * (inertia - 1);

    // A pure symmetric top traces a perfect circle at a constant rate, which can
    // itself read as machined. Modulating theta de-circularises the polhode —
    // and theta is a POSITION, not an accumulated angle, so this can never drive
    // the angular velocity negative the way the old sinusoid-sum did.
    const wNut = 0.021 + r0 * 0.017;

    const sy = 0.82 + r5 * 0.48;
    const sxz = 1 / Math.sqrt(sy);

    return {
        L,
        perp: [px, py, pz],
        theta: theta0 + 0.16 * Math.sin(wNut * t + r1 * Math.PI * 2),
        // Phases stay bounded — an unbounded per-instance offset added to an
        // accumulating angle is what quantised the old rotation into detents.
        psi: psiRate * t + r2 * Math.PI * 2,
        phi: phiRate * t + r3 * Math.PI * 2,
        scale: [sxz, sy, sxz],
    };
}

/** HSV → RGB, matching common.wgsl's hsv2rgb so both tiers land on one colour. */
function hsv2rgb(h: number, s: number, v: number): [number, number, number] {
    const f = (n: number) => {
        const k = (n + h * 6) % 6;
        return v - v * s * Math.max(0, Math.min(Math.min(k, 4 - k), 1));
    };
    return [f(5), f(3), f(1)];
}

/**
 * Cohesive aurora palette: a dominant cool band (cyan → blue → violet) drawn
 * from the sky the crystals float in, plus a sparse warm accent so the field is
 * not monotonous. Hue is decorative — `type_hash` is unique per folder path and
 * surfaces as a name tooltip; there is no colour legend anywhere in the UI.
 */
export function crystalPalette(typeHash: number): [number, number, number] {
    const r0 = urand(typeHash, 0x2545f491);
    const r1 = urand(typeHash, 0x7feb352d);
    const r2 = urand(typeHash, 0x846ca68b);
    let hue = 0.50 + 0.28 * r0;
    if (r1 > 0.85) hue = (0.93 + 0.06 * r2) % 1;
    return hsv2rgb(hue, 0.40 + 0.28 * r1, 0.60 + 0.24 * r2);
}
