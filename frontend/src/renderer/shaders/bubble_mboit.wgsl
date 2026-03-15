// bubble_mboit.wgsl
// Render file elements as iridescent bubbles, folders as sharp crystals

struct CameraUniform {
    viewProj: mat4x4<f32>,
    planes: array<vec4<f32>, 6>,
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
    @location(3) type_hash: u32,
    @location(4) is_folder: f32,
};

@vertex
fn vs_main(in: VertexInput) -> VertexOutput {
    var out: VertexOutput;
    
    let is_folder = step(in.instanceSize, 0.0); // 1.0 if folder (negative size), 0.0 if file
    let actual_size = abs(in.instanceSize);
    
    let worldPos = in.position * actual_size + in.instancePos;
    out.world_position = worldPos;
    out.clip_position = camera.viewProj * vec4<f32>(worldPos, 1.0);
    out.normal = in.normal;
    out.view_depth = out.clip_position.w; 
    out.type_hash = in.typeHash;
    out.is_folder = is_folder;
    
    return out;
}

struct MBOITOutput {
    @location(0) moments: vec4<f32>,  // b0, b1, b2, b3
    @location(1) color: vec4<f32>,    // accumulated premultiplied color
};

// Simple pseudo-random for iridescence
fn rando(h: u32) -> f32 {
    return f32(h % 100u) / 100.0;
}

@fragment
fn fs_main(in: VertexOutput) -> MBOITOutput {
    var out: MBOITOutput;
    
    let viewDir = normalize(camera.eyePosition - in.world_position);
    let N = normalize(in.normal);
    let dt = dot(viewDir, N);
    
    var finalColor: vec3<f32>;
    var alpha: f32;

    if (in.is_folder > 0.5) {
        // Crystal / Folder rendering (solid, distinct color)
        let baseColor = vec3<f32>(0.2, 0.6, 0.9) + rando(in.type_hash) * vec3<f32>(0.4, 0.2, 0.1);
        let highlight = pow(clamp(dt, 0.0, 1.0), 5.0);
        finalColor = baseColor + vec3<f32>(highlight);
        alpha = 0.9;
    } else {
        // Bubble / File rendering (iridescent, mostly transparent)
        let thickness = 400.0 + rando(in.type_hash) * 400.0; // nm
        let phase = dt * thickness * 0.01;
        
        let iridescence = vec3<f32>(
            0.5 + 0.5 * sin(phase),
            0.5 + 0.5 * sin(phase + 2.094),
            0.5 + 0.5 * sin(phase + 4.188)
        );
        
        let rim = pow(1.0 - clamp(dt, 0.0, 1.0), 3.0);
        finalColor = iridescence * rim;
        alpha = rim * 0.8 + 0.1; // mostly transparent except at edges
    }
    
    let depth = in.view_depth;
    
    let d2 = depth * depth;
    let d3 = d2 * depth;
    let momentWeights = vec4<f32>(1.0, depth, d2, d3);
    
    out.moments = momentWeights * alpha;
    out.color = vec4<f32>(finalColor * alpha, alpha);
    
    return out;
}
