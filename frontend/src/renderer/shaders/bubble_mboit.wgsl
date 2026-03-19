struct CameraUniform {
    viewProj: mat4x4<f32>,
    // This padding bridges the gap from byte 64 to byte 160 to match your TS code
    padding: array<vec4<f32>, 6>, 
    eyePosition: vec3<f32>,
};

@group(0) @binding(0) var<uniform> camera: CameraUniform;

struct VertexInput {
    @location(0) quad_pos: vec2<f32>,      
    @location(1) instancePos: vec3<f32>,   
    @location(2) instanceSize: f32,
    @location(3) typeHash: u32,
};

struct VertexOutput {
    @builtin(position) clip_position: vec4<f32>,
    @location(0) view_depth: f32,
    @location(1) local_uv: vec2<f32>,      
    @location(2) @interpolate(flat) type_hash: u32,
    @location(3) is_folder: f32,
    @location(4) sphere_radius: f32,
};

@vertex
fn vs_main(in: VertexInput) -> VertexOutput {
    var out: VertexOutput;
    
    // Determine if it's a folder (negative size) or file (positive size)
    let is_folder = step(in.instanceSize, 0.0); 
    let actual_size = abs(in.instanceSize);
    
    // BILLBOARDING: Calculate vectors so the quad always faces the camera
    let forward = normalize(camera.eyePosition - in.instancePos);
    let right = normalize(cross(vec3<f32>(0.0, 1.0, 0.0), forward));
    let up = cross(forward, right);
    
    // Expand the flat quad into 3D world space
    let localOffset = (right * in.quad_pos.x + up * in.quad_pos.y) * actual_size;
    let worldPos = in.instancePos + localOffset;
    
    out.clip_position = camera.viewProj * vec4<f32>(worldPos, 1.0);
    out.view_depth = out.clip_position.w; 
    out.local_uv = in.quad_pos; // Ranges from -1 to 1
    out.type_hash = in.typeHash;
    out.is_folder = is_folder;
    out.sphere_radius = actual_size;
    
    return out;
}

struct MBOITOutput {
    @location(0) moments: vec4<f32>,
    @location(1) color: vec4<f32>,
};

// Deterministic noise for unique crystal cuts
fn hash31(p: vec3<f32>, seed: u32) -> f32 {
    let p3 = fract(p * 0.1031 + f32(seed) * 0.01);
    let h = dot(p3, p3.yzx + 33.33);
    return fract((h + p3.x) * p3.y);
}

@fragment
fn fs_main(in: VertexOutput) -> MBOITOutput {
    var out: MBOITOutput;
    
    // RAYCAST: Carve the quad into a perfect circle
    let distSq = dot(in.local_uv, in.local_uv);
    if (distSq > 1.0) { discard; }
    
    // 3D FORGING: Calculate the procedural Z-depth of the surface
    let z = sqrt(1.0 - distSq);
    var localNormal = vec3<f32>(in.local_uv.x, in.local_uv.y, z);
    
    var finalColor: vec3<f32>;
    var alpha: f32;

    if (in.is_folder > 0.5) {
        // --- THE CRYSTAL (Folders) ---
        // Every folder gets a unique faceted cut based on its hash
        let hash_seed = in.type_hash;
        let noise = vec3<f32>(
            hash31(localNormal, hash_seed) - 0.5,
            hash31(localNormal, hash_seed + 1u) - 0.5,
            hash31(localNormal, hash_seed + 2u) - 0.5
        );
        
        let perturbedNormal = normalize(localNormal + noise * 0.8);
        let facets = 3.0 + f32(hash_seed % 4u); 
        let facetedNormal = normalize(round(perturbedNormal * facets) / facets);
        
        // Glassy specular highlight
        let dt = dot(vec3<f32>(0.0, 0.0, 1.0), facetedNormal);
        let baseColor = vec3<f32>(0.2, 0.6, 0.9) + vec3<f32>(f32(hash_seed % 10u)/20.0, 0.05, 0.1);
        finalColor = baseColor + pow(max(dt, 0.0), 12.0);
        alpha = 0.85; 
        
    } else {
        // --- THE BUBBLE (Files) ---
        // Organic wobble + Iridescence
        let wobble = sin(localNormal.x * 5.0 + f32(in.type_hash)) * 0.15;
        let organicNormal = normalize(localNormal + vec3<f32>(wobble, wobble, 0.0));
        let dt = max(dot(vec3<f32>(0.0, 0.0, 1.0), organicNormal), 0.0);
        
        // Thin-film interference simulation
        let phase = dt * (400.0 + f32(in.type_hash % 400u)) * 0.01;
        let iridescence = 0.5 + 0.5 * cos(vec3<f32>(phase, phase + 2.09, phase + 4.18));
        
        let rim = pow(1.0 - dt, 3.0); 
        finalColor = iridescence * rim;
        alpha = rim * 0.9 + 0.05; 
    }
    
    // MBOIT: Moment-Based Order Independent Transparency
    // Push depth based on the sphere's curvature for a 3D feel
    let depth = in.view_depth - (z * in.sphere_radius); 
    let d2 = depth * depth;
    let d3 = d2 * depth;
    
    out.moments = vec4<f32>(1.0, depth, d2, d3) * alpha;
    out.color = vec4<f32>(finalColor * alpha, alpha);
    
    return out;
}