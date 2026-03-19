/**
 * MortonEncoding.ts
 * CPU baseline for 3D Morton encoding (Z-order curve)
 * Useful for debugging or CPU-fallback spatial sorting.
 * High-performance encoding for 4M points is offloaded to WebGPU Compute shaders in LinearBVH.ts.
 */

// Expands a 10-bit integer into 30 bits by inserting 2 zeros after each bit.
function expandBits(v: number): number {
    v = (v * 0x00010001) & 0xFF0000FF;
    v = (v * 0x00000101) & 0x0F00F00F;
    v = (v * 0x00000011) & 0xC30C30C3;
    v = (v * 0x00000005) & 0x49249249;
    return v;
}

/**
 * Calculates a 30-bit Morton code for a 3D point.
 * Coordinates must be normalized in the range [0.0, 1.0].
 */
export function encodeMorton3D(x: number, y: number, z: number): number {
    x = Math.max(0, Math.min(1, x));
    y = Math.max(0, Math.min(1, y));
    z = Math.max(0, Math.min(1, z));

    // Quantize to 10 bits: [0, 1023]
    const xx = expandBits(Math.floor(x * 1023));
    const yy = expandBits(Math.floor(y * 1023));
    const zz = expandBits(Math.floor(z * 1023));

    return (xx * 4) + (yy * 2) + zz;
}
