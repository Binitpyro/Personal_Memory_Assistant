// outline.wgsl
// Fullscreen post-process for Sobel edge detection using depth + normal.

struct CameraUniform {
    viewProj: mat4x4<f32>,
    eyePosition: vec3<f32>,
    currentVariant: u32,
    time: f32,
    screenWidth: f32,
    screenHeight: f32,
    fogDensity: f32,
    fogColor: vec3<f32>,
    _pad2: f32,
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

// Sobel edge detection on depth
fn sobel_depth(uv: vec2<f32>, texelSize: vec2<f32>) -> f32 {
    let d00 = textureSample(sceneDepth, linearSampler, uv + vec2<f32>(-1.0, -1.0) * texelSize);
    let d10 = textureSample(sceneDepth, linearSampler, uv + vec2<f32>( 0.0, -1.0) * texelSize);
    let d20 = textureSample(sceneDepth, linearSampler, uv + vec2<f32>( 1.0, -1.0) * texelSize);
    
    let d01 = textureSample(sceneDepth, linearSampler, uv + vec2<f32>(-1.0,  0.0) * texelSize);
    let d21 = textureSample(sceneDepth, linearSampler, uv + vec2<f32>( 1.0,  0.0) * texelSize);
    
    let d02 = textureSample(sceneDepth, linearSampler, uv + vec2<f32>(-1.0,  1.0) * texelSize);
    let d12 = textureSample(sceneDepth, linearSampler, uv + vec2<f32>( 0.0,  1.0) * texelSize);
    let d22 = textureSample(sceneDepth, linearSampler, uv + vec2<f32>( 1.0,  1.0) * texelSize);
    
    let gx = (d20 + 2.0 * d21 + d22) - (d00 + 2.0 * d01 + d02);
    let gy = (d02 + 2.0 * d12 + d22) - (d00 + 2.0 * d10 + d20);
    
    return sqrt(gx * gx + gy * gy);
}

@fragment
fn fs_main(in: VertexOutput) -> @location(0) vec4<f32> {
    let dims = textureDimensions(sceneDepth);
    let texelSize = vec2<f32>(1.0 / f32(dims.x), 1.0 / f32(dims.y));
    
    let edgeDepth = sobel_depth(in.uv, texelSize);
    
    // Threshold edge value to get a hard outline
    let outline = step(0.005, edgeDepth); 
    
    // Return dark outline. We'll use alpha blending in the render pass.
    return vec4<f32>(0.02, 0.01, 0.05, outline * 0.85); 
}
