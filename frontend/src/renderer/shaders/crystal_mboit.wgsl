// crystal_mboit.wgsl
// Renders the folder elements as refractive crystals computing 4 depth moments

struct CameraUniform {
    viewProj: mat4x4<f32>,
    eyePosition: vec3<f32>,
};

@group(0) @binding(0) var<uniform> camera: CameraUniform;

struct VertexInput {
    @location(0) position: vec3<f32>,
    @location(1) normal: vec3<f32>,
    @location(2) instancePos: vec3<f32>,
    @location(3) instanceSize: f32,
    @location(4) typeHash: u32,
};

struct VertexOutput {
    @builtin(position) clip_position: vec4<f32>,
    @location(0) view_depth: f32,
    @location(1) world_position: vec3<f32>,
    @location(2) normal: vec3<f32>,
    @location(3) base_color: vec4<f32>,
};

@vertex
fn vs_main(in: VertexInput) -> VertexOutput {
    var out: VertexOutput;
    
    // Scale and translate by instance data
    let worldPos = in.position * in.instanceSize + in.instancePos;
    out.world_position = worldPos;
    out.clip_position = camera.viewProj * vec4<f32>(worldPos, 1.0);
    out.normal = in.normal;
    
    // Depth for moments calculation (linearized)
    out.view_depth = out.clip_position.w; 
    
    // Generate color procedurally based on hash (just a placeholder)
    let r = f32(in.typeHash & 0xFFu) / 255.0;
    let g = f32((in.typeHash >> 8u) & 0xFFu) / 255.0;
    let b = f32((in.typeHash >> 16u) & 0xFFu) / 255.0;
    out.base_color = vec4<f32>(r, g, b, 0.4); // Semi-transparent crystal
    
    return out;
}

// Represents the targets matching WebGPURenderer outputs
struct MBOITOutput {
    @location(0) moments: vec4<f32>,  // b0, b1, b2, b3
    @location(1) color: vec4<f32>,    // accumulated premultiplied color
};

@fragment
fn fs_main(in: VertexOutput) -> MBOITOutput {
    var out: MBOITOutput;
    
    // Simplistic Snell's Law simulated lighting / refraction
    let viewDir = normalize(camera.eyePosition - in.world_position);
    let N = normalize(in.normal);
    
    let dt = dot(viewDir, N);
    let fresnel = 0.04 + 0.96 * pow(1.0 - clamp(dt, 0.0, 1.0), 5.0);
    
    let finalColor = in.base_color * (1.0 - fresnel) + vec4<f32>(1.0, 1.0, 1.0, 0.0) * fresnel;
    
    let alpha = finalColor.a;
    let depth = in.view_depth;
    
    // Moment generation - standard 4-moment formula
    // b0 = 1, b1 = d, b2 = d^2, b3 = d^3 (weighted by alpha)
    let d2 = depth * depth;
    let d3 = d2 * depth;
    let momentWeights = vec4<f32>(1.0, depth, d2, d3);
    
    out.moments = momentWeights * alpha;
    out.color = vec4<f32>(finalColor.rgb * alpha, alpha); // Premultiplied
    
    return out;
}
