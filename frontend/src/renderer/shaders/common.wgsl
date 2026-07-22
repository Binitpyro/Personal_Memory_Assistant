// common.wgsl
// Shared functions for NPR cel-shading

// 3-band toon quantization
fn toon_lighting(NdotL: f32) -> f32 {
    // Band thresholds with smoothstep transitions
    let shadow = smoothstep(0.0, 0.05, NdotL) * 0.35;
    let mid    = smoothstep(0.3, 0.35, NdotL) * 0.35;
    let bright = smoothstep(0.65, 0.70, NdotL) * 0.30;
    return shadow + mid + bright;
}

// Toon-quantized specular highlight
fn toon_specular(NdotH: f32, threshold: f32) -> f32 {
    return smoothstep(threshold - 0.02, threshold + 0.02, NdotH);
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
