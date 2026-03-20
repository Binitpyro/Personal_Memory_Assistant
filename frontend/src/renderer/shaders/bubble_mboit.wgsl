struct CameraUniform {
    viewProj: mat4x4<f32>,
    eyePosition: vec3<f32>,
};

@group(0) @binding(0) var<uniform> camera: CameraUniform;

struct VertexInput {
    @builtin(instance_index) instance_idx: u32,
    @location(0) quad_pos: vec2<f32>,
    @location(1) instance_position: vec3<f32>,
    @location(2) instance_radius: f32,
    @location(3) instance_flags: u32,
    @location(4) instance_type_hash: u32,
};

struct VertexOutput {
    @builtin(position) clip_position: vec4<f32>,
    @location(0) view_depth: f32,
    @location(1) local_uv: vec2<f32>,
    @location(2) @interpolate(flat) type_hash: u32,
    @location(3) is_folder: f32,
    @location(4) sphere_radius: f32,
    @location(5) billboardWorldPos: vec3<f32>,
    @location(6) forward: vec3<f32>,
};

@vertex
fn vs_main(in: VertexInput) -> VertexOutput {
    var out: VertexOutput;

    let is_folder = f32(in.instance_flags);
    let actual_size = in.instance_radius;
    let instancePos = in.instance_position;

    // BILLBOARDING: Calculate vectors so the quad always faces the camera
    var viewDir = camera.eyePosition - instancePos;
    let dist = length(viewDir);
    
    // NaN Safeguard: If camera is exactly inside the object, force a default forward vector
    if dist < 0.001 {
        viewDir = vec3<f32>(0.0, 0.0, 1.0);
    } else {
        viewDir = viewDir / dist;
    }
    
    let forward = viewDir;
    let world_up = select(
        vec3<f32>(0.0, 1.0, 0.0),
        vec3<f32>(1.0, 0.0, 0.0),
        abs(forward.y) > 0.99
    );
    let right = normalize(cross(world_up, forward));
    let up = cross(forward, right);

    // Expand the flat quad into 3D world space
    let localOffset = (right * in.quad_pos.x + up * in.quad_pos.y) * actual_size;
    let worldPos = instancePos + localOffset;

    out.clip_position = camera.viewProj * vec4<f32>(worldPos, 1.0);
    out.view_depth = out.clip_position.w;
    out.local_uv = in.quad_pos; // Ranges from -1 to 1
    out.type_hash = in.instance_type_hash;
    out.is_folder = is_folder;
    out.sphere_radius = actual_size;
    out.billboardWorldPos = worldPos;
    out.forward = forward;

    return out;
}

struct MBOITOutput {
    @builtin(frag_depth) depth: f32,
    @location(0) moments: vec4<f32>,
    @location(1) color: vec4<f32>,
};

@fragment
fn fs_main(in: VertexOutput) -> MBOITOutput {
    var out: MBOITOutput;

    // RAYCAST: Carve the quad into a perfect circle
    let distSq = dot(in.local_uv, in.local_uv);
    if distSq > 1.0 { discard; }

    // 3D FORGING: Calculate the procedural Z-depth of the surface
    let z = sqrt(1.0 - distSq);
    var localNormal = vec3<f32>(in.local_uv.x, in.local_uv.y, z);

    let trueWorldPos = in.billboardWorldPos + in.forward * (z * in.sphere_radius);
    let trueClipPos = camera.viewProj * vec4<f32>(trueWorldPos, 1.0);
    out.depth = trueClipPos.z / trueClipPos.w;

    var finalColor: vec3<f32>;
    var alpha: f32;

    if in.is_folder > 0.5 {
        // --- THE CRYSTAL (Folders) ---
        // Replaced high-frequency static noise with low-frequency stable distortion
        let hash_f = f32(in.type_hash % 100u);
        let distortion = vec3<f32>(
            sin(localNormal.y * 3.0 + hash_f),
            cos(localNormal.x * 3.0 + hash_f),
            sin(localNormal.z * 3.0 + hash_f)
        ) * 0.4;

        let perturbedNormal = normalize(localNormal + distortion);
        
        // Snap to sharp facets
        let facets = 4.0;
        let facetedNormal = normalize(round(perturbedNormal * facets) / facets);

        // Glassy specular highlight
        let dt = dot(vec3<f32>(0.0, 0.0, 1.0), facetedNormal);
        let baseColor = vec3<f32>(0.2, 0.6, 0.9) + vec3<f32>(f32(in.type_hash % 10u) / 20.0, 0.05, 0.1);
        finalColor = baseColor + pow(max(dt, 0.0), 16.0);
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

    // WBOIT: Weighted Blended Order Independent Transparency
    let depth_val = max(0.1, in.view_depth - (z * in.sphere_radius));
    
    // McGuire 2013 WBOIT weight function, clamped to a lower max (100) to prevent Float16 overflow
    let weight = clamp(pow(alpha, 1.5) * max(1e-2, 3e3 / (1e-5 + pow(abs(depth_val) * 0.05, 3.0))), 1e-2, 100.0);

    out.moments = vec4<f32>(alpha, 0.0, 0.0, 0.0);
    out.color = vec4<f32>(finalColor * alpha * weight, alpha * weight);

    return out;
}