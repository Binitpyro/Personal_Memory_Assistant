// outline.wgsl
// Fullscreen post-process for Sobel edge detection using depth + normal.

struct CameraUniform {
    viewProj: mat4x4<f32>,
    eyePosition: vec4<f32>,
    fogColor: vec4<f32>,
    currentVariant: u32,
    time: f32,
    screenWidth: f32,
    screenHeight: f32,
    fogDensity: f32,
    _pad1: f32,
    _pad2: f32,
    _pad3: f32,
};

@group(0) @binding(0) var<uniform> camera: CameraUniform;
@group(0) @binding(1) var sceneDepth: texture_depth_2d;
@group(0) @binding(2) var linearSampler: sampler;

struct VertexOutput {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@vertex
fn vs_main(@builtin(vertex_index) vertexIndex: u32) -> VertexOutput {
    // Generate a fullscreen triangle
    let uv = vec2<f32>(vec2<u32>(
        (vertexIndex << 1u) & 2u,
        vertexIndex & 2u
    ));
    var out: VertexOutput;
    out.position = vec4<f32>(uv * 2.0 - 1.0, 0.0, 1.0);
    // Vulkan/WebGPU Y is down
    out.uv = vec2<f32>(uv.x, 1.0 - uv.y);
    return out;
}

// Combined Sobel and Laplacian edge detection on depth
fn detect_edges(uv: vec2<f32>, texelSize: vec2<f32>) -> f32 {
    let d00 = textureSample(sceneDepth, linearSampler, uv + vec2<f32>(-1.0, -1.0) * texelSize).r;
    let d10 = textureSample(sceneDepth, linearSampler, uv + vec2<f32>( 0.0, -1.0) * texelSize).r;
    let d20 = textureSample(sceneDepth, linearSampler, uv + vec2<f32>( 1.0, -1.0) * texelSize).r;
    
    let d01 = textureSample(sceneDepth, linearSampler, uv + vec2<f32>(-1.0,  0.0) * texelSize).r;
    let d11 = textureSample(sceneDepth, linearSampler, uv).r;
    let d21 = textureSample(sceneDepth, linearSampler, uv + vec2<f32>( 1.0,  0.0) * texelSize).r;
    
    let d02 = textureSample(sceneDepth, linearSampler, uv + vec2<f32>(-1.0,  1.0) * texelSize).r;
    let d12 = textureSample(sceneDepth, linearSampler, uv + vec2<f32>( 0.0,  1.0) * texelSize).r;
    let d22 = textureSample(sceneDepth, linearSampler, uv + vec2<f32>( 1.0,  1.0) * texelSize).r;
    
    // Sobel (1st derivative) for strong silhouettes
    let gx = (d20 + 2.0 * d21 + d22) - (d00 + 2.0 * d01 + d02);
    let gy = (d02 + 2.0 * d12 + d22) - (d00 + 2.0 * d10 + d20);
    let sobel = sqrt(gx * gx + gy * gy);
    
    // Laplacian (2nd derivative) for internal facet creases
    let laplacian = abs((d00 + d10 + d20 + d01 + d21 + d02 + d12 + d22) - 8.0 * d11);
    
    // Thresholds tuned for non-linear depth. Laplacian threshold is lower.
    let silhouette = step(0.005, sobel);
    let crease = step(0.0005, laplacian);
    
    return clamp(silhouette + crease, 0.0, 1.0);
}

@fragment
fn fs_main(in: VertexOutput) -> @location(0) vec4<f32> {
    let dims = textureDimensions(sceneDepth);
    let texelSize = vec2<f32>(1.0 / f32(dims.x), 1.0 / f32(dims.y));
    
    let edge = detect_edges(in.uv, texelSize);
    
    // Return dark ink color. Alpha is 0 where there's no edge, so it blends perfectly.
    return vec4<f32>(0.02, 0.01, 0.05, edge * 0.85); 
}
