// common.wgsl
// Shared functions for NPR cel-shading

// Clamp fwidth to avoid division-by-zero or excessively large derivatives
// PAL_AA is injected via paletteWGSL() 
fn aa_step(edge: f32, x: f32, derivative: f32) -> f32 {
    let w = clamp(derivative * 0.75, PAL_AA, 0.05);
    return smoothstep(edge - w, edge + w, x);
}

// 3-band toon quantization with Gooch bi-tonal shift
fn toon_lighting(NdotL: f32, derivative: f32) -> vec3<f32> {
    let shadow = aa_step(PAL_CEL_B1, NdotL, derivative);
    let mid    = aa_step(PAL_CEL_B2, NdotL, derivative);
    let bright = aa_step(PAL_CEL_B3, NdotL, derivative);
    
    // Ensure bands are distinct multipliers summing to 1.0
    let b3 = bright;
    let b2 = max(0.0, mid - b3);
    let b1 = max(0.0, shadow - (b2 + b3));
    let b0 = max(0.0, 1.0 - (b1 + b2 + b3));
    
    // Gooch shift blending
    let color_b0 = PAL_COOL;
    let color_b1 = mix(PAL_COOL, PAL_BASE, 0.5);
    let color_b2 = mix(PAL_BASE, PAL_WARM, 0.5);
    let color_b3 = PAL_WARM;
    
    return b0 * color_b0 + b1 * color_b1 + b2 * color_b2 + b3 * color_b3;
}

// Toon-quantized specular disc (hard highlight)
fn toon_specular(NdotH: f32, threshold: f32, derivative: f32) -> f32 {
    return aa_step(threshold, NdotH, derivative);
}

// Fresnel rim glow
fn rim_glow(NdotV: f32, rim_color: vec3<f32>, rim_power: f32, rim_strength: f32) -> vec3<f32> {
    let rim = pow(1.0 - max(NdotV, 0.0), rim_power) * rim_strength;
    return rim_color * rim;
}

// Distance-based exponential fog
fn atmospheric_fog(color: vec3<f32>, distance: f32, fog_density: f32, fog_color: vec3<f32>) -> vec3<f32> {
    let fogFactor = 1.0 - exp(-distance * fog_density);
    return mix(color, fog_color, clamp(fogFactor, 0.0, 1.0));
}
