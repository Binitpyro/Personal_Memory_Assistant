// frontend/src/renderer/palette.ts

/**
 * Shared palette and threshold constants for the Cel-Shaded Dreamscape.
 * These are injected into both WebGPU and WebGL2 shader pipelines to ensure parity.
 */
export const Palette = {
    // Four-band cel shading thresholds
    CEL_BAND_1: 0.15,
    CEL_BAND_2: 0.45,
    CEL_BAND_3: 0.75,

    // Anti-aliasing derivative bounds
    AA_MIN: 0.05,
    AA_MAX: 0.15,

    // Base colors (Gooch style warm/cool)
    WARM_COLOR: [1.0, 0.95, 0.8],
    COOL_COLOR: [0.2, 0.3, 0.6],
    BASE_COLOR: [0.8, 0.8, 0.8],

    // Fog and depth properties
    FOG_COLOR: [0.05, 0.07, 0.15],
    FOG_DENSITY: 0.002,
    
    // Core emission for collapsed nodes
    DENSE_CORE_COLOR: [1.0, 0.7, 0.2],
};

/**
 * Generates the WGSL constants preamble.
 */
export function paletteWGSL(): string {
    return `
        const PAL_CEL_B1: f32 = ${Palette.CEL_BAND_1};
        const PAL_CEL_B2: f32 = ${Palette.CEL_BAND_2};
        const PAL_CEL_B3: f32 = ${Palette.CEL_BAND_3};
        const PAL_AA: f32 = ${Palette.AA_MIN};
        
        const PAL_WARM: vec3<f32> = vec3<f32>(${Palette.WARM_COLOR.join(', ')});
        const PAL_COOL: vec3<f32> = vec3<f32>(${Palette.COOL_COLOR.join(', ')});
        const PAL_BASE: vec3<f32> = vec3<f32>(${Palette.BASE_COLOR.join(', ')});
        const PAL_FOG: vec3<f32> = vec3<f32>(${Palette.FOG_COLOR.join(', ')});
        const PAL_CORE: vec3<f32> = vec3<f32>(${Palette.DENSE_CORE_COLOR.join(', ')});
    `;
}

/**
 * Generates the GLSL constants preamble.
 */
export function paletteGLSL(): string {
    return `
        const float PAL_CEL_B1 = ${Palette.CEL_BAND_1};
        const float PAL_CEL_B2 = ${Palette.CEL_BAND_2};
        const float PAL_CEL_B3 = ${Palette.CEL_BAND_3};
        const float PAL_AA = ${Palette.AA_MIN};
        
        const vec3 PAL_WARM = vec3(${Palette.WARM_COLOR.join(', ')});
        const vec3 PAL_COOL = vec3(${Palette.COOL_COLOR.join(', ')});
        const vec3 PAL_BASE = vec3(${Palette.BASE_COLOR.join(', ')});
        const vec3 PAL_FOG = vec3(${Palette.FOG_COLOR.join(', ')});
        const vec3 PAL_CORE = vec3(${Palette.DENSE_CORE_COLOR.join(', ')});
    `;
}
