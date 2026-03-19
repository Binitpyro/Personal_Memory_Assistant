// frontend/src/renderer/shaders/oit_resolve.wgsl

struct FullscreenVertexOutput {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@vertex
fn vs_main(@builtin(vertex_index) vertex_index : u32) -> FullscreenVertexOutput {
    var out: FullscreenVertexOutput;
    // Giant Triangle math: creates a triangle that covers the whole screen
    let x = -1.0 + f32((vertex_index & 1u) << 2u);
    let y = -1.0 + f32((vertex_index & 2u) << 1u);
    out.position = vec4<f32>(x, y, 0.0, 1.0);
    out.uv = vec2<f32>(x * 0.5 + 0.5, 1.0 - (y * 0.5 + 0.5));
    return out;
}

@group(0) @binding(0) var momentTexture: texture_2d<f32>;
@group(0) @binding(1) var colorTexture: texture_2d<f32>;

@fragment
fn fs_main(in: FullscreenVertexOutput) -> @location(0) vec4<f32> {
    let coords = vec2<i32>(in.position.xy);
    let moments = textureLoad(momentTexture, coords, 0);
    let accum = textureLoad(colorTexture, coords, 0);

    if (moments.x <= 0.0001) {
        // Return the clear color of the background
        return vec4<f32>(0.945, 0.96, 0.878, 1.0);
    }

    // Standard MBOIT resolve logic
    let avgColor = accum.rgb / max(accum.a, 0.0001);
    let transmittance = moments.x; // Basic OIT for now
    
    return vec4<f32>(avgColor * transmittance + vec3<f32>(0.945, 0.96, 0.878) * (1.0 - transmittance), 1.0);
}