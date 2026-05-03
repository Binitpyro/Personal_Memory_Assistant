struct CameraUniform {
    viewProj: mat4x4<f32>,
    eyePosition: vec3<f32>,
};

@group(0) @binding(0) var<uniform> camera: CameraUniform;

struct Node {
    position: vec3<f32>,
    radius: f32,
    parent_index: u32,
    flags: u32,
    type_hash: u32,
    pad: u32,
}

@group(0) @binding(1) var<storage, read> nodes: array<Node>;

struct DrawIndirectArgs {
    vertexCount: u32,
    instanceCount: atomic<u32>,
    firstVertex: u32,
    firstInstance: u32,
}

@group(0) @binding(2) var<storage, read_write> draw_args: DrawIndirectArgs;
@group(0) @binding(3) var<storage, read_write> visible_indices: array<u32>;

@compute @workgroup_size(64)
fn cs_main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let index = global_id.x;
    if index >= arrayLength(&nodes) {
        return;
    }

    let node = nodes[index];
    let pos = node.position;
    let radius = node.radius;

    // 1. View Frustum Culling
    var clip_pos = camera.viewProj * vec4<f32>(pos, 1.0);
    let in_front = clip_pos.w > 0.0;

    // approximate radius in clip space
    let clip_radius = radius * camera.viewProj[1][1] * 2.0;

    var visible = true;
    if in_front {
        let ndc_x = clip_pos.x / clip_pos.w;
        let ndc_y = clip_pos.y / clip_pos.w;
        let ndc_z = clip_pos.z / clip_pos.w;
        let rad_ndc = abs(clip_radius / clip_pos.w);

        if ndc_x < -1.0 - rad_ndc || ndc_x > 1.0 + rad_ndc ||
            ndc_y < -1.0 - rad_ndc || ndc_y > 1.0 + rad_ndc ||
            ndc_z < 0.0 - rad_ndc || ndc_z > 1.0 + rad_ndc {
            visible = false;
        }

        // 2. Screen-space size culling
        if rad_ndc < 0.002 && node.flags == 0u {
            visible = false; // cull very small files
        }
    } else {
        if clip_pos.w < -radius {
            visible = false;
        }
    }

    // 3. LOD Culling (Files only)
    if visible && node.flags == 0u && node.parent_index != 0xFFFFFFFFu {
        let parent = nodes[node.parent_index];
        let dist_to_camera = distance(camera.eyePosition, parent.position);
        let interaction_radius = parent.radius * 20.0;
        if dist_to_camera > interaction_radius {
            visible = false;
        }
    }

    if visible {
        let write_idx = atomicAdd(&draw_args.instanceCount, 1u);
        visible_indices[write_idx] = index;
    }
}
