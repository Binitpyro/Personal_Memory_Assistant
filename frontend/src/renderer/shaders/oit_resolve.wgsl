// oit_resolve.wgsl
// Solves the Hamburger moment problem over a full-screen quad to composite transparency.

struct VertexOutput {
    @builtin(position) clip_position: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@vertex
fn vs_main(@builtin(vertex_index) in_vertex_index: u32) -> VertexOutput {
    var out: VertexOutput;
    // Full screen triangle
    let x = f32((in_vertex_index << 1u) & 2u);
    let y = f32(in_vertex_index & 2u);
    out.uv = vec2<f32>(x * 0.5, y * 0.5);
    out.clip_position = vec4<f32>(x - 1.0, 1.0 - y, 0.0, 1.0);
    return out;
}

@group(0) @binding(0) var momentTex: texture_2d<f32>;
@group(0) @binding(1) var colorTex: texture_2d<f32>;
@group(0) @binding(2) var textureSampler: sampler;

@fragment
fn fs_main(in: VertexOutput) -> @location(0) vec4<f32> {
    let intCoord = vec2<i32>(in.clip_position.xy);
    
    let moments = textureLoad(momentTex, intCoord, 0);
    let colorAcc = textureLoad(colorTex, intCoord, 0);
    
    // MBOIT Resolve math (Peters et al. 2017)
    // For a real implementation, you'd reconstruct the transmittance function 
    // from the 4 moments (b0, b1, b2, b3), finding polynomial roots.
    // For this boilerplate, we perform a simplified naive accumulation fallback.
    
    let totalAlpha = moments.x; // b0 is total optical depth/alpha implicitly
    if (totalAlpha < 0.001) {
        discard;
    }
    
    // Very naive resolve: average color / alpha, and exponentiate for background
    // Production MBOIT requires solving a 2x2 eigenvalue problem per pixel.
    let avgColor = colorAcc.rgb / max(colorAcc.a, 0.0001);
    let finalTransmittance = exp(-totalAlpha * 2.0); // Beer-Lambert approximation
    
    return vec4<f32>(avgColor, 1.0 - finalTransmittance);
}
