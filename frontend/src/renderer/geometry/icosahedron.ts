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
