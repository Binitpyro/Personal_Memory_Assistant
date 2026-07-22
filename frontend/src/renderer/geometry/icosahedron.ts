/**
 * icosahedron.ts
 * Generates a flat-shaded icosahedron subdivided once (80 triangles).
 * Each triangle has unique vertices so normals are per-face (visible facets).
 * Used for the crystal mesh (folders).
 */

export interface MeshData {
    /** Interleaved [x,y,z, nx,ny,nz] per vertex */
    vertices: Float32Array;
    indices: Uint16Array;
    vertexCount: number;
    indexCount: number;
}

const PHI = (1 + Math.sqrt(5)) / 2;

/** Base 12 icosahedron vertices on a unit sphere */
const BASE_VERTS: [number, number, number][] = [
    [-1,  PHI, 0], [ 1,  PHI, 0], [-1, -PHI, 0], [ 1, -PHI, 0],
    [0, -1,  PHI], [0,  1,  PHI], [0, -1, -PHI], [0,  1, -PHI],
    [ PHI, 0, -1], [ PHI, 0,  1], [-PHI, 0, -1], [-PHI, 0,  1],
];

/** 20 base faces */
const BASE_FACES: [number, number, number][] = [
    [0,11,5],[0,5,1],[0,1,7],[0,7,10],[0,10,11],
    [1,5,9],[5,11,4],[11,10,2],[10,7,6],[7,1,8],
    [3,9,4],[3,4,2],[3,2,6],[3,6,8],[3,8,9],
    [4,9,5],[2,4,11],[6,2,10],[8,6,7],[9,8,1],
];

function normalize3(v: [number, number, number]): [number, number, number] {
    const len = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    return [v[0] / len, v[1] / len, v[2] / len];
}

function midpoint(a: [number, number, number], b: [number, number, number]): [number, number, number] {
    return normalize3([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2]);
}

/** Subdivide each triangle into 4 (1 pass → 80 faces from 20) */
function subdivide(
    verts: [number, number, number][],
    faces: [number, number, number][],
): { verts: [number, number, number][]; faces: [number, number, number][] } {
    const newFaces: [number, number, number][] = [];
    const midCache = new Map<string, number>();

    function getMid(a: number, b: number): number {
        const key = a < b ? `${a}_${b}` : `${b}_${a}`;
        if (midCache.has(key)) return midCache.get(key)!;
        const mid = midpoint(verts[a], verts[b]);
        const idx = verts.length;
        verts.push(mid);
        midCache.set(key, idx);
        return idx;
    }

    for (const [a, b, c] of faces) {
        const ab = getMid(a, b);
        const bc = getMid(b, c);
        const ca = getMid(c, a);
        newFaces.push([a, ab, ca], [ab, b, bc], [ca, bc, c], [ab, bc, ca]);
    }
    return { verts, faces: newFaces };
}

/**
 * Generate a flat-shaded subdivided icosahedron.
 * Flat shading requires unshared vertices per triangle.
 * Returns interleaved [x,y,z,nx,ny,nz] Float32Array + index array.
 */
export function generateIcosahedron(subdivisions = 1): MeshData {
    let verts: [number, number, number][] = BASE_VERTS.map(normalize3);
    let faces: [number, number, number][] = [...BASE_FACES];

    for (let i = 0; i < subdivisions; i++) {
        const result = subdivide(verts, faces);
        verts = result.verts;
        faces = result.faces;
    }

    // Flat shading: 3 unique vertices per triangle, normal = face normal
    const floatsPerVert = 6; // xyz + normal xyz
    const vertArray = new Float32Array(faces.length * 3 * floatsPerVert);
    const idxArray = new Uint16Array(faces.length * 3);

    let vi = 0;
    for (let fi = 0; fi < faces.length; fi++) {
        const [ia, ib, ic] = faces[fi];
        const a = verts[ia], b = verts[ib], c = verts[ic];

        // Face normal (cross product of edges)
        const e1: [number, number, number] = [b[0]-a[0], b[1]-a[1], b[2]-a[2]];
        const e2: [number, number, number] = [c[0]-a[0], c[1]-a[1], c[2]-a[2]];
        const n = normalize3([
            e1[1]*e2[2] - e1[2]*e2[1],
            e1[2]*e2[0] - e1[0]*e2[2],
            e1[0]*e2[1] - e1[1]*e2[0],
        ]);

        for (const v of [a, b, c]) {
            vertArray[vi * floatsPerVert + 0] = v[0];
            vertArray[vi * floatsPerVert + 1] = v[1];
            vertArray[vi * floatsPerVert + 2] = v[2];
            vertArray[vi * floatsPerVert + 3] = n[0];
            vertArray[vi * floatsPerVert + 4] = n[1];
            vertArray[vi * floatsPerVert + 5] = n[2];
            idxArray[vi] = vi;
            vi++;
        }
    }

    return { vertices: vertArray, indices: idxArray, vertexCount: vi, indexCount: vi };
}

/** Deterministic PRNG (mulberry32) — same seed = same shard every load. */
function mulberry32(seed: number) {
    return function () {
        seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
        let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}

/**
 * Jagged, rock-like crystal shard: subdivided icosahedron with per-vertex
 * radial noise and flat plateaus ("fractured geode" look) created by random clipping planes.
 * Re-normalized so the furthest vertex sits at radius 1.0 — keeps instance
 * scaling and the picking pass consistent with a unit bounding sphere.
 */
export function generateCrystalShard(subdivisions = 1, seed = 1337): MeshData {
    let verts: [number, number, number][] = BASE_VERTS.map(normalize3);
    let faces: [number, number, number][] = [...BASE_FACES];

    for (let i = 0; i < subdivisions; i++) {
        const result = subdivide(verts, faces);
        verts = result.verts;
        faces = result.faces;
    }

    const rand = mulberry32(seed);
    
    // Generate 4-7 random clipping planes for the "fractured geode" look
    const numPlanes = 4 + Math.floor(rand() * 4);
    const planes: { n: [number, number, number], dist: number }[] = [];
    
    // Always add top and bottom clipping planes to flatten poles
    planes.push({ n: [0, 1, 0], dist: 0.8 + rand() * 0.1 });
    planes.push({ n: [0, -1, 0], dist: 0.8 + rand() * 0.1 });
    
    for (let i = 0; i < numPlanes; i++) {
        let nx = rand() - 0.5;
        let ny = rand() - 0.5;
        let nz = rand() - 0.5;
        const len = Math.hypot(nx, ny, nz);
        planes.push({ 
            n: [nx / len, ny / len, nz / len], 
            dist: 0.65 + rand() * 0.25 // Cut distance from center
        });
    }

    let maxLen = 0;
    verts = verts.map((v) => {
        let r = 0.8 + rand() * 0.4; // rough rocky surface
        let p: [number, number, number] = [
            v[0] * r * 0.9,   // pinch X/Z
            v[1] * r * 1.35,  // elongate Y → shard profile
            v[2] * r * 0.9,
        ];
        
        // Apply clipping planes (flattening)
        for (const plane of planes) {
            const dot = p[0]*plane.n[0] + p[1]*plane.n[1] + p[2]*plane.n[2];
            if (dot > plane.dist) {
                // Project onto the plane
                const excess = dot - plane.dist;
                p[0] -= plane.n[0] * excess;
                p[1] -= plane.n[1] * excess;
                p[2] -= plane.n[2] * excess;
            }
        }

        maxLen = Math.max(maxLen, Math.hypot(p[0], p[1], p[2]));
        return p;
    });
    
    const inv = 1 / maxLen;
    verts = verts.map(v => [v[0] * inv, v[1] * inv, v[2] * inv] as [number, number, number]);

    // Flat shading: identical to generateIcosahedron's second half.
    const floatsPerVert = 6;
    const vertArray = new Float32Array(faces.length * 3 * floatsPerVert);
    const idxArray = new Uint16Array(faces.length * 3);
    let vi = 0;
    for (const [ia, ib, ic] of faces) {
        const a = verts[ia], b = verts[ib], c = verts[ic];
        const e1: [number, number, number] = [b[0]-a[0], b[1]-a[1], b[2]-a[2]];
        const e2: [number, number, number] = [c[0]-a[0], c[1]-a[1], c[2]-a[2]];
        const n = normalize3([
            e1[1]*e2[2] - e1[2]*e2[1],
            e1[2]*e2[0] - e1[0]*e2[2],
            e1[0]*e2[1] - e1[1]*e2[0],
        ]);
        for (const v of [a, b, c]) {
            vertArray.set([v[0], v[1], v[2], n[0], n[1], n[2]], vi * floatsPerVert);
            idxArray[vi] = vi;
            vi++;
        }
    }
    return { vertices: vertArray, indices: idxArray, vertexCount: vi, indexCount: vi };
}

/**
 * Generates multiple distinct crystal mesh variants.
 */
export function generateCrystalVariants(count: number): MeshData[] {
    const variants: MeshData[] = [];
    for (let i = 0; i < count; i++) {
        // Use subdivisions = 2 to give enough geometry for the jagged shards
        // Use a consistent seed per variant
        variants.push(generateCrystalShard(2, 1337 + i * 42));
    }
    return variants;
}
