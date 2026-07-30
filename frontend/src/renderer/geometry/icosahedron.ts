/**
 * icosahedron.ts — crystal geometry.
 *
 * Production meshes all come from `generateCrystalVariants` -> `makeCrystalHabit`,
 * which grows a single convex body by intersecting half-spaces. The important
 * property is that every bounding plane belongs to one of a few families
 * derived from the crystal's own growth axis — prism ring, primary and
 * secondary termination, basal cut. That is what makes it read as one coherent
 * specimen rather than a chipped rock.
 *
 * A previous version added 1-3 "bevel" planes whose normals were uniform on the
 * sphere. Being unrelated to the growth axis, they sliced arbitrarily across the
 * body and could shear the termination flat — the "sudden cuts". Irregularity
 * now comes only from *within* the families: face distances vary (Steno's law
 * — interfacial angles are lattice-fixed, distances are not), the basal cut is
 * oblique, and one modifying face cuts a single flank back.
 *
 * Invariants other code depends on:
 *   • Every clip plane has d > 0, so the origin stays strictly interior and the
 *     body star-shaped — `build()`'s centroid winding heuristic requires it.
 *   • `build()` normalises the furthest vertex to radius 1.0.
 *   • Facets are flat-shaded with no vertex welding. Hard facet edges are the
 *     point; they are not an artifact.
 *   • The growth axis is canonically local +Y, which is what lets the vertex
 *     shader apply its per-instance stretch as a plain scale along Y.
 *
 * The lapidary archetypes below `generateCrystalShard` are legacy and unused.
 */

export interface MeshData {
    /** Interleaved [x,y,z, nx,ny,nz] per vertex — 24 bytes/vertex */
    vertices: Float32Array;
    indices: Uint16Array;
    vertexCount: number;
    indexCount: number;
}

type V3 = [number, number, number];

const PHI = (1 + Math.sqrt(5)) / 2;

// ── Tiny vec3 helpers (keeps this file self-contained; no gl-matrix dep) ──
const v3sub = (a: V3, b: V3): V3 => [a[0]-b[0], a[1]-b[1], a[2]-b[2]];
const v3cross = (a: V3, b: V3): V3 => [
    a[1]*b[2] - a[2]*b[1],
    a[2]*b[0] - a[0]*b[2],
    a[0]*b[1] - a[1]*b[0],
];
const v3len = (v: V3): number => Math.hypot(v[0], v[1], v[2]);
const v3norm = (v: V3): V3 => {
    const l = v3len(v) || 1;
    return [v[0]/l, v[1]/l, v[2]/l];
};
const v3scale = (v: V3, s: number): V3 => [v[0]*s, v[1]*s, v[2]*s];
const ORIGIN: V3 = [0, 0, 0];
/** Rodrigues — rotate v about unit axis k by theta. */
const v3rot = (v: V3, k: V3, theta: number): V3 => {
    const c = Math.cos(theta), s = Math.sin(theta);
    const kv = k[0]*v[0] + k[1]*v[1] + k[2]*v[2];
    return [
        v[0]*c + (k[1]*v[2] - k[2]*v[1])*s + k[0]*kv*(1-c),
        v[1]*c + (k[2]*v[0] - k[0]*v[2])*s + k[1]*kv*(1-c),
        v[2]*c + (k[0]*v[1] - k[1]*v[0])*s + k[2]*kv*(1-c),
    ];
};

// ── Deterministic PRNG (mulberry32) ─────────────────────────────────────
function mulberry32(seed: number) {
    return function () {
        seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
        let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}

/**
 * Build a MeshData from a soup of (verts, faces).
 *   • Normalizes so the furthest vertex sits at radius = 1.0.
 *   • Splits each face into 3 unique verts (flat shading).
 *   • Computes face normals from cross product; if pointing inward
 *     (dot(normal, centroid) < 0) flips the winding.
 */
function build(verts: V3[], faces: [number, number, number][], pivots?: V3[]): MeshData {
    let maxR = 0;
    for (const v of verts) maxR = Math.max(maxR, v3len(v));
    if (maxR > 0) {
        const inv = 1 / maxR;
        verts = verts.map(v => v3scale(v, inv));
        // Pivots live in the same space, so they scale with it.
        if (pivots) pivots = pivots.map(v => v3scale(v, inv));
    }

    const vertArray = new Float32Array(faces.length * 3 * 6);
    const idxArray  = new Uint16Array(faces.length * 3);
    let vi = 0;

    for (let fi = 0; fi < faces.length; fi++) {
        const [ia, ib, ic] = faces[fi];
        let a = verts[ia], b = verts[ib], c = verts[ic];
        const e1 = v3sub(b, a);
        const e2 = v3sub(c, a);
        const cr = v3cross(e1, e2);
        // Reject slivers. v3norm() guards its division by returning (0,0,0) for
        // a zero-length cross product, and normalize((0,0,0)) in the vertex
        // shader is NaN — which then poisons the entire facet. Intersecting
        // ~20 half-spaces produces the occasional degenerate triangle, so this
        // has to be caught here rather than relied upon not to happen.
        if (v3len(cr) < 1e-9) continue;
        let n = v3norm(cr);

        // Outward test. "Outward" means away from a point known to be INSIDE
        // the body this face belongs to. For a single convex habit that is the
        // origin, but a cluster is a union of convex members sitting off-centre
        // — testing a satellite's faces against the global origin flips roughly
        // half of them inward. So each face carries its own member's interior
        // point; pivots is undefined for the single-body case, which keeps the
        // original origin-relative behaviour.
        const pv = pivots ? pivots[fi] : ORIGIN;
        const centroid: V3 = [
            (a[0]+b[0]+c[0])/3 - pv[0],
            (a[1]+b[1]+c[1])/3 - pv[1],
            (a[2]+b[2]+c[2])/3 - pv[2],
        ];
        if (n[0]*centroid[0] + n[1]*centroid[1] + n[2]*centroid[2] < 0) {
            n = [-n[0], -n[1], -n[2]];
            // Reverse winding too so back-face culling still works
            [b, c] = [c, b];
        }

        for (const v of [a, b, c]) {
            vertArray[vi * 6 + 0] = v[0];
            vertArray[vi * 6 + 1] = v[1];
            vertArray[vi * 6 + 2] = v[2];
            vertArray[vi * 6 + 3] = n[0];
            vertArray[vi * 6 + 4] = n[1];
            vertArray[vi * 6 + 5] = n[2];
            idxArray[vi] = vi;
            vi++;
        }
    }
    // Trim — rejected slivers leave slack at the tail of both arrays.
    return {
        vertices: vertArray.subarray(0, vi * 6),
        indices: idxArray.subarray(0, vi),
        vertexCount: vi,
        indexCount: vi,
    };
}

// ── Archetype 1 — Bipyramid ("classic gem") ─────────────────────────────
// Ring of N verts at y = 0, top apex at +Y, bottom apex at -Y. Slightly
// elongated for a crystalline look.
function makeBipyramid(sides: number, elongation = 1.6, rand = Math.random): { verts: V3[], faces: [number, number, number][] } {
    const verts: V3[] = [];
    const faces: [number, number, number][] = [];
    // Ring
    for (let i = 0; i < sides; i++) {
        const th = (i / sides) * Math.PI * 2 + rand() * 0.15;
        verts.push([Math.cos(th) * 0.7, 0, Math.sin(th) * 0.7]);
    }
    // Girdle bevel (small ring of inset verts at ±0.15 Y for a "table cut")
    const bevelTop = verts.length;
    for (let i = 0; i < sides; i++) {
        const th = (i / sides) * Math.PI * 2 + rand() * 0.15;
        verts.push([Math.cos(th) * 0.55, 0.15, Math.sin(th) * 0.55]);
    }
    const bevelBot = verts.length;
    for (let i = 0; i < sides; i++) {
        const th = (i / sides) * Math.PI * 2 + rand() * 0.15;
        verts.push([Math.cos(th) * 0.55, -0.15, Math.sin(th) * 0.55]);
    }
    const apexTop = verts.length; verts.push([0,  elongation, 0]);
    const apexBot = verts.length; verts.push([0, -elongation * 0.7, 0]);
    for (let i = 0; i < sides; i++) {
        const j = (i + 1) % sides;
        // Girdle strip (2 tris)
        faces.push([i, bevelTop + i, j]);
        faces.push([bevelTop + i, bevelTop + j, j]);
        faces.push([i, j, bevelBot + i]);
        faces.push([bevelBot + i, j, bevelBot + j]);
        // Top pavilion
        faces.push([bevelTop + i, apexTop, bevelTop + j]);
        // Bottom pavilion
        faces.push([bevelBot + i, bevelBot + j, apexBot]);
    }
    return { verts, faces };
}

// ── Archetype 2 — Cluster of shards ─────────────────────────────────────
// Central bipyramid + 4 smaller bipyramids skewed at random angles.
function makeCluster(rand = Math.random): { verts: V3[], faces: [number, number, number][] } {
    const outVerts: V3[] = [];
    const outFaces: [number, number, number][] = [];

    function embed(mesh: { verts: V3[], faces: [number, number, number][] },
                   offset: V3, scale: V3, tilt: number) {
        const base = outVerts.length;
        // Simple axis-aligned tilt (rotate around Z by `tilt`).
        const cs = Math.cos(tilt), sn = Math.sin(tilt);
        for (const v of mesh.verts) {
            const x = v[0] * cs - v[1] * sn;
            const y = v[0] * sn + v[1] * cs;
            const z = v[2];
            outVerts.push([
                x * scale[0] + offset[0],
                y * scale[1] + offset[1],
                z * scale[2] + offset[2],
            ]);
        }
        for (const f of mesh.faces) {
            outFaces.push([f[0]+base, f[1]+base, f[2]+base]);
        }
    }

    // Central big shard
    embed(makeBipyramid(6, 1.5, rand), [0, 0, 0], [1, 1, 1], 0);
    // 4 satellite shards leaning outward
    const N = 4;
    for (let i = 0; i < N; i++) {
        const th = (i / N) * Math.PI * 2 + rand() * 0.3;
        const r = 0.55;
        const off: V3 = [Math.cos(th) * r, -0.35, Math.sin(th) * r];
        const s = 0.4 + rand() * 0.15;
        const tilt = 0.5 + rand() * 0.4;
        embed(makeBipyramid(5, 1.2, rand), off, [s, s * 1.2, s], tilt);
    }

    return { verts: outVerts, faces: outFaces };
}

// ── Archetype 3 — Tall spire ("obsidian dagger") ────────────────────────
function makeSpire(rand = Math.random) {
    return makeBipyramid(4, 2.3 + rand() * 0.3, rand);
}

// ── Archetype 4 — Rough-cut chunk (asymmetric bipyramid + jitter) ───────
function makeRoughCut(rand = Math.random): { verts: V3[], faces: [number, number, number][] } {
    const base = makeBipyramid(7, 1.4, rand);
    // Slight per-vertex jitter (< 8%) — just enough to break perfect symmetry
    // without ruining the flat facets.
    base.verts = base.verts.map(v => [
        v[0] * (1 + (rand() - 0.5) * 0.15),
        v[1] * (1 + (rand() - 0.5) * 0.10),
        v[2] * (1 + (rand() - 0.5) * 0.15),
    ]);
    return base;
}

// ── Archetype 5 — Diamond (broad table, deep pavilion) ──────────────────
function makeDiamond(_rand = Math.random): { verts: V3[], faces: [number, number, number][] } {
    const sides = 8;
    const verts: V3[] = [];
    const faces: [number, number, number][] = [];
    // Table (top hexagonal plane at y = 0.5)
    const tableStart = 0;
    for (let i = 0; i < sides; i++) {
        const th = (i / sides) * Math.PI * 2;
        verts.push([Math.cos(th) * 0.55, 0.55, Math.sin(th) * 0.55]);
    }
    // Crown ring (girdle at y = 0.15, wider)
    const crownStart = verts.length;
    for (let i = 0; i < sides; i++) {
        const th = (i / sides) * Math.PI * 2 + Math.PI / sides;
        verts.push([Math.cos(th) * 0.95, 0.15, Math.sin(th) * 0.95]);
    }
    // Culet (single pointy bottom)
    const culet = verts.length; verts.push([0, -1.3, 0]);
    // Table cap (fan)
    const tableCenter = verts.length; verts.push([0, 0.55, 0]);
    for (let i = 0; i < sides; i++) {
        const j = (i + 1) % sides;
        faces.push([tableCenter, tableStart + i, tableStart + j]);
        // Crown facets (kite pairs)
        faces.push([tableStart + i, crownStart + i, tableStart + j]);
        faces.push([crownStart + i, crownStart + j, tableStart + j]);
        // Pavilion facets
        faces.push([crownStart + i, culet, crownStart + j]);
    }
    return { verts, faces };
}

// ── Archetype 6 — Twinned prism ("two spires fused") ─────────────────────
function makeTwinned(rand = Math.random) {
    const outVerts: V3[] = [];
    const outFaces: [number, number, number][] = [];
    const embed = (mesh: { verts: V3[], faces: [number, number, number][] },
                   off: V3, tilt: number) => {
        const base = outVerts.length;
        const cs = Math.cos(tilt), sn = Math.sin(tilt);
        for (const v of mesh.verts) {
            outVerts.push([
                v[0] * cs - v[2] * sn + off[0],
                v[1] + off[1],
                v[0] * sn + v[2] * cs + off[2],
            ]);
        }
        for (const f of mesh.faces) outFaces.push([f[0]+base, f[1]+base, f[2]+base]);
    };
    embed(makeBipyramid(5, 1.7, rand), [-0.25, 0, 0],  0.35);
    embed(makeBipyramid(5, 1.5, rand), [ 0.28, 0.1, 0], -0.28);
    return { verts: outVerts, faces: outFaces };
}

// ── Natural crystal habit (half-space intersection) ─────────────────────
// A real crystal is not a bundle of separate spikes glued together — it is a
// SINGLE convex body bounded by planar faces, and those faces sit at angles
// fixed by the lattice rather than at random. So that is how this builds one:
// start with an oversized block and slice it with a family of planes.
//
// Face families, mirroring a natural prismatic habit (quartz/tourmaline):
//   • prism faces  — normals perpendicular to the growth axis, ringed around it
//   • termination  — normals tilted toward +c, converging to a point
//   • basal cut    — one plane across -c, where the crystal met its matrix
//
// Per-face distance jitter is what keeps it from looking machined: the facets
// come out different sizes and the outline irregular, while every face stays
// flat and correctly angled. The result is one coherent crystal.

type Poly = V3[];

const v3dot = (a: V3, b: V3): number => a[0]*b[0] + a[1]*b[1] + a[2]*b[2];

/**
 * Clip a convex polyhedron (list of coplanar vertex loops) by the half-space
 * dot(n, x) <= d. Faces are clipped with Sutherland-Hodgman; the new cut face
 * is rebuilt by angularly sorting the intersection points around the plane,
 * which is valid precisely because the body is convex.
 */
function clipConvex(faces: Poly[], n: V3, d: number): Poly[] {
    const out: Poly[] = [];
    const cut: V3[] = [];

    for (const face of faces) {
        const kept: V3[] = [];
        for (let i = 0; i < face.length; i++) {
            const A = face[i];
            const B = face[(i + 1) % face.length];
            const da = v3dot(n, A) - d;
            const db = v3dot(n, B) - d;
            if (da <= 0) kept.push(A);
            if ((da < 0 && db > 0) || (da > 0 && db < 0)) {
                const t = da / (da - db);
                const P: V3 = [
                    A[0] + (B[0] - A[0]) * t,
                    A[1] + (B[1] - A[1]) * t,
                    A[2] + (B[2] - A[2]) * t,
                ];
                kept.push(P);
                cut.push(P);
            }
        }
        if (kept.length >= 3) out.push(kept);
    }

    // Rebuild the face created by the cut.
    if (cut.length >= 3) {
        const c: V3 = [0, 0, 0];
        for (const p of cut) { c[0] += p[0]; c[1] += p[1]; c[2] += p[2]; }
        c[0] /= cut.length; c[1] /= cut.length; c[2] /= cut.length;

        const ref: V3 = Math.abs(n[1]) > 0.9 ? [1, 0, 0] : [0, 1, 0];
        const u = v3norm(v3cross(n, ref));
        const w = v3norm(v3cross(n, u));

        const sorted = cut
            .map(p => {
                const r = v3sub(p, c);
                return { p, a: Math.atan2(v3dot(r, w), v3dot(r, u)) };
            })
            .sort((x, y) => x.a - y.a)
            .map(x => x.p);

        // Drop near-duplicate points introduced by clipping shared edges.
        const dedup: V3[] = [];
        for (const p of sorted) {
            const last = dedup[dedup.length - 1];
            if (!last || v3len(v3sub(p, last)) > 1e-4) dedup.push(p);
        }
        if (dedup.length >= 3 &&
            v3len(v3sub(dedup[0], dedup[dedup.length - 1])) < 1e-4) dedup.pop();
        if (dedup.length >= 3) out.push(dedup);
    }

    return out;
}

/**
 * Grow one crystal, as a set of planar faces in a canonical frame where the
 * growth axis is +Y and the body is star-shaped about the origin.
 *
 * `slender` multiplies the elongation — the dominant crystal of a cluster wants
 * to be longer than the satellites crowding its base.
 */
function growHabit(rand: () => number, slender = 1): Poly[] {
    // Canonical frame: the crystal grows along +Y, so the radial direction at
    // angle th is simply (cos th, 0, sin th). Orientation is applied by the
    // caller (for cluster members) and by the vertex shader's per-instance
    // tumble, so choosing a random axis here would be redundant — and a fixed
    // axis lets the shader apply its stretch as a plain scale along local Y.
    const radial = (th: number): V3 => [Math.cos(th), 0, Math.sin(th)];

    // Start from an oversized block; every plane below carves into it.
    const E = 4.0;
    let faces: Poly[] = [
        [[-E,-E,-E], [-E,-E, E], [-E, E, E], [-E, E,-E]],
        [[ E,-E,-E], [ E, E,-E], [ E, E, E], [ E,-E, E]],
        [[-E,-E,-E], [ E,-E,-E], [ E,-E, E], [-E,-E, E]],
        [[-E, E,-E], [-E, E, E], [ E, E, E], [ E, E,-E]],
        [[-E,-E,-E], [-E, E,-E], [ E, E,-E], [ E,-E,-E]],
        [[-E,-E, E], [ E,-E, E], [ E, E, E], [-E, E, E]],
    ];

    // Hexagonal is the iconic prismatic habit; 8- and 4-sided appear less often.
    const pick = rand();
    const sides = pick < 0.62 ? 6 : (pick < 0.84 ? 8 : 4);

    const radius = 0.42 + rand() * 0.16;
    const angOff = rand() * Math.PI * 2;
    // A slight taper — real crystals narrow toward the termination.
    const taper = 0.05 + rand() * 0.07;

    // ── Prism faces — a ring of planes about the growth axis ──────────────
    // Steno's law: interfacial ANGLES are fixed by the lattice, only the face
    // DISTANCES vary with growth rate. So the angles get pinned hard (±0.025
    // rad, was ±0.15) and the distances keep a generous ±12% — regularising
    // the distances too would just produce a machined hex nut.
    //
    // A prism face is clipped out of existence when d_i/d_j > 1/cos(2pi/sides).
    // At ±12% the worst ratio is 1.12/0.88 = 1.27, against a bound of 1.41 at
    // 8 sides and 2.00 at 6. The OLD combination of ±21% distance and ±0.15 rad
    // angle exceeded that bound and really was dropping faces.
    const faceAng: number[] = [];
    for (let i = 0; i < sides; i++) {
        const th = angOff + (i / sides) * Math.PI * 2 + (rand() - 0.5) * 0.05;
        faceAng.push(th);
        const rd = radial(th);
        faces = clipConvex(faces, v3norm([rd[0], taper, rd[2]]),
                           radius * (0.88 + rand() * 0.24));
    }

    // ── Termination — alternating r and z rhombohedra ─────────────────────
    // A real quartz point is two interpenetrating rhombohedra: r and z share
    // the same tilt off the c axis and sit at ALTERNATING prism azimuths, with
    // r reaching further so it shows as the large rhomb and z as the small.
    // That large/small rhythm is most of what reads as "grown" rather than
    // "turned on a lathe", and it breaks the n-fold symmetry down to 2-fold
    // without introducing a plane unrelated to the crystal's own axes.
    //
    // An earlier attempt put the secondary family at half-step azimuths with a
    // steeper tilt. Instrumenting faces.length showed those planes landed
    // outside the body and cut nothing for 5 of every 6 — a silent no-op.
    // Sharing one ring guarantees every plane bites, since each is the nearest
    // plane at its own azimuth. `sides` is always even, so the alternation
    // closes without two same-parity faces meeting at the wrap.
    const tipTilt = 0.70 + rand() * 0.28;               // ~35-44 deg off c
    const tipDist = 0.86 + rand() * 0.26;               // r — cuts deepest
    const zDist   = tipDist * (1.06 + rand() * 0.10);   // z — cuts less
    for (let i = 0; i < sides; i++) {
        const rd = radial(faceAng[i]);
        // Per-face jitter on top of that: this is what tips the apex off the
        // axis, so the point is not perfectly centred.
        const d = (i % 2 === 0 ? tipDist : zDist) * (0.95 + rand() * 0.10);
        faces = clipConvex(faces, v3norm([rd[0] * tipTilt, 1, rd[2] * tipTilt]), d);
    }

    // ── Basal end ─────────────────────────────────────────────────────────
    // Usually a blunt cut where the crystal met its matrix; sometimes a second
    // termination, giving a double-terminated specimen.
    if (rand() < 0.60) {
        // Oblique, not square to the axis — a crystal rarely breaks off its
        // matrix at a right angle, and the tilt gives the body an asymmetry
        // that is still derived from its own axis system.
        const bt = 0.20 + rand() * 0.22;
        const brd = radial(faceAng[Math.floor(rand() * sides)]);
        faces = clipConvex(faces, v3norm([brd[0] * bt, -1, brd[2] * bt]),
                           0.55 + rand() * 0.35);
    } else {
        const botTilt = 0.78 + rand() * 0.34;
        const botDist = 0.72 + rand() * 0.26;
        for (let i = 0; i < sides; i++) {
            const rd = radial(faceAng[i]);
            faces = clipConvex(faces, v3norm([rd[0] * botTilt, -1, rd[2] * botTilt]),
                               botDist * (0.97 + rand() * 0.06));
        }
    }

    // ── One deliberate asymmetry ──────────────────────────────────────────
    // A single modifying face, drawn from the families already present but set
    // noticeably closer so one flank is cut back. Coherent irregularity —
    // unlike a plane at a random orientation, which reads as damage.
    {
        const rd = radial(faceAng[Math.floor(rand() * sides)]);
        const onTermination = rand() < 0.5;
        faces = onTermination
            ? clipConvex(faces, v3norm([rd[0] * tipTilt, 1, rd[2] * tipTilt]), tipDist * 0.86)
            : clipConvex(faces, v3norm([rd[0], taper, rd[2]]), radius * 0.84);
    }

    // Elongate along the growth axis so the crystal is prismatic, not chunky.
    // An affine scale about the origin keeps every face planar (so facets stay
    // flat) and keeps the body star-shaped about the origin, which build()'s
    // winding heuristic depends on.
    const elong = (1.9 + rand() * 1.4) * slender;
    return faces
        .filter(f => f.length >= 3)
        .map(f => f.map(p => [p[0], p[1] * elong, p[2]] as V3));
}

/**
 * A cluster: one dominant crystal with several smaller ones fanning out of a
 * shared root, the way a real mineral specimen grows on its matrix.
 *
 * Each member is an independent convex habit from growHabit(), rotated onto its
 * own growth direction and translated so its BASE lands on the common root. The
 * union is non-convex, which is fine for rendering (flat facets, back-face
 * culling) but is exactly why build() needs per-face pivots — see there.
 */
function makeCrystalCluster(rand = Math.random): {
    verts: V3[], faces: [number, number, number][], pivots: V3[],
} {
    const verts: V3[] = [];
    const tris: [number, number, number][] = [];
    const pivots: V3[] = [];

    /** Rotate a canonical (+Y) habit onto `dir`, drop its base on `root`, emit. */
    const emit = (poly: Poly[], dir: V3, root: V3, scale: number) => {
        // Rotation carrying +Y onto dir. For dir ~= +Y the cross product
        // degenerates, so fall back to the identity.
        const axis = v3cross([0, 1, 0], dir);
        const alen = v3len(axis);
        const ang = Math.atan2(alen, dir[1]);
        const k: V3 = alen > 1e-6 ? v3scale(axis, 1 / alen) : [1, 0, 0];
        const place = (p: V3): V3 => (alen > 1e-6 ? v3rot(v3scale(p, scale), k, ang)
                                                  : v3scale(p, scale));

        // Lowest point along the growth axis, so the base can be seated exactly
        // on the root rather than guessed at.
        let minY = Infinity;
        for (const f of poly) for (const p of f) minY = Math.min(minY, p[1]);
        const baseOff: V3 = [
            root[0] - dir[0] * minY * scale,
            root[1] - dir[1] * minY * scale,
            root[2] - dir[2] * minY * scale,
        ];

        // The member's interior reference is its canonical origin carried
        // through the same transform — which is exactly baseOff.
        for (const f of poly) {
            const base = verts.length;
            for (const p of f) {
                const q = place(p);
                verts.push([q[0] + baseOff[0], q[1] + baseOff[1], q[2] + baseOff[2]]);
            }
            for (let i = 1; i < f.length - 1; i++) {
                tris.push([base, base + i, base + i + 1]);
                pivots.push(baseOff);
            }
        }
    };

    // Dominant crystal — upright, the most slender of the group.
    const mainPoly = growHabit(rand, 1.15);
    let rootY = Infinity;
    for (const f of mainPoly) for (const p of f) rootY = Math.min(rootY, p[1]);
    const root: V3 = [0, rootY, 0];
    emit(mainPoly, [0, 1, 0], root, 1);

    // Satellites. Azimuths are spread over a full turn with jitter so they do
    // not stack on one side, and each leans further out the smaller it is —
    // small crystals nucleate on the flanks of the big one and grow outward.
    const count = 2 + Math.floor(rand() * 4);          // 2-5 companions
    const azBase = rand() * Math.PI * 2;
    for (let i = 0; i < count; i++) {
        const az = azBase + (i / count) * Math.PI * 2 + (rand() - 0.5) * 0.7;
        const scale = 0.34 + rand() * 0.36;
        // Smaller members lean out harder.
        const tilt = 0.30 + (1 - scale) * 0.55 + rand() * 0.25;
        const dir: V3 = [
            Math.sin(tilt) * Math.cos(az),
            Math.cos(tilt),
            Math.sin(tilt) * Math.sin(az),
        ];
        // Seat the base slightly off the exact root and a little up the main
        // crystal's flank, so they read as grown ON it rather than through it.
        const off = 0.10 + rand() * 0.30;
        const climb = rand() * 0.45;
        const memberRoot: V3 = [
            Math.cos(az) * off,
            root[1] + climb,
            Math.sin(az) * off,
        ];
        emit(growHabit(rand, 0.85), dir, memberRoot, scale);
    }

    return { verts, faces: tris, pivots };
}

/** Public: generate a single crystal MeshData with the given archetype id. */
export function generateCrystalShard(archetype = 0, seed = 1337): MeshData {
    const rand = mulberry32(seed);
    let mesh;
    switch (archetype % 6) {
        case 0: mesh = makeBipyramid(6, 1.6, rand); break;
        case 1: mesh = makeCluster(rand); break;
        case 2: mesh = makeSpire(rand); break;
        case 3: mesh = makeRoughCut(rand); break;
        case 4: mesh = makeDiamond(rand); break;
        default: mesh = makeTwinned(rand); break;
    }
    return build(mesh.verts, mesh.faces);
}

/**
 * Generate the 3 mesh variants used by the WebGPU instance pipeline. Each
 * gets a distinct archetype + a distinct PRNG seed so no two visible
 * crystals of adjacent variant indices look the same.
 */
/**
 * How many distinct crystal meshes the scene carries. Both renderers must
 * agree, since instances are bucketed by `type_hash % CRYSTAL_VARIANTS`.
 * At 3 the repetition was obvious; the meshes are only ~60-100 triangles each,
 * so more of them is essentially free.
 */
export const CRYSTAL_VARIANTS = 10;

export function generateCrystalVariants(count: number): MeshData[] {
    // Every variant is an independently-grown crystal habit. The lapidary
    // archetypes behind generateCrystalShard() are deliberately NOT used here:
    // faceted gem cuts are radially symmetric and read as spinning tops once
    // the instance rotation kicks in.
    const seeds = [1337, 42, 7919, 20260725, 8675309, 271828];
    const variants: MeshData[] = [];
    for (let i = 0; i < count; i++) {
        const seed = seeds[i % seeds.length] + i * 104729;
        const mesh = makeCrystalCluster(mulberry32(seed));
        variants.push(build(mesh.verts, mesh.faces, mesh.pivots));
    }
    return variants;
}

/** Legacy export — preserved so anything importing it still compiles. */
export function generateIcosahedron(subdivisions = 1): MeshData {
    // Keep the classic 20-face icosahedron available (used nowhere in the
    // overhaul, but the original API is public).
    const verts: V3[] = [
        [-1,PHI,0],[1,PHI,0],[-1,-PHI,0],[1,-PHI,0],
        [0,-1,PHI],[0,1,PHI],[0,-1,-PHI],[0,1,-PHI],
        [PHI,0,-1],[PHI,0,1],[-PHI,0,-1],[-PHI,0,1],
    ].map(v => v3norm(v as V3));
    let faces: [number, number, number][] = [
        [0,11,5],[0,5,1],[0,1,7],[0,7,10],[0,10,11],
        [1,5,9],[5,11,4],[11,10,2],[10,7,6],[7,1,8],
        [3,9,4],[3,4,2],[3,2,6],[3,6,8],[3,8,9],
        [4,9,5],[2,4,11],[6,2,10],[8,6,7],[9,8,1],
    ];
    // (subdivisions unused — legacy path only)
    void subdivisions;
    return build(verts, faces);
}
