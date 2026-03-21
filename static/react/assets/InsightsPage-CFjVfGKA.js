import{c as b,j as e,o as E,u as F,q as O,C as T,t as L,n as R}from"./index-B6KZ1_pF.js";import{a as d}from"./echarts-Dso2f9nO.js";import{F as U,L as D}from"./FileTypeTreemap-BLfhyGrq.js";import{L as k}from"./trash-2-Ciwq1n19.js";import{H as V}from"./hard-drive-B8D7mBXy.js";const q=[["path",{d:"M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z",key:"hh9hay"}],["path",{d:"m3.3 7 8.7 5 8.7-5",key:"g66t2b"}],["path",{d:"M12 22V12",key:"d0xqtd"}]],z=b("box",q);const I=[["path",{d:"M21 12c.552 0 1.005-.449.95-.998a10 10 0 0 0-8.953-8.951c-.55-.055-.998.398-.998.95v8a1 1 0 0 0 1 1z",key:"pzmjnu"}],["path",{d:"M21.21 15.89A10 10 0 1 1 8 2.83",key:"k2fpak"}]],S=b("chart-pie",I);const W=[["path",{d:"M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z",key:"1oefj6"}],["path",{d:"M14 2v5a1 1 0 0 0 1 1h5",key:"wfsgrz"}],["path",{d:"M11 18h2",key:"12mj7e"}],["path",{d:"M12 12v6",key:"3ahymv"}],["path",{d:"M9 13v-.5a.5.5 0 0 1 .5-.5h5a.5.5 0 0 1 .5.5v.5",key:"qbrxap"}]],G=b("file-type",W);const X=[["path",{d:"M12 3q1 4 4 6.5t3 5.5a1 1 0 0 1-14 0 5 5 0 0 1 1-3 1 1 0 0 0 5 0c0-2-1.5-3-1.5-5q0-2 2.5-4",key:"1slcih"}]],Y=b("flame",X);const $=[["path",{d:"m10 20-1.25-2.5L6 18",key:"18frcb"}],["path",{d:"M10 4 8.75 6.5 6 6",key:"7mghy3"}],["path",{d:"m14 20 1.25-2.5L18 18",key:"1chtki"}],["path",{d:"m14 4 1.25 2.5L18 6",key:"1b4wsy"}],["path",{d:"m17 21-3-6h-4",key:"15hhxa"}],["path",{d:"m17 3-3 6 1.5 3",key:"11697g"}],["path",{d:"M2 12h6.5L10 9",key:"kv9z4n"}],["path",{d:"m20 10-1.5 2 1.5 2",key:"1swlpi"}],["path",{d:"M22 12h-6.5L14 15",key:"1mxi28"}],["path",{d:"m4 10 1.5 2L4 14",key:"k9enpj"}],["path",{d:"m7 21 3-6-1.5-3",key:"j8hb9u"}],["path",{d:"m7 3 3 6h4",key:"1otusx"}]],H=b("snowflake",$);const Z=[["path",{d:"M16 7h6v6",key:"box55l"}],["path",{d:"m22 7-8.5 8.5-5-5L2 17",key:"1t1m79"}]],K=b("trending-up",Z),J=`struct CameraUniform {\r
    viewProj: mat4x4<f32>,\r
    eyePosition: vec3<f32>,\r
};\r
\r
@group(0) @binding(0) var<uniform> camera: CameraUniform;\r
\r
struct VertexInput {\r
    @builtin(instance_index) instance_idx: u32,\r
    @location(0) quad_pos: vec2<f32>,\r
    @location(1) instance_position: vec3<f32>,\r
    @location(2) instance_radius: f32,\r
    @location(3) instance_flags: u32,\r
    @location(4) instance_type_hash: u32,\r
};\r
\r
struct VertexOutput {\r
    @builtin(position) clip_position: vec4<f32>,\r
    @location(0) view_depth: f32,\r
    @location(1) local_uv: vec2<f32>,\r
    @location(2) @interpolate(flat) type_hash: u32,\r
    @location(3) is_folder: f32,\r
    @location(4) sphere_radius: f32,\r
    @location(5) billboardWorldPos: vec3<f32>,\r
    @location(6) forward: vec3<f32>,\r
};\r
\r
@vertex\r
fn vs_main(in: VertexInput) -> VertexOutput {\r
    var out: VertexOutput;\r
\r
    let is_folder = f32(in.instance_flags);\r
    let actual_size = in.instance_radius;\r
    let instancePos = in.instance_position;\r
\r
    // BILLBOARDING: Calculate vectors so the quad always faces the camera\r
    var viewDir = camera.eyePosition - instancePos;\r
    let dist = length(viewDir);\r
    \r
    // NaN Safeguard: If camera is exactly inside the object, force a default forward vector\r
    if dist < 0.001 {\r
        viewDir = vec3<f32>(0.0, 0.0, 1.0);\r
    } else {\r
        viewDir = viewDir / dist;\r
    }\r
    \r
    let forward = viewDir;\r
    let world_up = select(\r
        vec3<f32>(0.0, 1.0, 0.0),\r
        vec3<f32>(1.0, 0.0, 0.0),\r
        abs(forward.y) > 0.99\r
    );\r
    let right = normalize(cross(world_up, forward));\r
    let up = cross(forward, right);\r
\r
    // Expand the flat quad into 3D world space\r
    let localOffset = (right * in.quad_pos.x + up * in.quad_pos.y) * actual_size;\r
    let worldPos = instancePos + localOffset;\r
\r
    out.clip_position = camera.viewProj * vec4<f32>(worldPos, 1.0);\r
    out.view_depth = out.clip_position.w;\r
    out.local_uv = in.quad_pos; // Ranges from -1 to 1\r
    out.type_hash = in.instance_type_hash;\r
    out.is_folder = is_folder;\r
    out.sphere_radius = actual_size;\r
    out.billboardWorldPos = worldPos;\r
    out.forward = forward;\r
\r
    return out;\r
}\r
\r
struct MBOITOutput {\r
    @builtin(frag_depth) depth: f32,\r
    @location(0) moments: vec4<f32>,\r
    @location(1) color: vec4<f32>,\r
};\r
\r
@fragment\r
fn fs_main(in: VertexOutput) -> MBOITOutput {\r
    var out: MBOITOutput;\r
\r
    // RAYCAST: Carve the quad into a perfect circle\r
    let distSq = dot(in.local_uv, in.local_uv);\r
    if distSq > 1.0 { discard; }\r
\r
    // 3D FORGING: Calculate the procedural Z-depth of the surface\r
    let z = sqrt(1.0 - distSq);\r
    var localNormal = vec3<f32>(in.local_uv.x, in.local_uv.y, z);\r
\r
    let trueWorldPos = in.billboardWorldPos + in.forward * (z * in.sphere_radius);\r
    let trueClipPos = camera.viewProj * vec4<f32>(trueWorldPos, 1.0);\r
    out.depth = trueClipPos.z / trueClipPos.w;\r
\r
    var finalColor: vec3<f32>;\r
    var alpha: f32;\r
\r
    if in.is_folder > 0.5 {\r
        // --- THE CRYSTAL (Folders) ---\r
        let hash_f = f32(in.type_hash % 100u);\r
        let distortion = vec3<f32>(\r
            sin(localNormal.y * 3.0 + hash_f),\r
            cos(localNormal.x * 3.0 + hash_f),\r
            sin(localNormal.z * 3.0 + hash_f)\r
        ) * 0.4;\r
\r
        let perturbedNormal = normalize(localNormal + distortion);\r
        let facets = 4.0;\r
        let facetedNormal = normalize(round(perturbedNormal * facets) / facets);\r
\r
        let dt = dot(vec3<f32>(0.0, 0.0, 1.0), facetedNormal);\r
        let baseColor = vec3<f32>(0.2, 0.6, 0.9) + vec3<f32>(f32(in.type_hash % 10u) / 20.0, 0.05, 0.1);\r
        finalColor = baseColor + pow(max(dt, 0.0), 16.0);\r
        \r
        alpha = 0.15; \r
    } else {\r
        // --- THE BUBBLE (Files) ---\r
        // Organic wobble + Iridescence\r
        let wobble = sin(localNormal.x * 5.0 + f32(in.type_hash)) * 0.15;\r
        let organicNormal = normalize(localNormal + vec3<f32>(wobble, wobble, 0.0));\r
        let dt = max(dot(vec3<f32>(0.0, 0.0, 1.0), organicNormal), 0.0);\r
\r
        // Thin-film interference simulation\r
        let phase = dt * (400.0 + f32(in.type_hash % 400u)) * 0.01;\r
        let iridescence = 0.5 + 0.5 * cos(vec3<f32>(phase, phase + 2.09, phase + 4.18));\r
\r
        let rim = pow(1.0 - dt, 3.0);\r
        finalColor = iridescence * rim;\r
        alpha = rim * 0.9 + 0.05;\r
    }\r
\r
    // WBOIT: Weighted Blended Order Independent Transparency\r
    let depth_val = max(0.1, in.view_depth - (z * in.sphere_radius));\r
    \r
    // WBOIT weight calculation...\r
    let weight = clamp(pow(alpha, 1.5) * max(1e-2, 3e3 / (1e-5 + pow(abs(depth_val) * 0.05, 3.0))), 1e-2, 100.0);\r
\r
    // FIX: Write the procedural spherical depth to the GPU depth buffer\r
    out.depth = trueClipPos.z / trueClipPos.w; \r
\r
    out.moments = vec4<f32>(alpha, 0.0, 0.0, 0.0);\r
    out.color = vec4<f32>(finalColor * alpha * weight, alpha * weight);\r
\r
    return out;\r
}`,Q=`// frontend/src/renderer/shaders/oit_resolve.wgsl\r
\r
struct FullscreenVertexOutput {\r
    @builtin(position) position: vec4<f32>,\r
};\r
\r
@vertex\r
fn vs_main(@builtin(vertex_index) vertex_index: u32) -> FullscreenVertexOutput {\r
    var out: FullscreenVertexOutput;\r
    // Giant Triangle math: creates a triangle that covers the whole screen\r
    let x = -1.0 + f32((vertex_index & 1u) << 2u);\r
    let y = -1.0 + f32((vertex_index & 2u) << 1u);\r
    out.position = vec4<f32>(x, y, 0.0, 1.0);\r
    return out;\r
}\r
\r
@group(0) @binding(0) var momentTexture: texture_2d<f32>;\r
@group(0) @binding(1) var colorTexture: texture_2d<f32>;\r
\r
@fragment\r
fn fs_main(in: FullscreenVertexOutput) -> @location(0) vec4<f32> {\r
    let coords = vec2<i32>(in.position.xy);\r
    let moments = textureLoad(momentTexture, coords, 0);\r
    let accum = textureLoad(colorTexture, coords, 0);\r
\r
    if moments.x <= 0.0001 {\r
        return vec4<f32>(0.945, 0.96, 0.878, 1.0);\r
    }\r
\r
    let totalAlpha = moments.x;\r
    let visibility = 1.0 - exp(-totalAlpha); \r
    \r
    let avgColor = accum.rgb / max(accum.a, 0.0001);\r
    let backgroundColor = vec3<f32>(0.945, 0.96, 0.878);\r
\r
    return vec4<f32>(avgColor * visibility + backgroundColor * (1.0 - visibility), 1.0);\r
}`,ee=`struct CameraUniform {\r
    viewProj: mat4x4<f32>,\r
    eyePosition: vec3<f32>,\r
};\r
\r
@group(0) @binding(0) var<uniform> camera: CameraUniform;\r
\r
struct VertexInput {\r
    @builtin(instance_index) instance_idx: u32,\r
    @location(0) quad_pos: vec2<f32>,\r
    @location(1) instance_position: vec3<f32>,\r
    @location(2) instance_radius: f32,\r
    @location(3) instance_flags: u32,\r
    @location(4) instance_type_hash: u32,\r
};\r
\r
struct VertexOutput {\r
    @builtin(position) clip_position: vec4<f32>,\r
    @location(0) local_uv: vec2<f32>,\r
    @location(1) @interpolate(flat) type_hash: u32,\r
    @location(2) billboardWorldPos: vec3<f32>,\r
    @location(3) forward: vec3<f32>,\r
    @location(4) sphere_radius: f32,\r
};\r
\r
@vertex\r
fn vs_main(in: VertexInput) -> VertexOutput {\r
    var out: VertexOutput;\r
\r
    let actual_size = in.instance_radius;\r
    let instancePos = in.instance_position;\r
\r
    var viewDir = camera.eyePosition - instancePos;\r
    let dist = length(viewDir);\r
    if dist < 0.001 {\r
        viewDir = vec3<f32>(0.0, 0.0, 1.0);\r
    } else {\r
        viewDir = viewDir / dist;\r
    }\r
    let forward = viewDir;\r
    \r
    let world_up = select(\r
        vec3<f32>(0.0, 1.0, 0.0),\r
        vec3<f32>(1.0, 0.0, 0.0),\r
        abs(forward.y) > 0.99\r
    );\r
    let right = normalize(cross(world_up, forward));\r
    let up = cross(forward, right);\r
\r
    let localOffset = (right * in.quad_pos.x + up * in.quad_pos.y) * actual_size;\r
    let worldPos = instancePos + localOffset;\r
\r
    out.clip_position = camera.viewProj * vec4<f32>(worldPos, 1.0);\r
    out.local_uv = in.quad_pos;\r
    out.type_hash = in.instance_type_hash;\r
    out.billboardWorldPos = worldPos;\r
    out.forward = forward;\r
    out.sphere_radius = actual_size;\r
\r
    return out;\r
}\r
\r
struct FragmentOutput {\r
    @location(0) hash: u32,\r
    @builtin(frag_depth) depth: f32,\r
};\r
\r
@fragment\r
fn fs_main(in: VertexOutput) -> FragmentOutput {\r
    let distSq = dot(in.local_uv, in.local_uv);\r
    if distSq > 1.0 { discard; }\r
\r
    let z = sqrt(1.0 - distSq);\r
    let trueWorldPos = in.billboardWorldPos + in.forward * (z * in.sphere_radius);\r
    let trueClipPos = camera.viewProj * vec4<f32>(trueWorldPos, 1.0);\r
\r
    var out: FragmentOutput;\r
    out.hash = in.type_hash;\r
    out.depth = trueClipPos.z / trueClipPos.w;\r
\r
    return out;\r
}\r
`;class te{canvas;device;context;format;momentTexture;colorTexture;depthTexture;pickingTexture;cameraBuffer;geometryBuffer;pickBuffer;nodeBuffer;bubblePipeline;resolvePipeline;pickingPipeline;renderBindGroup;resolveBindGroup;pickingBindGroup;nodeCount=0;rotationX=.5;rotationY=.5;zoom=550;focusPosition=null;cameraPosition=[0,0,0];isFirstFrame=!0;constructor(t){this.canvas=t}async init(){if(!navigator.gpu)throw new Error("WebGPU not supported on this browser.");const t=await navigator.gpu.requestAdapter();if(!t)throw new Error("No appropriate GPUAdapter found.");this.device=await t.requestDevice({requiredLimits:{maxStorageBufferBindingSize:t.limits.maxStorageBufferBindingSize,maxComputeWorkgroupStorageSize:t.limits.maxComputeWorkgroupStorageSize,maxBufferSize:t.limits.maxBufferSize}}),this.context=this.canvas.getContext("webgpu"),this.format=navigator.gpu.getPreferredCanvasFormat(),this.context.configure({device:this.device,format:this.format,alphaMode:"premultiplied"}),this.pickBuffer=this.device.createBuffer({size:256,usage:GPUBufferUsage.COPY_DST|GPUBufferUsage.MAP_READ}),await this.createGeometryBuffer(),this.canvas.width=Math.max(1,this.canvas.clientWidth),this.canvas.height=Math.max(1,this.canvas.clientHeight),await this.setupPipelines(),this.setupTextures()}async createGeometryBuffer(){const t=new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]);this.geometryBuffer=this.device.createBuffer({size:t.byteLength,usage:GPUBufferUsage.VERTEX,mappedAtCreation:!0}),new Float32Array(this.geometryBuffer.getMappedRange()).set(t),this.geometryBuffer.unmap()}setupTextures(){const t={width:this.canvas.width,height:this.canvas.height};this.momentTexture=this.device.createTexture({size:t,format:"rgba16float",usage:GPUTextureUsage.RENDER_ATTACHMENT|GPUTextureUsage.TEXTURE_BINDING}),this.colorTexture=this.device.createTexture({size:t,format:"rgba16float",usage:GPUTextureUsage.RENDER_ATTACHMENT|GPUTextureUsage.TEXTURE_BINDING}),this.depthTexture=this.device.createTexture({size:t,format:"depth32float",usage:GPUTextureUsage.RENDER_ATTACHMENT}),this.pickingTexture=this.device.createTexture({size:t,format:"r32uint",usage:GPUTextureUsage.RENDER_ATTACHMENT|GPUTextureUsage.COPY_SRC}),this.resolveBindGroup=this.device.createBindGroup({layout:this.resolvePipeline.getBindGroupLayout(0),entries:[{binding:0,resource:this.momentTexture.createView()},{binding:1,resource:this.colorTexture.createView()}]})}resize(t,r){const s=Math.max(1,t),n=Math.max(1,r);this.canvas.width===s&&this.canvas.height===n||(this.canvas.width=s,this.canvas.height=n,this.momentTexture&&this.momentTexture.destroy(),this.colorTexture&&this.colorTexture.destroy(),this.depthTexture&&this.depthTexture.destroy(),this.pickingTexture&&this.pickingTexture.destroy(),this.setupTextures())}async setupPipelines(){const t=this.device.createShaderModule({code:J});this.bubblePipeline=this.device.createRenderPipeline({layout:"auto",vertex:{module:t,entryPoint:"vs_main",buffers:[{arrayStride:8,attributes:[{shaderLocation:0,offset:0,format:"float32x2"}]},{arrayStride:32,stepMode:"instance",attributes:[{shaderLocation:1,offset:0,format:"float32x3"},{shaderLocation:2,offset:12,format:"float32"},{shaderLocation:3,offset:20,format:"uint32"},{shaderLocation:4,offset:24,format:"uint32"}]}]},fragment:{module:t,entryPoint:"fs_main",targets:[{format:"rgba16float",blend:{color:{srcFactor:"one",dstFactor:"one",operation:"add"},alpha:{srcFactor:"one",dstFactor:"one",operation:"add"}}},{format:"rgba16float",blend:{color:{srcFactor:"one",dstFactor:"one",operation:"add"},alpha:{srcFactor:"one",dstFactor:"one",operation:"add"}}}]},primitive:{topology:"triangle-list"},depthStencil:{depthWriteEnabled:!1,depthCompare:"less",format:"depth32float"}});const r=this.device.createShaderModule({code:ee});this.pickingPipeline=this.device.createRenderPipeline({layout:"auto",vertex:{module:r,entryPoint:"vs_main",buffers:[{arrayStride:8,attributes:[{shaderLocation:0,offset:0,format:"float32x2"}]},{arrayStride:32,stepMode:"instance",attributes:[{shaderLocation:1,offset:0,format:"float32x3"},{shaderLocation:2,offset:12,format:"float32"},{shaderLocation:3,offset:20,format:"uint32"},{shaderLocation:4,offset:24,format:"uint32"}]}]},fragment:{module:r,entryPoint:"fs_main",targets:[{format:"r32uint"}]},primitive:{topology:"triangle-list"},depthStencil:{depthWriteEnabled:!0,depthCompare:"less-equal",format:"depth32float"}});const s=this.device.createShaderModule({code:Q});this.resolvePipeline=this.device.createRenderPipeline({layout:"auto",vertex:{module:s,entryPoint:"vs_main"},fragment:{module:s,entryPoint:"fs_main",targets:[{format:this.format,blend:{color:{srcFactor:"src-alpha",dstFactor:"one-minus-src-alpha",operation:"add"},alpha:{srcFactor:"one",dstFactor:"one-minus-src-alpha",operation:"add"}}}]}})}async loadData(t){this.nodeCount=Math.floor(t.byteLength/32),this.nodeCount!==0&&(this.nodeBuffer&&this.nodeBuffer.destroy(),this.nodeBuffer=this.device.createBuffer({size:Math.max(32,t.byteLength),usage:GPUBufferUsage.VERTEX|GPUBufferUsage.COPY_DST}),t.byteLength>0&&this.device.queue.writeBuffer(this.nodeBuffer,0,t),this.cameraBuffer||(this.cameraBuffer=this.device.createBuffer({size:80,usage:GPUBufferUsage.UNIFORM|GPUBufferUsage.COPY_DST})),this.updateCamera(),this.renderBindGroup=this.device.createBindGroup({layout:this.bubblePipeline.getBindGroupLayout(0),entries:[{binding:0,resource:{buffer:this.cameraBuffer}}]}),this.pickingBindGroup=this.device.createBindGroup({layout:this.pickingPipeline.getBindGroupLayout(0),entries:[{binding:0,resource:{buffer:this.cameraBuffer}}]}))}async pick(t,r){if(this.nodeCount===0||!this.cameraBuffer||!this.pickingTexture)return null;const s=Math.max(0,Math.min(Math.floor(t),this.canvas.width-1)),n=Math.max(0,Math.min(Math.floor(r),this.canvas.height-1));this.updateCamera();const i=this.device.createCommandEncoder(),l=i.beginRenderPass({colorAttachments:[{view:this.pickingTexture.createView(),loadOp:"clear",clearValue:{r:4294967295,g:0,b:0,a:0},storeOp:"store"}],depthStencilAttachment:{view:this.depthTexture.createView(),depthClearValue:1,depthLoadOp:"clear",depthStoreOp:"store"}});l.setPipeline(this.pickingPipeline),l.setScissorRect(s,n,1,1),this.pickingBindGroup&&l.setBindGroup(0,this.pickingBindGroup),this.geometryBuffer&&l.setVertexBuffer(0,this.geometryBuffer),this.nodeBuffer&&l.setVertexBuffer(1,this.nodeBuffer),l.draw(6,this.nodeCount,0,0),l.end(),i.copyTextureToBuffer({texture:this.pickingTexture,origin:[s,n,0]},{buffer:this.pickBuffer,bytesPerRow:256},[1,1,1]),this.device.queue.submit([i.finish()]),await this.pickBuffer.mapAsync(GPUMapMode.READ);const a=this.pickBuffer.getMappedRange(),p=new Uint32Array(a)[0],v=p===4294967295?null:p;return this.pickBuffer.unmap(),v}handleMouseMove(t,r){this.rotationY-=t*.005,this.rotationX+=r*.005,this.rotationX=Math.max(-Math.PI/2+.1,Math.min(Math.PI/2-.1,this.rotationX))}handleZoom(t){let r=Math.max(10,this.zoom*.05);this.zoom=Math.max(5,this.zoom+(t>0?r:-r))}updateCamera(){const t=this.canvas.width/this.canvas.height,r=this.perspective(45*Math.PI/180,t,.1,1e5);let s=this.focusPosition||[0,0,0];const n=s[0]+this.zoom*Math.cos(this.rotationX)*Math.sin(this.rotationY),i=s[1]+this.zoom*Math.sin(this.rotationX),l=s[2]+this.zoom*Math.cos(this.rotationX)*Math.cos(this.rotationY);this.isFirstFrame?(this.cameraPosition=[n,i,l],this.isFirstFrame=!1):(this.cameraPosition[0]+=(n-this.cameraPosition[0])*.1,this.cameraPosition[1]+=(i-this.cameraPosition[1])*.1,this.cameraPosition[2]+=(l-this.cameraPosition[2])*.1);const a=this.lookAt(this.cameraPosition,s,[0,1,0]),m=this.multiply(r,a);if(this.cameraBuffer){const p=new Float32Array(20);p.set(m,0),p.set([this.cameraPosition[0],this.cameraPosition[1],this.cameraPosition[2],0],16),this.device.queue.writeBuffer(this.cameraBuffer,0,p)}}perspective(t,r,s,n){const i=1/Math.tan(t/2),l=new Float32Array(16);return l[0]=i/r,l[5]=i,l[10]=n/(s-n),l[11]=-1,l[14]=s*n/(s-n),l}lookAt(t,r,s){const n=this.normalize(this.subtract(t,r)),i=this.normalize(this.cross(s,n)),l=this.cross(n,i),a=new Float32Array(16);return a[0]=i[0],a[4]=i[1],a[8]=i[2],a[12]=-this.dot(i,t),a[1]=l[0],a[5]=l[1],a[9]=l[2],a[13]=-this.dot(l,t),a[2]=n[0],a[6]=n[1],a[10]=n[2],a[14]=-this.dot(n,t),a[3]=0,a[7]=0,a[11]=0,a[15]=1,a}multiply(t,r){const s=new Float32Array(16);for(let n=0;n<4;n++)for(let i=0;i<4;i++)s[n*4+i]=t[0+i]*r[n*4+0]+t[4+i]*r[n*4+1]+t[8+i]*r[n*4+2]+t[12+i]*r[n*4+3];return s}subtract(t,r){return[t[0]-r[0],t[1]-r[1],t[2]-r[2]]}normalize(t){const r=Math.hypot(t[0],t[1],t[2]);return r===0?[0,0,1]:[t[0]/r,t[1]/r,t[2]/r]}cross(t,r){return[t[1]*r[2]-t[2]*r[1],t[2]*r[0]-t[0]*r[2],t[0]*r[1]-t[1]*r[0]]}dot(t,r){return t[0]*r[0]+t[1]*r[1]+t[2]*r[2]}render(){if(this.nodeCount===0||!this.cameraBuffer){const n=this.device.createCommandEncoder();n.beginRenderPass({colorAttachments:[{view:this.context.getCurrentTexture().createView(),loadOp:"clear",clearValue:[.945,.96,.878,1],storeOp:"store"}]}).end(),this.device.queue.submit([n.finish()]);return}this.updateCamera();const t=this.device.createCommandEncoder(),r=t.beginRenderPass({colorAttachments:[{view:this.momentTexture.createView(),loadOp:"clear",clearValue:[0,0,0,0],storeOp:"store"},{view:this.colorTexture.createView(),loadOp:"clear",clearValue:[0,0,0,0],storeOp:"store"}],depthStencilAttachment:{view:this.depthTexture.createView(),depthClearValue:1,depthLoadOp:"clear",depthStoreOp:"store"}});r.setPipeline(this.bubblePipeline),this.renderBindGroup&&r.setBindGroup(0,this.renderBindGroup),this.geometryBuffer&&r.setVertexBuffer(0,this.geometryBuffer),this.nodeBuffer&&r.setVertexBuffer(1,this.nodeBuffer),r.draw(6,this.nodeCount,0,0),r.end();const s=t.beginRenderPass({colorAttachments:[{view:this.context.getCurrentTexture().createView(),loadOp:"clear",clearValue:[.945,.96,.878,1],storeOp:"store"}]});s.setPipeline(this.resolvePipeline),this.resolveBindGroup&&s.setBindGroup(0,this.resolveBindGroup),s.draw(3),s.end(),this.device.queue.submit([t.finish()])}destroy(){this.momentTexture&&this.momentTexture.destroy(),this.colorTexture&&this.colorTexture.destroy(),this.depthTexture&&this.depthTexture.destroy(),this.pickingTexture&&this.pickingTexture.destroy(),this.cameraBuffer&&this.cameraBuffer.destroy(),this.geometryBuffer&&this.geometryBuffer.destroy(),this.pickBuffer&&this.pickBuffer.destroy(),this.nodeBuffer&&this.nodeBuffer.destroy(),this.device&&this.device.destroy(),this.context&&this.context.unconfigure()}}const re=({allFiles:o,activeFilter:t,onError:r})=>{const s=d.useRef(null),n=d.useRef(null),i=d.useRef(0),[l,a]=d.useState(!1),m=d.useRef({x:0,y:0}),p=d.useRef({x:0,y:0}),v=d.useCallback(async c=>{const u=new te(c);n.current=u;try{await u.init();const f=await E(t);if(f.byteLength>4){const g=new Uint8Array(f,0,2);if(g[0]===60&&g[1]===33)throw new Error("Backend sent HTML. Vite trap detected.");await u.loadData(f)}else throw new Error("No 3D data available or filter returned 0 results.");const x=()=>{u.render(),i.current=requestAnimationFrame(x)};return i.current=requestAnimationFrame(x),u}catch(f){return r(f instanceof Error?f.message:"Unknown error loading 3D data"),null}},[t,r]);d.useEffect(()=>{if(!s.current)return;const c=s.current;let u=null;return v(c).then(f=>{f&&(u=new ResizeObserver(x=>{for(const g of x){const{width:N,height:B}=g.contentRect;N>0&&B>0&&f.resize(N,B)}}),u.observe(c))}),()=>{i.current&&cancelAnimationFrame(i.current),u&&u.disconnect(),n.current&&n.current.destroy()}},[o,t,v]);const _=c=>{a(!0),m.current={x:c.clientX,y:c.clientY},p.current={x:c.clientX,y:c.clientY}},P=c=>{!l||!n.current||(n.current.handleMouseMove(c.clientX-m.current.x,c.clientY-m.current.y),m.current={x:c.clientX,y:c.clientY})},w=async c=>{a(!1);const u=Math.abs(c.clientX-p.current.x),f=Math.abs(c.clientY-p.current.y);if(u<5&&f<5&&n.current&&s.current){const x=s.current.getBoundingClientRect(),g=await n.current.pick(c.clientX-x.left,c.clientY-x.top);g!==null&&console.log("Picked 3D Node Hash:",g)}};return d.useEffect(()=>{const c=s.current;if(!c)return;const u=f=>{f.preventDefault(),f.stopPropagation(),n.current?.handleZoom(f.deltaY)};return c.addEventListener("wheel",u,{passive:!1}),()=>c.removeEventListener("wheel",u)},[]),e.jsxs("div",{className:"w-full h-full min-h-[400px] relative bg-[#f1f5e0] rounded-3xl overflow-hidden border border-white/40 shadow-inner",children:[e.jsxs("div",{className:"absolute top-6 left-8 z-10 pointer-events-none",children:[e.jsxs("h2",{className:"text-2xl font-bold text-primary flex items-center gap-3",children:[e.jsx("span",{className:"w-3 h-3 bg-accent rounded-full animate-pulse shadow-[0_0_12px_rgba(142,72,234,0.6)]"})," ","Crystal Dreamscape 3D"]}),e.jsx("p",{className:"text-text-secondary text-[10px] font-bold mt-2 tracking-widest uppercase opacity-60",children:"DreamScape 3D"})]}),e.jsx("canvas",{ref:s,className:"w-full h-full cursor-grab active:cursor-grabbing block touch-none",style:{minHeight:"400px",height:"100%",width:"100%",touchAction:"none"},onMouseDown:_,onMouseMove:P,onMouseUp:w,onMouseLeave:w})]})},ne=({allFiles:o,activeFilter:t,onFilterChange:r,initialMode:s})=>{const[n,i]=d.useState("checking"),[l,a]=d.useState(null);return d.useEffect(()=>{(async()=>{if(!navigator.gpu){a("Browser doesn't support WebGPU."),i("unsupported");return}try{await navigator.gpu.requestAdapter()?i("supported"):(a("No appropriate GPU Adapter found."),i("unsupported"))}catch(p){console.error("GPU Check Error:",p),a("WebGPU initialization failed."),i("unsupported")}})()},[]),n==="checking"?e.jsx("div",{className:"w-full h-[600px] bg-slate-900 flex items-center justify-center rounded-lg border border-slate-800",children:e.jsxs("div",{className:"flex flex-col items-center",children:[e.jsx("div",{className:"w-12 h-12 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin"}),e.jsx("p",{className:"mt-4 text-slate-400 font-mono text-sm",children:"Initializing GPU Infrastructure..."})]})}):n==="unsupported"?e.jsxs("div",{className:"w-full h-full flex flex-col",children:[e.jsx("div",{className:"bg-amber-900/30 border-l-4 border-amber-500 p-4 mb-4",children:e.jsxs("p",{className:"text-amber-200 text-sm",children:[e.jsx("span",{className:"font-bold",children:"2D Hardware-Accelerated View:"})," ",l||"WebGPU Not Available"]})}),e.jsx("div",{className:"flex-1 min-h-[400px]",children:e.jsx(U,{allFiles:o,activeFilter:t,onFilterChange:r,initialMode:s})})]}):e.jsx(re,{allFiles:o,activeFilter:t,onError:m=>{a(`3D Stream Error: ${m}`),i("unsupported")}})};function j(o){return o<1024?`${o} B`:o<1024*1024?`${(o/1024).toFixed(1)} KB`:o<1024*1024*1024?`${(o/(1024*1024)).toFixed(1)} MB`:`${(o/(1024*1024*1024)).toFixed(2)} GB`}function ce(){const{data:o,loading:t,error:r}=F(L,{cacheKey:"insights"}),{data:s,loading:n}=F(R,{cacheKey:"file-tree"}),[i,l]=d.useState(null),[a,m]=d.useState([]),[p,v]=d.useState([]),[_,P]=d.useState(!1),[w,c]=d.useState(null),[u,f]=d.useState("3d"),x=d.useCallback(h=>{l(h)},[]);d.useEffect(()=>{if(!i){m(o?.top_files??[]),v(o?.cold_files??[]),c(null);return}let h=!1;return P(!0),c(null),O(i).then(y=>{h||(m(y.top_files??[]),v(y.cold_files??[]))}).catch(y=>{h||(m([]),v([]),c(y instanceof Error?y.message:String(y)))}).finally(()=>{h||P(!1)}),()=>{h=!0}},[i,o]);const g=d.useMemo(()=>o?.type_breakdown?Object.keys(o.type_breakdown).length:0,[o]),N=o?j(o.total_size_bytes):"—",B=o?j(o.database_size_bytes):"—",C=o?.file_count??0;return e.jsxs("div",{className:"flex-1 overflow-y-auto p-6 space-y-6 animate-fade-in-up custom-scrollbar",children:[e.jsx("div",{className:"flex items-center justify-between",children:e.jsxs("div",{children:[e.jsxs("h1",{className:"text-2xl font-bold flex items-center gap-3",children:[e.jsx(T,{className:"w-7 h-7 text-primary"}),"Insights"]}),e.jsx("p",{className:"text-text-secondary mt-1 text-sm",children:"Analytics and visualizations of your personal data"})]})}),r&&e.jsx("div",{className:"glass-card bg-error/10 text-error text-sm",children:r}),t&&!o&&e.jsx("div",{className:"glass-card flex items-center justify-center py-16",children:e.jsx(k,{className:"w-8 h-8 text-primary animate-spin"})}),o&&e.jsxs(e.Fragment,{children:[e.jsx("div",{className:"grid grid-cols-1 md:grid-cols-5 gap-4",children:[{label:"Total Files",value:C.toLocaleString(),icon:G,color:"text-primary-light"},{label:"Indexed Files Size",value:N,icon:S,color:"text-accent"},{label:"Database Size",value:B,icon:V,color:"text-primary"},{label:"File Types",value:g.toString(),icon:K,color:"text-success"},{label:"Top Used",value:(o?.top_files?.length??0).toString(),icon:T,color:"text-warning"}].map(({label:h,value:y,icon:A,color:M})=>e.jsxs("div",{className:"glass-card flex flex-col items-center justify-center py-6 px-4",children:[e.jsx(A,{className:`w-6 h-6 ${M} mb-2`}),e.jsx("span",{className:`text-xl font-bold ${M} text-center`,children:y}),e.jsx("span",{className:"text-text-secondary text-xs mt-1 text-center uppercase tracking-wider font-semibold",children:h})]},h))}),e.jsxs("div",{className:"grid grid-cols-1 lg:grid-cols-3 gap-4 flex-1",children:[e.jsxs("div",{className:"glass-card lg:col-span-2 flex flex-col min-h-[400px] h-full overflow-hidden",children:[e.jsxs("div",{className:"flex items-center justify-between mb-4 shrink-0",children:[e.jsxs("h2",{className:"text-lg font-bold text-primary flex items-center gap-2",children:[e.jsx(S,{className:"w-5 h-5"}),"File Type Hierarchy"]}),e.jsxs("div",{className:"flex items-center bg-black/5 p-1 rounded-xl border border-black/5 shadow-inner",children:[e.jsxs("button",{onClick:()=>f("3d"),className:`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold transition-all ${u==="3d"?"bg-primary text-white shadow-lg":"text-text-secondary hover:text-text-primary"}`,children:[e.jsx(z,{className:"w-3.5 h-3.5"})," 3D CRYSTAL"]}),e.jsxs("button",{onClick:()=>f("2d"),className:`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold transition-all ${u==="2d"?"bg-primary text-white shadow-lg":"text-text-secondary hover:text-text-primary"}`,children:[e.jsx(D,{className:"w-3.5 h-3.5"})," 2D TREEMAP"]})]})]}),s?.folders&&Object.keys(s.folders).length>0?e.jsx("div",{className:"flex-1 min-h-0 flex flex-col relative",children:u==="3d"?e.jsx(ne,{allFiles:s.folders,activeFilter:i,onFilterChange:x,initialMode:"type"}):e.jsx(U,{allFiles:s.folders,activeFilter:i,onFilterChange:x,initialMode:"type"})}):e.jsx("div",{className:"flex-1 flex flex-col items-center justify-center text-text-secondary text-sm bg-white/5 rounded-2xl border border-white/5",children:n?e.jsxs("div",{className:"flex flex-col items-center gap-3",children:[e.jsx(k,{className:"w-8 h-8 text-primary animate-spin"}),e.jsx("p",{children:"Loading folder structure..."})]}):e.jsxs("div",{className:"flex flex-col items-center gap-3 opacity-60",children:[e.jsx(z,{className:"w-12 h-12"}),e.jsx("p",{children:"No file hierarchy data available."})]})})]}),e.jsxs("div",{className:"glass-card space-y-6",children:[i&&e.jsxs("div",{className:"bg-primary/10 border border-primary/20 rounded-xl flex items-center justify-between p-3 shrink-0 shadow-sm animate-fade-in-up",children:[e.jsxs("div",{className:"flex items-center gap-3",children:[e.jsx(G,{className:"w-4 h-4 text-primary"}),e.jsxs("span",{className:"text-xs font-bold text-primary uppercase",children:[i," Active"]})]}),e.jsx("button",{onClick:()=>x(null),className:"text-[9px] font-black bg-primary/20 text-primary hover:bg-primary/30 px-2 py-1 rounded transition-all",children:"CLEAR"})]}),e.jsxs("div",{children:[e.jsxs("h2",{className:"text-lg font-semibold mb-3 flex items-center gap-2 text-text-primary",children:[e.jsx(Y,{className:"w-5 h-5 text-warning"}),"Top Files"]}),_?e.jsx("div",{className:"flex items-center justify-center py-12",children:e.jsx(k,{className:"w-6 h-6 text-primary animate-spin"})}):w?e.jsx("div",{className:"text-center py-8",children:e.jsx("p",{className:"text-error text-sm font-medium",children:w})}):a.length>0?e.jsx("div",{className:"space-y-2",children:a.slice(0,10).map(h=>e.jsxs("div",{className:"group flex items-center justify-between text-sm bg-white/5 hover:bg-white/10 rounded-xl px-4 py-3 transition-all border border-white/5",children:[e.jsx("span",{className:"truncate text-text-primary font-medium",children:h.path.split(/[\\/]/).pop()}),e.jsx("span",{className:"text-primary-light text-xs font-mono font-bold shrink-0 ml-2",children:j(h.size)})]},h.path))}):e.jsx("div",{className:"text-center py-8 opacity-40",children:e.jsx("p",{className:"text-text-secondary text-sm",children:i?`No ${i} files found`:"No files indexed yet"})})]}),!_&&p.length>0&&e.jsxs("div",{children:[e.jsxs("h2",{className:"text-lg font-semibold mb-3 flex items-center gap-2 text-text-primary",children:[e.jsx(H,{className:"w-5 h-5 text-accent"}),"Cold Files"]}),e.jsx("div",{className:"space-y-2",children:p.slice(0,8).map(h=>e.jsxs("div",{className:"group flex items-center justify-between text-sm bg-white/5 hover:bg-white/10 rounded-xl px-4 py-3 transition-all border border-white/5",children:[e.jsx("span",{className:"truncate text-text-primary font-medium",children:h.path.split(/[\\/]/).pop()}),e.jsx("span",{className:"text-accent text-xs font-bold shrink-0 ml-2",children:h.usage_count===void 0?j(h.size||0):`${h.usage_count} hits`})]},h.path))})]})]})]}),o.error&&e.jsxs("div",{className:"glass-card bg-warning/10 text-warning text-sm",children:["Partial data — some statistics unavailable: ",o.error]})]}),!t&&o&&C===0&&e.jsxs("div",{className:"glass-card text-center py-12",children:[e.jsx(T,{className:"w-12 h-12 text-primary/20 mx-auto mb-4"}),e.jsx("p",{className:"text-text-secondary",children:"Index some files to generate insights about your personal data."})]})]})}export{ce as InsightsPage};
