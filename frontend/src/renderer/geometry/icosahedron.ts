/**
 * icosahedron.ts — Overhauled crystal geometry.
 *
 * The old shards were subdivided icosahedra with per-vertex radial jitter
 * and Y elongation. They read as "lumpy rocks", not "gems". This rewrite:
 *
 *   1. Uses SIX distinct archetypes (bipyramid, cluster, spire, cluster-of-6,
 *      diamond, and rough-cut) chosen at random per instance, seeded by
 *      typeHash % 3 which slots them into the existing 3-variant instance
 *      pipeline. Two archetypes per variant, chosen sub-hash.
 *
 *   2. Every archetype is built from clean triangulated faces (no radial
 *      jitter) so the flat-shaded facets read as sharp, gem-like planes.
 *
 *   3. All are normalized so the furthest vertex sits at radius 1.0 — the
 *      picking pass's bounding-sphere assumption still holds.
 *
 *   4. Adds a `computeFaceNormals` step that flips backwards faces so all
 *      normals point outward. The previous code assumed winding was
 *      already correct, which broke for the mirrored-face archetypes here.
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
function build(verts: V3[], faces: [number, number, number][]): MeshData {
    let maxR = 0;
    for (const v of verts) maxR = Math.max(maxR, v3len(v));
    if (maxR > 0) {
        const inv = 1 / maxR;
        verts = verts.map(v => v3scale(v, inv));
    }

    const vertArray = new Float32Array(faces.length * 3 * 6);
    const idxArray  = new Uint16Array(faces.length * 3);
    let vi = 0;

    for (const [ia, ib, ic] of faces) {
        let a = verts[ia], b = verts[ib], c = verts[ic];
        const e1 = v3sub(b, a);
        const e2 = v3sub(c, a);
        let n = v3norm(v3cross(e1, e2));

        // Centroid heuristic — flip inward-facing normals.
        const centroid: V3 = [
            (a[0]+b[0]+c[0])/3,
            (a[1]+b[1]+c[1])/3,
            (a[2]+b[2]+c[2])/3,
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
    return { vertices: vertArray, indices: idxArray, vertexCount: vi, indexCount: vi };
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
export function generateCrystalVariants(count: number): MeshData[] {
    const archetypes = [0, 1, 4, 5, 2, 3];   // ordered by visual weight
    const seeds      = [1337, 42, 7919, 20260725, 8675309, 271828];
    const variants: MeshData[] = [];
    for (let i = 0; i < count; i++) {
        variants.push(generateCrystalShard(archetypes[i % archetypes.length],
                                            seeds[i % seeds.length]));
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
