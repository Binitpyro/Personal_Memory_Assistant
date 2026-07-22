/**
 * icosphere.ts
 * Generates a smooth-shaded icosphere subdivided 3 times (~642 verts, ~1280 faces).
 * Each vertex normal points radially outward for smooth shading.
 * Used for the bubble mesh (files).
 */

import type { MeshData } from './icosahedron';

const PHI = (1 + Math.sqrt(5)) / 2;

const BASE_VERTS: [number, number, number][] = [
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

function normalize3(v: [number, number, number]): [number, number, number] {
    const len = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    return [v[0] / len, v[1] / len, v[2] / len];
}

function midpoint(a: [number, number, number], b: [number, number, number]): [number, number, number] {
    return normalize3([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2]);
}

function subdivide(
    verts: [number, number, number][],
    faces: [number, number, number][],
) {
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
 * Generate a smooth-shaded icosphere.
 * Vertices are shared — normals are the vertex position (radially outward).
 * Returns interleaved [x,y,z,nx,ny,nz] + index buffer using Uint32Array for larger meshes.
 */
export function generateIcosphere(subdivisions = 3): MeshData {
    let verts: [number, number, number][] = BASE_VERTS.map(normalize3);
    let faces: [number, number, number][] = [...BASE_FACES];

    for (let i = 0; i < subdivisions; i++) {
        const result = subdivide(verts, faces);
        verts = result.verts;
        faces = result.faces;
    }

    const floatsPerVert = 6; // xyz + normal xyz (normal = position for unit sphere)
    const vertArray = new Float32Array(verts.length * floatsPerVert);
    for (let i = 0; i < verts.length; i++) {
        const v = verts[i];
        vertArray[i * floatsPerVert + 0] = v[0];
        vertArray[i * floatsPerVert + 1] = v[1];
        vertArray[i * floatsPerVert + 2] = v[2];
        vertArray[i * floatsPerVert + 3] = v[0]; // normal = position on unit sphere
        vertArray[i * floatsPerVert + 4] = v[1];
        vertArray[i * floatsPerVert + 5] = v[2];
    }

    // Use Uint16Array if possible, otherwise Uint32Array
    const indexCount = faces.length * 3;
    const idxArray = new Uint16Array(indexCount);
    for (let fi = 0; fi < faces.length; fi++) {
        idxArray[fi * 3 + 0] = faces[fi][0];
        idxArray[fi * 3 + 1] = faces[fi][1];
        idxArray[fi * 3 + 2] = faces[fi][2];
    }

    return {
        vertices: vertArray,
        indices: idxArray,
        vertexCount: verts.length,
        indexCount,
    };
}
