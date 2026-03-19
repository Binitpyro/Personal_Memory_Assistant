/**
 * MortonEncoding.ts
 * CPU baseline for 3D Morton encoding (Z-order curve)
 * Useful for debugging or CPU-fallback spatial sorting.
 * High-performance encoding for 4M points is offloaded to WebGPU Compute shaders in LinearBVH.ts.
 */

/**
 * Expand a 10-bit integer into a 30-bit pattern by inserting two zero bits after each input bit.
 *
 * @param v - A 10-bit integer in the range 0–1023 to expand
 * @returns A 30-bit integer where each bit of `v` is placed with two zero bits between successive bits, suitable for 3D Morton interleaving
 */
function expandBits(v: number): number {
    v = (v * 0x00010001) & 0xFF0000FF;
    v = (v * 0x00000101) & 0x0F00F00F;
    v = (v * 0x00000011) & 0xC30C30C3;
    v = (v * 0x00000005) & 0x49249249;
    return v;
}

/**
 * Encode a 3D point into a 30-bit Morton (Z-order) code.
 *
 * @param x - X coordinate in [0, 1]; values are clamped to this range before encoding
 * @param y - Y coordinate in [0, 1]; values are clamped to this range before encoding
 * @param z - Z coordinate in [0, 1]; values are clamped to this range before encoding
 * @returns A 30-bit Morton code produced by quantizing each coordinate to 10 bits and interleaving their bits
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
