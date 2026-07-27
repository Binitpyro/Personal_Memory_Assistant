/**
 * icosphere.ts — Overhauled with real per-LOD subdivision, seam-free normals,
 * and an on-the-side helper that produces an "iridescent bubble" high-detail
 * mesh (subdiv 4) plus a low-detail one (subdiv 2). Renderer picks the LOD
 * based on distance-to-camera at instance build time.
 *
 * Old file: fixed subdiv 3, 642 verts, hard-coded `generateIcosphereLOD =
 * generateIcosphere`. New file: `generateIcosphereLOD(level)` where level ∈
 * {0,1,2,3,4}, and `generateIcosphereMulti()` returns [near, mid, far] in
 * one shot so the renderer allocates all three GPU meshes at init.
 */

import type { MeshData } from './icosahedron';

const PHI = (1 + Math.sqrt(5)) / 2;

type V3 = [number, number, number];

const BASE_VERTS: V3[] = [
    [-1,  PHI, 0], [ 1,  PHI, 0], [-1, -PHI, 0], [ 1, -PHI, 0],
    [0, -1,  PHI], [0,  1,  PHI], [0, -1, -PHI], [0,  1, -PHI],
    [ PHI, 0, -1], [ PHI, 0,  1], [-PHI, 0, -1], [-PHI, 0,  1],
];

const BASE_FACES: [number, number, number][] = [
    [0,11,5],[0,5,1],[0,1,7],[0,7,10],[0,10,11],
    [1,5,9],[5,11,4],[11,10,2],[10,7,6],[7,1,8],
    [3,9,4],[3,4,2],[3,2,6],[3,6,8],[3,8,9],
    [4,9,5],[2,4,11],[6,2,10],[8,6,7],[9,8,1],
];

function normalize3(v: V3): V3 {
    const l = Math.hypot(v[0], v[1], v[2]) || 1;
    return [v[0]/l, v[1]/l, v[2]/l];
}
function midpoint(a: V3, b: V3): V3 {
    return normalize3([(a[0]+b[0])/2, (a[1]+b[1])/2, (a[2]+b[2])/2]);
}

function subdivide(verts: V3[], faces: [number, number, number][]) {
    const newFaces: [number, number, number][] = [];
    const midCache = new Map<string, number>();

    const getMid = (a: number, b: number): number => {
        const key = a < b ? `${a}_${b}` : `${b}_${a}`;
        const cached = midCache.get(key);
        if (cached !== undefined) return cached;
        const m = midpoint(verts[a], verts[b]);
        const idx = verts.length;
        verts.push(m);
        midCache.set(key, idx);
        return idx;
    };

    for (const [a, b, c] of faces) {
        const ab = getMid(a, b);
        const bc = getMid(b, c);
        const ca = getMid(c, a);
        newFaces.push([a, ab, ca], [ab, b, bc], [ca, bc, c], [ab, bc, ca]);
    }
    return { verts, faces: newFaces };
}

export function generateIcosphere(subdivisions = 3): MeshData {
    let verts: V3[] = BASE_VERTS.map(normalize3);
    let faces: [number, number, number][] = [...BASE_FACES];

    for (let i = 0; i < subdivisions; i++) {
        const r = subdivide(verts, faces);
        verts = r.verts;
        faces = r.faces;
    }

    // Smooth shading — vertex normal = position (unit sphere).
    // Uint16 tops out at 65,535 verts; subdiv 4 gives 2,562 verts so we're fine.
    const stride = 6;
    const vertArray = new Float32Array(verts.length * stride);
    for (let i = 0; i < verts.length; i++) {
        const v = verts[i];
        vertArray[i * stride + 0] = v[0];
        vertArray[i * stride + 1] = v[1];
        vertArray[i * stride + 2] = v[2];
        vertArray[i * stride + 3] = v[0];
        vertArray[i * stride + 4] = v[1];
        vertArray[i * stride + 5] = v[2];
    }
    const indexCount = faces.length * 3;
    const idxArray = new Uint16Array(indexCount);
    for (let fi = 0; fi < faces.length; fi++) {
        idxArray[fi*3+0] = faces[fi][0];
        idxArray[fi*3+1] = faces[fi][1];
        idxArray[fi*3+2] = faces[fi][2];
    }
    return { vertices: vertArray, indices: idxArray, vertexCount: verts.length, indexCount };
}

/**
 * LOD table (kept in sync with the renderer's `bubbleLods` array):
 *   0 → subdiv 1  (42 verts,  80 faces)   very far
 *   1 → subdiv 2  (162 verts, 320 faces)  medium
 *   2 → subdiv 3  (642 verts, 1280 faces) near — same as legacy default
 *   3 → subdiv 4  (2562 verts, 5120 faces) hero close-up (max Uint16 safe)
 */
export function generateIcosphereLOD(level = 2): MeshData {
    const clamped = Math.max(0, Math.min(4, level | 0));
    // Level maps 1:1 to subdivisions minus 1 for our chosen scale.
    const subdiv = [1, 2, 3, 4, 4][clamped];
    return generateIcosphere(subdiv);
}

/** Convenience: return [near, mid, far] MeshData for a 3-LOD pipeline. */
export function generateIcosphereMulti(): [MeshData, MeshData, MeshData] {
    return [
        generateIcosphereLOD(3), // near
        generateIcosphereLOD(2), // mid
        generateIcosphereLOD(1), // far
    ];
}
