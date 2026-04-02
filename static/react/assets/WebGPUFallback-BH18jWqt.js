import{j as c,I as _}from"./index-jiolHXMf.js";import{a as f}from"./echarts-Dso2f9nO.js";import{F as B}from"./FileTypeTreemap-BKccNKFi.js";import"./trash-2-nWijmSmg.js";const T=`struct CameraUniform {\r
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
}`,C=`// frontend/src/renderer/shaders/oit_resolve.wgsl\r
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
}`,M=`struct CameraUniform {\r
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
`;class G{canvas;device;context;format;momentTexture;colorTexture;depthTexture;pickingTexture;cameraBuffer;geometryBuffer;pickBuffer;nodeBuffer;bubblePipeline;resolvePipeline;pickingPipeline;renderBindGroup;resolveBindGroup;pickingBindGroup;nodeCount=0;rotationX=.5;rotationY=.5;zoom=550;focusPosition=null;cameraPosition=[0,0,0];isFirstFrame=!0;constructor(e){this.canvas=e}async init(){if(!navigator.gpu)throw new Error("WebGPU not supported on this browser.");const e=await navigator.gpu.requestAdapter();if(!e)throw new Error("No appropriate GPUAdapter found.");this.device=await e.requestDevice({requiredLimits:{maxStorageBufferBindingSize:e.limits.maxStorageBufferBindingSize,maxComputeWorkgroupStorageSize:e.limits.maxComputeWorkgroupStorageSize,maxBufferSize:e.limits.maxBufferSize}}),this.context=this.canvas.getContext("webgpu"),this.format=navigator.gpu.getPreferredCanvasFormat(),this.context.configure({device:this.device,format:this.format,alphaMode:"premultiplied"}),this.pickBuffer=this.device.createBuffer({size:256,usage:GPUBufferUsage.COPY_DST|GPUBufferUsage.MAP_READ}),await this.createGeometryBuffer(),this.canvas.width=Math.max(1,this.canvas.clientWidth),this.canvas.height=Math.max(1,this.canvas.clientHeight),await this.setupPipelines(),this.setupTextures()}async createGeometryBuffer(){const e=new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]);this.geometryBuffer=this.device.createBuffer({size:e.byteLength,usage:GPUBufferUsage.VERTEX,mappedAtCreation:!0}),new Float32Array(this.geometryBuffer.getMappedRange()).set(e),this.geometryBuffer.unmap()}setupTextures(){const e={width:this.canvas.width,height:this.canvas.height};this.momentTexture=this.device.createTexture({size:e,format:"rgba16float",usage:GPUTextureUsage.RENDER_ATTACHMENT|GPUTextureUsage.TEXTURE_BINDING}),this.colorTexture=this.device.createTexture({size:e,format:"rgba16float",usage:GPUTextureUsage.RENDER_ATTACHMENT|GPUTextureUsage.TEXTURE_BINDING}),this.depthTexture=this.device.createTexture({size:e,format:"depth32float",usage:GPUTextureUsage.RENDER_ATTACHMENT}),this.pickingTexture=this.device.createTexture({size:e,format:"r32uint",usage:GPUTextureUsage.RENDER_ATTACHMENT|GPUTextureUsage.COPY_SRC}),this.resolveBindGroup=this.device.createBindGroup({layout:this.resolvePipeline.getBindGroupLayout(0),entries:[{binding:0,resource:this.momentTexture.createView()},{binding:1,resource:this.colorTexture.createView()}]})}resize(e,t){const n=Math.max(1,e),r=Math.max(1,t);this.canvas.width===n&&this.canvas.height===r||(this.canvas.width=n,this.canvas.height=r,this.momentTexture&&this.momentTexture.destroy(),this.colorTexture&&this.colorTexture.destroy(),this.depthTexture&&this.depthTexture.destroy(),this.pickingTexture&&this.pickingTexture.destroy(),this.setupTextures())}async setupPipelines(){const e=this.device.createShaderModule({code:T});this.bubblePipeline=this.device.createRenderPipeline({layout:"auto",vertex:{module:e,entryPoint:"vs_main",buffers:[{arrayStride:8,attributes:[{shaderLocation:0,offset:0,format:"float32x2"}]},{arrayStride:32,stepMode:"instance",attributes:[{shaderLocation:1,offset:0,format:"float32x3"},{shaderLocation:2,offset:12,format:"float32"},{shaderLocation:3,offset:20,format:"uint32"},{shaderLocation:4,offset:24,format:"uint32"}]}]},fragment:{module:e,entryPoint:"fs_main",targets:[{format:"rgba16float",blend:{color:{srcFactor:"one",dstFactor:"one",operation:"add"},alpha:{srcFactor:"one",dstFactor:"one",operation:"add"}}},{format:"rgba16float",blend:{color:{srcFactor:"one",dstFactor:"one",operation:"add"},alpha:{srcFactor:"one",dstFactor:"one",operation:"add"}}}]},primitive:{topology:"triangle-list"},depthStencil:{depthWriteEnabled:!1,depthCompare:"less",format:"depth32float"}});const t=this.device.createShaderModule({code:M});this.pickingPipeline=this.device.createRenderPipeline({layout:"auto",vertex:{module:t,entryPoint:"vs_main",buffers:[{arrayStride:8,attributes:[{shaderLocation:0,offset:0,format:"float32x2"}]},{arrayStride:32,stepMode:"instance",attributes:[{shaderLocation:1,offset:0,format:"float32x3"},{shaderLocation:2,offset:12,format:"float32"},{shaderLocation:3,offset:20,format:"uint32"},{shaderLocation:4,offset:24,format:"uint32"}]}]},fragment:{module:t,entryPoint:"fs_main",targets:[{format:"r32uint"}]},primitive:{topology:"triangle-list"},depthStencil:{depthWriteEnabled:!0,depthCompare:"less-equal",format:"depth32float"}});const n=this.device.createShaderModule({code:C});this.resolvePipeline=this.device.createRenderPipeline({layout:"auto",vertex:{module:n,entryPoint:"vs_main"},fragment:{module:n,entryPoint:"fs_main",targets:[{format:this.format,blend:{color:{srcFactor:"src-alpha",dstFactor:"one-minus-src-alpha",operation:"add"},alpha:{srcFactor:"one",dstFactor:"one-minus-src-alpha",operation:"add"}}}]}})}async loadData(e){this.nodeCount=Math.floor(e.byteLength/32),this.nodeCount!==0&&(this.nodeBuffer&&this.nodeBuffer.destroy(),this.nodeBuffer=this.device.createBuffer({size:Math.max(32,e.byteLength),usage:GPUBufferUsage.VERTEX|GPUBufferUsage.COPY_DST}),e.byteLength>0&&this.device.queue.writeBuffer(this.nodeBuffer,0,e),this.cameraBuffer||(this.cameraBuffer=this.device.createBuffer({size:80,usage:GPUBufferUsage.UNIFORM|GPUBufferUsage.COPY_DST})),this.updateCamera(),this.renderBindGroup=this.device.createBindGroup({layout:this.bubblePipeline.getBindGroupLayout(0),entries:[{binding:0,resource:{buffer:this.cameraBuffer}}]}),this.pickingBindGroup=this.device.createBindGroup({layout:this.pickingPipeline.getBindGroupLayout(0),entries:[{binding:0,resource:{buffer:this.cameraBuffer}}]}))}async pick(e,t){if(this.nodeCount===0||!this.cameraBuffer||!this.pickingTexture)return null;const n=Math.max(0,Math.min(Math.floor(e),this.canvas.width-1)),r=Math.max(0,Math.min(Math.floor(t),this.canvas.height-1));this.updateCamera();const i=this.device.createCommandEncoder(),a=i.beginRenderPass({colorAttachments:[{view:this.pickingTexture.createView(),loadOp:"clear",clearValue:{r:4294967295,g:0,b:0,a:0},storeOp:"store"}],depthStencilAttachment:{view:this.depthTexture.createView(),depthClearValue:1,depthLoadOp:"clear",depthStoreOp:"store"}});a.setPipeline(this.pickingPipeline),a.setScissorRect(n,r,1,1),this.pickingBindGroup&&a.setBindGroup(0,this.pickingBindGroup),this.geometryBuffer&&a.setVertexBuffer(0,this.geometryBuffer),this.nodeBuffer&&a.setVertexBuffer(1,this.nodeBuffer),a.draw(6,this.nodeCount,0,0),a.end(),i.copyTextureToBuffer({texture:this.pickingTexture,origin:[n,r,0]},{buffer:this.pickBuffer,bytesPerRow:256},[1,1,1]),this.device.queue.submit([i.finish()]),await this.pickBuffer.mapAsync(GPUMapMode.READ);const o=this.pickBuffer.getMappedRange(),d=new Uint32Array(o)[0],x=d===4294967295?null:d;return this.pickBuffer.unmap(),x}handleMouseMove(e,t){this.rotationY-=e*.005,this.rotationX+=t*.005,this.rotationX=Math.max(-Math.PI/2+.1,Math.min(Math.PI/2-.1,this.rotationX))}handleZoom(e){let t=Math.max(10,this.zoom*.05);this.zoom=Math.max(5,this.zoom+(e>0?t:-t))}updateCamera(){const e=this.canvas.width/this.canvas.height,t=this.perspective(45*Math.PI/180,e,.1,1e5);let n=this.focusPosition||[0,0,0];const r=n[0]+this.zoom*Math.cos(this.rotationX)*Math.sin(this.rotationY),i=n[1]+this.zoom*Math.sin(this.rotationX),a=n[2]+this.zoom*Math.cos(this.rotationX)*Math.cos(this.rotationY);this.isFirstFrame?(this.cameraPosition=[r,i,a],this.isFirstFrame=!1):(this.cameraPosition[0]+=(r-this.cameraPosition[0])*.1,this.cameraPosition[1]+=(i-this.cameraPosition[1])*.1,this.cameraPosition[2]+=(a-this.cameraPosition[2])*.1);const o=this.lookAt(this.cameraPosition,n,[0,1,0]),h=this.multiply(t,o);if(this.cameraBuffer){const d=new Float32Array(20);d.set(h,0),d.set([this.cameraPosition[0],this.cameraPosition[1],this.cameraPosition[2],0],16),this.device.queue.writeBuffer(this.cameraBuffer,0,d)}}perspective(e,t,n,r){const i=1/Math.tan(e/2),a=new Float32Array(16);return a[0]=i/t,a[5]=i,a[10]=r/(n-r),a[11]=-1,a[14]=n*r/(n-r),a}lookAt(e,t,n){const r=this.normalize(this.subtract(e,t)),i=this.normalize(this.cross(n,r)),a=this.cross(r,i),o=new Float32Array(16);return o[0]=i[0],o[4]=i[1],o[8]=i[2],o[12]=-this.dot(i,e),o[1]=a[0],o[5]=a[1],o[9]=a[2],o[13]=-this.dot(a,e),o[2]=r[0],o[6]=r[1],o[10]=r[2],o[14]=-this.dot(r,e),o[3]=0,o[7]=0,o[11]=0,o[15]=1,o}multiply(e,t){const n=new Float32Array(16);for(let r=0;r<4;r++)for(let i=0;i<4;i++)n[r*4+i]=e[0+i]*t[r*4+0]+e[4+i]*t[r*4+1]+e[8+i]*t[r*4+2]+e[12+i]*t[r*4+3];return n}subtract(e,t){return[e[0]-t[0],e[1]-t[1],e[2]-t[2]]}normalize(e){const t=Math.hypot(e[0],e[1],e[2]);return t===0?[0,0,1]:[e[0]/t,e[1]/t,e[2]/t]}cross(e,t){return[e[1]*t[2]-e[2]*t[1],e[2]*t[0]-e[0]*t[2],e[0]*t[1]-e[1]*t[0]]}dot(e,t){return e[0]*t[0]+e[1]*t[1]+e[2]*t[2]}render(){if(this.nodeCount===0||!this.cameraBuffer){const r=this.device.createCommandEncoder();r.beginRenderPass({colorAttachments:[{view:this.context.getCurrentTexture().createView(),loadOp:"clear",clearValue:[.945,.96,.878,1],storeOp:"store"}]}).end(),this.device.queue.submit([r.finish()]);return}this.updateCamera();const e=this.device.createCommandEncoder(),t=e.beginRenderPass({colorAttachments:[{view:this.momentTexture.createView(),loadOp:"clear",clearValue:[0,0,0,0],storeOp:"store"},{view:this.colorTexture.createView(),loadOp:"clear",clearValue:[0,0,0,0],storeOp:"store"}],depthStencilAttachment:{view:this.depthTexture.createView(),depthClearValue:1,depthLoadOp:"clear",depthStoreOp:"store"}});t.setPipeline(this.bubblePipeline),this.renderBindGroup&&t.setBindGroup(0,this.renderBindGroup),this.geometryBuffer&&t.setVertexBuffer(0,this.geometryBuffer),this.nodeBuffer&&t.setVertexBuffer(1,this.nodeBuffer),t.draw(6,this.nodeCount,0,0),t.end();const n=e.beginRenderPass({colorAttachments:[{view:this.context.getCurrentTexture().createView(),loadOp:"clear",clearValue:[.945,.96,.878,1],storeOp:"store"}]});n.setPipeline(this.resolvePipeline),this.resolveBindGroup&&n.setBindGroup(0,this.resolveBindGroup),n.draw(3),n.end(),this.device.queue.submit([e.finish()])}destroy(){this.momentTexture&&this.momentTexture.destroy(),this.colorTexture&&this.colorTexture.destroy(),this.depthTexture&&this.depthTexture.destroy(),this.pickingTexture&&this.pickingTexture.destroy(),this.cameraBuffer&&this.cameraBuffer.destroy(),this.geometryBuffer&&this.geometryBuffer.destroy(),this.pickBuffer&&this.pickBuffer.destroy(),this.nodeBuffer&&this.nodeBuffer.destroy(),this.device&&this.device.destroy(),this.context&&this.context.unconfigure()}}const U=({allFiles:v,activeFilter:e,onError:t})=>{const n=f.useRef(null),r=f.useRef(null),i=f.useRef(0),[a,o]=f.useState(!1),h=f.useRef({x:0,y:0}),d=f.useRef({x:0,y:0}),x=f.useCallback(async s=>{const u=new G(s);r.current=u;try{await u.init();const l=await _(e);if(l.byteLength>4){const m=new Uint8Array(l,0,2);if(m[0]===60&&m[1]===33)throw new Error("Backend sent HTML. Vite trap detected.");await u.loadData(l)}else throw new Error("No 3D data available or filter returned 0 results.");const p=()=>{u.render(),i.current=requestAnimationFrame(p)};return i.current=requestAnimationFrame(p),u}catch(l){return t(l instanceof Error?l.message:"Unknown error loading 3D data"),null}},[e,t]);f.useEffect(()=>{if(!n.current)return;const s=n.current;let u=null;return x(s).then(l=>{l&&(u=new ResizeObserver(p=>{for(const m of p){const{width:w,height:b}=m.contentRect;w>0&&b>0&&l.resize(w,b)}}),u.observe(s))}),()=>{i.current&&cancelAnimationFrame(i.current),u&&u.disconnect(),r.current&&r.current.destroy()}},[v,e,x]);const P=s=>{o(!0),h.current={x:s.clientX,y:s.clientY},d.current={x:s.clientX,y:s.clientY}},y=s=>{!a||!r.current||(r.current.handleMouseMove(s.clientX-h.current.x,s.clientY-h.current.y),h.current={x:s.clientX,y:s.clientY})},g=async s=>{o(!1);const u=Math.abs(s.clientX-d.current.x),l=Math.abs(s.clientY-d.current.y);if(u<5&&l<5&&r.current&&n.current){const p=n.current.getBoundingClientRect(),m=await r.current.pick(s.clientX-p.left,s.clientY-p.top);m!==null&&console.log("Picked 3D Node Hash:",m)}};return f.useEffect(()=>{const s=n.current;if(!s)return;const u=l=>{l.preventDefault(),l.stopPropagation(),r.current?.handleZoom(l.deltaY)};return s.addEventListener("wheel",u,{passive:!1}),()=>s.removeEventListener("wheel",u)},[]),c.jsxs("div",{className:"w-full h-full min-h-[400px] relative bg-[#f1f5e0] rounded-3xl overflow-hidden border border-white/40 shadow-inner",children:[c.jsxs("div",{className:"absolute top-6 left-8 z-10 pointer-events-none",children:[c.jsxs("h2",{className:"text-2xl font-bold text-primary flex items-center gap-3",children:[c.jsx("span",{className:"w-3 h-3 bg-accent rounded-full animate-pulse shadow-[0_0_12px_rgba(142,72,234,0.6)]"})," ","Crystal Dreamscape 3D"]}),c.jsx("p",{className:"text-text-secondary text-[10px] font-bold mt-2 tracking-widest uppercase opacity-60",children:"DreamScape 3D"})]}),c.jsx("canvas",{ref:n,className:"w-full h-full cursor-grab active:cursor-grabbing block touch-none",style:{minHeight:"400px",height:"100%",width:"100%",touchAction:"none"},onMouseDown:P,onMouseMove:y,onMouseUp:g,onMouseLeave:g})]})},E=({allFiles:v,activeFilter:e,onFilterChange:t,initialMode:n})=>{const[r,i]=f.useState("checking"),[a,o]=f.useState(null);return f.useEffect(()=>{(async()=>{if(!navigator.gpu){o("Browser doesn't support WebGPU."),i("unsupported");return}try{await navigator.gpu.requestAdapter()?i("supported"):(o("No appropriate GPU Adapter found."),i("unsupported"))}catch(d){console.error("GPU Check Error:",d),o("WebGPU initialization failed."),i("unsupported")}})()},[]),r==="checking"?c.jsx("div",{className:"w-full h-[600px] bg-slate-900 flex items-center justify-center rounded-lg border border-slate-800",children:c.jsxs("div",{className:"flex flex-col items-center",children:[c.jsx("div",{className:"w-12 h-12 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin"}),c.jsx("p",{className:"mt-4 text-slate-400 font-mono text-sm",children:"Initializing GPU Infrastructure..."})]})}):r==="unsupported"?c.jsxs("div",{className:"w-full h-full flex flex-col",children:[c.jsx("div",{className:"bg-amber-900/30 border-l-4 border-amber-500 p-4 mb-4",children:c.jsxs("p",{className:"text-amber-200 text-sm",children:[c.jsx("span",{className:"font-bold",children:"2D Hardware-Accelerated View:"})," ",a||"WebGPU Not Available"]})}),c.jsx("div",{className:"flex-1 min-h-[400px]",children:c.jsx(B,{allFiles:v,activeFilter:e,onFilterChange:t,initialMode:n})})]}):c.jsx(U,{allFiles:v,activeFilter:e,onError:h=>{o(`3D Stream Error: ${h}`),i("unsupported")}})};export{E as WebGPUFallback,E as default};
