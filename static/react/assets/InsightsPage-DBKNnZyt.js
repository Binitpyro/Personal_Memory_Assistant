import{c as y,j as t,o as S,u as F,q as U,C as _,t as A,n as O}from"./index-CMBcoCLW.js";import{a as d}from"./echarts-Dso2f9nO.js";import{F as G,L as R}from"./FileTypeTreemap-BONoz2Yt.js";import{L as P}from"./trash-2-CGfVKJdY.js";import{H as L}from"./hard-drive-C1HvAbz_.js";const D=[["path",{d:"M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z",key:"hh9hay"}],["path",{d:"m3.3 7 8.7 5 8.7-5",key:"g66t2b"}],["path",{d:"M12 22V12",key:"d0xqtd"}]],z=y("box",D);const V=[["path",{d:"M21 12c.552 0 1.005-.449.95-.998a10 10 0 0 0-8.953-8.951c-.55-.055-.998.398-.998.95v8a1 1 0 0 0 1 1z",key:"pzmjnu"}],["path",{d:"M21.21 15.89A10 10 0 1 1 8 2.83",key:"k2fpak"}]],k=y("chart-pie",V);const I=[["path",{d:"M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z",key:"1oefj6"}],["path",{d:"M14 2v5a1 1 0 0 0 1 1h5",key:"wfsgrz"}],["path",{d:"M11 18h2",key:"12mj7e"}],["path",{d:"M12 12v6",key:"3ahymv"}],["path",{d:"M9 13v-.5a.5.5 0 0 1 .5-.5h5a.5.5 0 0 1 .5.5v.5",key:"qbrxap"}]],E=y("file-type",I);const q=[["path",{d:"M12 3q1 4 4 6.5t3 5.5a1 1 0 0 1-14 0 5 5 0 0 1 1-3 1 1 0 0 0 5 0c0-2-1.5-3-1.5-5q0-2 2.5-4",key:"1slcih"}]],Y=y("flame",q);const H=[["path",{d:"m10 20-1.25-2.5L6 18",key:"18frcb"}],["path",{d:"M10 4 8.75 6.5 6 6",key:"7mghy3"}],["path",{d:"m14 20 1.25-2.5L18 18",key:"1chtki"}],["path",{d:"m14 4 1.25 2.5L18 6",key:"1b4wsy"}],["path",{d:"m17 21-3-6h-4",key:"15hhxa"}],["path",{d:"m17 3-3 6 1.5 3",key:"11697g"}],["path",{d:"M2 12h6.5L10 9",key:"kv9z4n"}],["path",{d:"m20 10-1.5 2 1.5 2",key:"1swlpi"}],["path",{d:"M22 12h-6.5L14 15",key:"1mxi28"}],["path",{d:"m4 10 1.5 2L4 14",key:"k9enpj"}],["path",{d:"m7 21 3-6-1.5-3",key:"j8hb9u"}],["path",{d:"m7 3 3 6h4",key:"1otusx"}]],W=y("snowflake",H);const X=[["path",{d:"M16 7h6v6",key:"box55l"}],["path",{d:"m22 7-8.5 8.5-5-5L2 17",key:"1t1m79"}]],$=y("trending-up",X);class Z{device;numElements=0;elementBuffer;mortonCodeBuffer;bvhNodeBuffer;mortonPipeline;constructor(e){this.device=e}async initializePipelines(){const e=this.device.createShaderModule({label:"Morton Code Generation",code:`
                struct Element {
                    pos: vec3<f32>,
                    size: f32,
                    typeHash: u32,
                };
                
                struct MortonEntry {
                    code: u32,
                    index: u32,
                };

                @group(0) @binding(0) var<storage, read> elements: array<Element>;
                @group(0) @binding(1) var<storage, read_write> mortonCodes: array<MortonEntry>;
                
                fn expandBits(vIn: u32) -> u32 {
                    var v = vIn;
                    v = (v * 0x00010001u) & 0xFF0000FFu;
                    v = (v * 0x00000101u) & 0x0F00F00Fu;
                    v = (v * 0x00000011u) & 0xC30C30C3u;
                    v = (v * 0x00000005u) & 0x49249249u;
                    return v;
                }

                @compute @workgroup_size(256)
                fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
                    let idx = global_id.x;
                    if (idx >= arrayLength(&elements)) { return; }
                    
                    let e = elements[idx];
                    
                    // Normalize position (assuming scene bounds are pre-calculated. 
                    // For now, mock bounds [0..1] range mapping)
                    let p = clamp(e.pos, vec3<f32>(0.0), vec3<f32>(1.0));
                    
                    let xx = expandBits(u32(p.x * 1023.0));
                    let yy = expandBits(u32(p.y * 1023.0));
                    let zz = expandBits(u32(p.z * 1023.0));
                    
                    let code = (xx * 4u) + (yy * 2u) + zz;
                    mortonCodes[idx] = MortonEntry(code, idx);
                }
            `});this.mortonPipeline=this.device.createComputePipeline({label:"Morton Pipeline",layout:"auto",compute:{module:e,entryPoint:"main"}})}async uploadData(e){const r=new Uint32Array(e,0,1);this.numElements=r[0];const i=4,n=e.byteLength-i;this.elementBuffer=this.device.createBuffer({size:n,usage:GPUBufferUsage.STORAGE|GPUBufferUsage.COPY_DST|GPUBufferUsage.VERTEX,mappedAtCreation:!0}),new Uint8Array(this.elementBuffer.getMappedRange()).set(new Uint8Array(e,i)),this.elementBuffer.unmap(),this.mortonCodeBuffer=this.device.createBuffer({size:this.numElements*8,usage:GPUBufferUsage.STORAGE|GPUBufferUsage.COPY_SRC|GPUBufferUsage.COPY_DST}),this.bvhNodeBuffer=this.device.createBuffer({size:Math.max(1,this.numElements*2-1)*32,usage:GPUBufferUsage.STORAGE|GPUBufferUsage.COPY_SRC})}build(){if(!this.numElements)return;const e=this.device.createCommandEncoder(),r=e.beginComputePass(),i=this.device.createBindGroup({layout:this.mortonPipeline.getBindGroupLayout(0),entries:[{binding:0,resource:{buffer:this.elementBuffer}},{binding:1,resource:{buffer:this.mortonCodeBuffer}}]});r.setPipeline(this.mortonPipeline),r.setBindGroup(0,i),r.dispatchWorkgroups(Math.ceil(this.numElements/256)),r.end(),this.device.queue.submit([e.finish()])}getBVHBuffer(){return this.bvhNodeBuffer}getElementBuffer(){return this.elementBuffer}getElementCount(){return this.numElements}destroy(){this.elementBuffer&&this.elementBuffer.destroy(),this.mortonCodeBuffer&&this.mortonCodeBuffer.destroy(),this.bvhNodeBuffer&&this.bvhNodeBuffer.destroy(),this.numElements=0}}const K=`struct CameraUniform {\r
    viewProj: mat4x4<f32>,\r
    // This padding bridges the gap from byte 64 to byte 160 to match your TS code\r
    padding: array<vec4<f32>, 6>, \r
    eyePosition: vec3<f32>,\r
};\r
\r
@group(0) @binding(0) var<uniform> camera: CameraUniform;\r
\r
struct VertexInput {\r
    @location(0) quad_pos: vec2<f32>,      \r
    @location(1) instancePos: vec3<f32>,   \r
    @location(2) instanceSize: f32,\r
    @location(3) typeHash: u32,\r
};\r
\r
struct VertexOutput {\r
    @builtin(position) clip_position: vec4<f32>,\r
    @location(0) view_depth: f32,\r
    @location(1) local_uv: vec2<f32>,      \r
    @location(2) @interpolate(flat) type_hash: u32,\r
    @location(3) is_folder: f32,\r
    @location(4) sphere_radius: f32,\r
};\r
\r
@vertex\r
fn vs_main(in: VertexInput) -> VertexOutput {\r
    var out: VertexOutput;\r
    \r
    // Determine if it's a folder (negative size) or file (positive size)\r
    let is_folder = step(in.instanceSize, 0.0); \r
    let actual_size = abs(in.instanceSize);\r
    \r
    // BILLBOARDING: Calculate vectors so the quad always faces the camera\r
    let forward = normalize(camera.eyePosition - in.instancePos);\r
    let right = normalize(cross(vec3<f32>(0.0, 1.0, 0.0), forward));\r
    let up = cross(forward, right);\r
    \r
    // Expand the flat quad into 3D world space\r
    let localOffset = (right * in.quad_pos.x + up * in.quad_pos.y) * actual_size;\r
    let worldPos = in.instancePos + localOffset;\r
    \r
    out.clip_position = camera.viewProj * vec4<f32>(worldPos, 1.0);\r
    out.view_depth = out.clip_position.w; \r
    out.local_uv = in.quad_pos; // Ranges from -1 to 1\r
    out.type_hash = in.typeHash;\r
    out.is_folder = is_folder;\r
    out.sphere_radius = actual_size;\r
    \r
    return out;\r
}\r
\r
struct MBOITOutput {\r
    @location(0) moments: vec4<f32>,\r
    @location(1) color: vec4<f32>,\r
};\r
\r
// Deterministic noise for unique crystal cuts\r
fn hash31(p: vec3<f32>, seed: u32) -> f32 {\r
    let p3 = fract(p * 0.1031 + f32(seed) * 0.01);\r
    let h = dot(p3, p3.yzx + 33.33);\r
    return fract((h + p3.x) * p3.y);\r
}\r
\r
@fragment\r
fn fs_main(in: VertexOutput) -> MBOITOutput {\r
    var out: MBOITOutput;\r
    \r
    // RAYCAST: Carve the quad into a perfect circle\r
    let distSq = dot(in.local_uv, in.local_uv);\r
    if (distSq > 1.0) { discard; }\r
    \r
    // 3D FORGING: Calculate the procedural Z-depth of the surface\r
    let z = sqrt(1.0 - distSq);\r
    var localNormal = vec3<f32>(in.local_uv.x, in.local_uv.y, z);\r
    \r
    var finalColor: vec3<f32>;\r
    var alpha: f32;\r
\r
    if (in.is_folder > 0.5) {\r
        // --- THE CRYSTAL (Folders) ---\r
        // Every folder gets a unique faceted cut based on its hash\r
        let hash_seed = in.type_hash;\r
        let noise = vec3<f32>(\r
            hash31(localNormal, hash_seed) - 0.5,\r
            hash31(localNormal, hash_seed + 1u) - 0.5,\r
            hash31(localNormal, hash_seed + 2u) - 0.5\r
        );\r
        \r
        let perturbedNormal = normalize(localNormal + noise * 0.8);\r
        let facets = 3.0 + f32(hash_seed % 4u); \r
        let facetedNormal = normalize(round(perturbedNormal * facets) / facets);\r
        \r
        // Glassy specular highlight\r
        let dt = dot(vec3<f32>(0.0, 0.0, 1.0), facetedNormal);\r
        let baseColor = vec3<f32>(0.2, 0.6, 0.9) + vec3<f32>(f32(hash_seed % 10u)/20.0, 0.05, 0.1);\r
        finalColor = baseColor + pow(max(dt, 0.0), 12.0);\r
        alpha = 0.85; \r
        \r
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
        let rim = pow(1.0 - dt, 3.0); \r
        finalColor = iridescence * rim;\r
        alpha = rim * 0.9 + 0.05; \r
    }\r
    \r
    // MBOIT: Moment-Based Order Independent Transparency\r
    // Push depth based on the sphere's curvature for a 3D feel\r
    let depth = in.view_depth - (z * in.sphere_radius); \r
    let d2 = depth * depth;\r
    let d3 = d2 * depth;\r
    \r
    out.moments = vec4<f32>(1.0, depth, d2, d3) * alpha;\r
    out.color = vec4<f32>(finalColor * alpha, alpha);\r
    \r
    return out;\r
}`,J=`// frontend/src/renderer/shaders/oit_resolve.wgsl\r
\r
struct FullscreenVertexOutput {\r
    @builtin(position) position: vec4<f32>,\r
    @location(0) uv: vec2<f32>,\r
};\r
\r
@vertex\r
fn vs_main(@builtin(vertex_index) vertex_index : u32) -> FullscreenVertexOutput {\r
    var out: FullscreenVertexOutput;\r
    // Giant Triangle math: creates a triangle that covers the whole screen\r
    let x = -1.0 + f32((vertex_index & 1u) << 2u);\r
    let y = -1.0 + f32((vertex_index & 2u) << 1u);\r
    out.position = vec4<f32>(x, y, 0.0, 1.0);\r
    out.uv = vec2<f32>(x * 0.5 + 0.5, 1.0 - (y * 0.5 + 0.5));\r
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
    if (moments.x <= 0.0001) {\r
        // Return the clear color of the background\r
        return vec4<f32>(0.945, 0.96, 0.878, 1.0);\r
    }\r
\r
    // Standard MBOIT resolve logic\r
    let avgColor = accum.rgb / max(accum.a, 0.0001);\r
    let transmittance = moments.x; // Basic OIT for now\r
    \r
    return vec4<f32>(avgColor * transmittance + vec3<f32>(0.945, 0.96, 0.878) * (1.0 - transmittance), 1.0);\r
}`;class Q{canvas;device;context;format;momentTexture;colorTexture;depthTexture;cameraBuffer;geometryBuffer;bubblePipeline;resolvePipeline;renderBindGroup;resolveBindGroup;bvh;rotationX=.5;rotationY=.5;zoom=550;constructor(e){this.canvas=e}async init(){if(!navigator.gpu)throw new Error("WebGPU not supported on this browser.");const e=await navigator.gpu.requestAdapter();if(!e)throw new Error("No appropriate GPUAdapter found.");this.device=await e.requestDevice({requiredLimits:{maxStorageBufferBindingSize:e.limits.maxStorageBufferBindingSize,maxComputeWorkgroupStorageSize:e.limits.maxComputeWorkgroupStorageSize,maxBufferSize:e.limits.maxBufferSize}}),this.context=this.canvas.getContext("webgpu"),this.format=navigator.gpu.getPreferredCanvasFormat(),this.context.configure({device:this.device,format:this.format,alphaMode:"premultiplied"}),this.bvh=new Z(this.device),await this.createGeometryBuffer(),this.canvas.width=Math.max(1,this.canvas.clientWidth),this.canvas.height=Math.max(1,this.canvas.clientHeight),this.setupTextures(),await this.setupPipelines()}async createGeometryBuffer(){const e=new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]);this.geometryBuffer=this.device.createBuffer({size:e.byteLength,usage:GPUBufferUsage.VERTEX,mappedAtCreation:!0}),new Float32Array(this.geometryBuffer.getMappedRange()).set(e),this.geometryBuffer.unmap()}setupTextures(){const e={width:this.canvas.width,height:this.canvas.height};this.momentTexture=this.device.createTexture({size:e,format:"rgba16float",usage:GPUTextureUsage.RENDER_ATTACHMENT|GPUTextureUsage.TEXTURE_BINDING}),this.colorTexture=this.device.createTexture({size:e,format:"rgba16float",usage:GPUTextureUsage.RENDER_ATTACHMENT|GPUTextureUsage.TEXTURE_BINDING}),this.depthTexture=this.device.createTexture({size:e,format:"depth32float",usage:GPUTextureUsage.RENDER_ATTACHMENT})}resize(e,r){const i=Math.max(1,e),n=Math.max(1,r);this.canvas.width===i&&this.canvas.height===n||(this.canvas.width=i,this.canvas.height=n,this.momentTexture&&this.momentTexture.destroy(),this.colorTexture&&this.colorTexture.destroy(),this.depthTexture&&this.depthTexture.destroy(),this.setupTextures(),this.resolvePipeline&&(this.resolveBindGroup=this.device.createBindGroup({layout:this.resolvePipeline.getBindGroupLayout(0),entries:[{binding:0,resource:this.momentTexture.createView()},{binding:1,resource:this.colorTexture.createView()}]})))}async setupPipelines(){await this.bvh.initializePipelines();const e=this.device.createShaderModule({code:K});this.bubblePipeline=this.device.createRenderPipeline({layout:"auto",vertex:{module:e,entryPoint:"vs_main",buffers:[{arrayStride:8,attributes:[{shaderLocation:0,offset:0,format:"float32x2"}]},{arrayStride:20,stepMode:"instance",attributes:[{shaderLocation:1,offset:0,format:"float32x3"},{shaderLocation:2,offset:12,format:"float32"},{shaderLocation:3,offset:16,format:"uint32"}]}]},fragment:{module:e,entryPoint:"fs_main",targets:[{format:"rgba16float",blend:{color:{srcFactor:"one",dstFactor:"one",operation:"add"},alpha:{srcFactor:"one",dstFactor:"one",operation:"add"}}},{format:"rgba16float",blend:{color:{srcFactor:"one",dstFactor:"one",operation:"add"},alpha:{srcFactor:"one",dstFactor:"one",operation:"add"}}}]},primitive:{topology:"triangle-list"},depthStencil:{depthWriteEnabled:!1,depthCompare:"less-equal",format:"depth32float"}});const r=this.device.createShaderModule({code:J});this.resolvePipeline=this.device.createRenderPipeline({layout:"auto",vertex:{module:r,entryPoint:"vs_main"},fragment:{module:r,entryPoint:"fs_main",targets:[{format:this.format,blend:{color:{srcFactor:"src-alpha",dstFactor:"one-minus-src-alpha",operation:"add"},alpha:{srcFactor:"one",dstFactor:"one-minus-src-alpha",operation:"add"}}}]}})}async loadData(e){await this.bvh.uploadData(e),this.bvh.build(),this.cameraBuffer=this.device.createBuffer({size:256,usage:GPUBufferUsage.UNIFORM|GPUBufferUsage.COPY_DST}),this.updateCamera(),this.renderBindGroup=this.device.createBindGroup({layout:this.bubblePipeline.getBindGroupLayout(0),entries:[{binding:0,resource:{buffer:this.cameraBuffer}}]}),this.resolveBindGroup=this.device.createBindGroup({layout:this.resolvePipeline.getBindGroupLayout(0),entries:[{binding:0,resource:this.momentTexture.createView()},{binding:1,resource:this.colorTexture.createView()}]})}handleMouseMove(e,r){this.rotationY-=e*.01,this.rotationX-=r*.01,this.rotationX=Math.max(-Math.PI/2+.1,Math.min(Math.PI/2-.1,this.rotationX))}handleZoom(e){this.zoom=Math.max(10,Math.min(500,this.zoom+e*.05))}updateCamera(){const e=this.canvas.width/this.canvas.height,r=this.perspective(45*Math.PI/180,e,.1,5e3),i=this.zoom*Math.cos(this.rotationX)*Math.sin(this.rotationY),n=this.zoom*Math.sin(this.rotationX),s=this.zoom*Math.cos(this.rotationX)*Math.cos(this.rotationY),c=this.lookAt([i,n,s],[0,0,0],[0,1,0]),o=this.multiply(r,c);this.device.queue.writeBuffer(this.cameraBuffer,0,o),this.device.queue.writeBuffer(this.cameraBuffer,64,new Float32Array(24)),this.device.queue.writeBuffer(this.cameraBuffer,160,new Float32Array([i,n,s]))}perspective(e,r,i,n){const s=1/Math.tan(e/2),c=new Float32Array(16);return c[0]=s/r,c[5]=s,c[10]=n/(i-n),c[11]=-1,c[14]=i*n/(i-n),c}lookAt(e,r,i){const n=this.normalize(this.subtract(e,r)),s=this.normalize(this.cross(i,n)),c=this.cross(n,s),o=new Float32Array(16);return o[0]=s[0],o[4]=s[1],o[8]=s[2],o[12]=-this.dot(s,e),o[1]=c[0],o[5]=c[1],o[9]=c[2],o[13]=-this.dot(c,e),o[2]=n[0],o[6]=n[1],o[10]=n[2],o[14]=-this.dot(n,e),o[3]=0,o[7]=0,o[11]=0,o[15]=1,o}multiply(e,r){const i=new Float32Array(16);for(let n=0;n<4;n++)for(let s=0;s<4;s++)i[n*4+s]=e[0+s]*r[n*4+0]+e[4+s]*r[n*4+1]+e[8+s]*r[n*4+2]+e[12+s]*r[n*4+3];return i}subtract(e,r){return[e[0]-r[0],e[1]-r[1],e[2]-r[2]]}normalize(e){const r=Math.sqrt(e[0]*e[0]+e[1]*e[1]+e[2]*e[2]);return[e[0]/r,e[1]/r,e[2]/r]}cross(e,r){return[e[1]*r[2]-e[2]*r[1],e[2]*r[0]-e[0]*r[2],e[0]*r[1]-e[1]*r[0]]}dot(e,r){return e[0]*r[0]+e[1]*r[1]+e[2]*r[2]}render(){if(!this.bvh.getElementCount()||!this.cameraBuffer){const n=this.device.createCommandEncoder();n.beginRenderPass({colorAttachments:[{view:this.context.getCurrentTexture().createView(),loadOp:"clear",clearValue:[.945,.96,.878,1],storeOp:"store"}]}).end(),this.device.queue.submit([n.finish()]);return}this.updateCamera();const e=this.device.createCommandEncoder(),r=e.beginRenderPass({colorAttachments:[{view:this.momentTexture.createView(),loadOp:"clear",clearValue:[0,0,0,0],storeOp:"store"},{view:this.colorTexture.createView(),loadOp:"clear",clearValue:[0,0,0,0],storeOp:"store"}],depthStencilAttachment:{view:this.depthTexture.createView(),depthClearValue:1,depthLoadOp:"clear",depthStoreOp:"store"}});r.setPipeline(this.bubblePipeline),r.setBindGroup(0,this.renderBindGroup),r.setVertexBuffer(0,this.geometryBuffer),r.setVertexBuffer(1,this.bvh.getElementBuffer()),r.draw(6,this.bvh.getElementCount(),0,0),r.end();const i=e.beginRenderPass({colorAttachments:[{view:this.context.getCurrentTexture().createView(),loadOp:"clear",clearValue:[.945,.96,.878,1],storeOp:"store"}]});i.setPipeline(this.resolvePipeline),i.setBindGroup(0,this.resolveBindGroup),i.draw(3),i.end(),this.device.queue.submit([e.finish()])}destroy(){this.momentTexture&&this.momentTexture.destroy(),this.colorTexture&&this.colorTexture.destroy(),this.depthTexture&&this.depthTexture.destroy(),this.cameraBuffer&&this.cameraBuffer.destroy(),this.geometryBuffer&&this.geometryBuffer.destroy(),this.bvh&&typeof this.bvh.destroy=="function"&&this.bvh.destroy(),this.device&&this.device.destroy()}}const ee=()=>{const a=d.useRef(null),e=d.useRef(null),r=d.useRef(0),[i,n]=d.useState(!1),s=d.useRef({x:0,y:0}),[c,o]=d.useState(null);d.useEffect(()=>{if(!a.current)return;const u=a.current,h=new Q(u);e.current=h;let f=!1,p=null;return(async()=>{try{if(await h.init(),f)return;p=new ResizeObserver(x=>{for(let l of x){const{width:v,height:j}=l.contentRect;v>0&&j>0&&e.current&&e.current.resize(v,j)}}),p.observe(u);const m=await S();if(f)return;if(m.byteLength>4){const x=new Uint8Array(m,0,4);if(console.log("FIRST 4 BYTES FROM BACKEND:",x),x[0]===60&&x[1]===33){console.error("VITE TRAP: The backend sent HTML instead of Binary 3D Data!"),o("Backend disconnected. Vite sent HTML.");return}await h.loadData(m)}else{f||o("No 3D data available. Please index some files first.");return}const B=()=>{h.render(),r.current=requestAnimationFrame(B)};r.current=requestAnimationFrame(B)}catch(m){console.error("Failed to initialize WebGPU:",m),f||o(m instanceof Error?m.message:"Unknown error loading 3D data")}})(),()=>{f=!0,r.current&&cancelAnimationFrame(r.current),p&&p.disconnect(),e.current&&e.current.destroy()}},[]);const b=u=>{n(!0),s.current={x:u.clientX,y:u.clientY}},w=u=>{if(!i||!e.current)return;const h=u.clientX-s.current.x,f=u.clientY-s.current.y;e.current.handleMouseMove(h,f),s.current={x:u.clientX,y:u.clientY}},g=()=>n(!1),N=u=>{e.current&&e.current.handleZoom(u.deltaY)};return c?t.jsx("div",{className:"w-full h-full min-h-[400px] flex items-center justify-center bg-error/5 text-error rounded-3xl border border-error/20",children:t.jsxs("div",{className:"text-center p-6",children:[t.jsx("p",{className:"font-bold mb-2",children:"Failed to load Crystal Dreamscape"}),t.jsx("p",{className:"text-xs opacity-80",children:c})]})}):t.jsxs("div",{className:"w-full h-full min-h-[500px] relative bg-[#f1f5e0] rounded-3xl overflow-hidden border border-white/40 shadow-inner",children:[t.jsxs("div",{className:"absolute top-6 left-8 z-10 pointer-events-none",children:[t.jsxs("h2",{className:"text-2xl font-bold text-primary flex items-center gap-3",children:[t.jsx("span",{className:"w-3 h-3 bg-accent rounded-full animate-pulse shadow-[0_0_12px_rgba(142,72,234,0.6)]"}),"Crystal Dreamscape 3D"]}),t.jsx("p",{className:"text-text-secondary text-[10px] font-bold mt-2 tracking-widest uppercase opacity-60",children:"DreamScape 3D"})]}),t.jsx("canvas",{ref:a,className:"w-full h-full cursor-grab active:cursor-grabbing block",style:{minHeight:"500px",height:"100%",width:"100%"},onMouseDown:b,onMouseMove:w,onMouseUp:g,onMouseLeave:g,onWheel:N})]})},te=({allFiles:a,activeFilter:e,onFilterChange:r,initialMode:i})=>{const[n,s]=d.useState("checking");return d.useEffect(()=>{(async()=>{if(!navigator.gpu){s("unsupported");return}try{if(!await navigator.gpu.requestAdapter()){s("unsupported");return}s("supported")}catch(o){console.error("WebGPU initialization failed: ",o),s("unsupported")}})()},[]),n==="checking"?t.jsx("div",{className:"w-full h-[600px] bg-slate-900 flex items-center justify-center rounded-lg border border-slate-800",children:t.jsxs("div",{className:"flex flex-col items-center",children:[t.jsx("div",{className:"w-12 h-12 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin"}),t.jsx("p",{className:"mt-4 text-slate-400 font-mono text-sm",children:"Initializing GPU Infrastructure..."})]})}):n==="unsupported"?t.jsxs("div",{className:"w-full h-full flex flex-col",children:[t.jsx("div",{className:"bg-amber-900/30 border-l-4 border-amber-500 p-4 mb-4",children:t.jsxs("p",{className:"text-amber-200 text-sm",children:[t.jsx("span",{className:"font-bold",children:"WebGPU Not Available:"})," Your browser does not support WebGPU or it is disabled. Falling back to 2D Hardware-Accelerated Charts."]})}),t.jsx("div",{className:"flex-1 min-h-[600px]",children:t.jsx(G,{allFiles:a,activeFilter:e,onFilterChange:r,initialMode:i})})]}):t.jsx(ee,{})};function T(a){return a<1024?`${a} B`:a<1024*1024?`${(a/1024).toFixed(1)} KB`:a<1024*1024*1024?`${(a/(1024*1024)).toFixed(1)} MB`:`${(a/(1024*1024*1024)).toFixed(2)} GB`}function oe(){const{data:a,loading:e,error:r}=F(A,{cacheKey:"insights"}),{data:i,loading:n}=F(O,{cacheKey:"file-tree"}),[s,c]=d.useState(null),[o,b]=d.useState([]),[w,g]=d.useState([]),[N,u]=d.useState(!1),[h,f]=d.useState("3d"),p=d.useCallback(l=>{c(l)},[]);d.useEffect(()=>{if(!s){b(a?.top_files??[]),g(a?.cold_files??[]);return}let l=!1;return u(!0),U(s).then(v=>{l||(b(v.top_files??[]),g(v.cold_files??[]))}).catch(()=>{l||(b([]),g([]))}).finally(()=>{l||u(!1)}),()=>{l=!0}},[s,a]);const C=d.useMemo(()=>a?.type_breakdown?Object.keys(a.type_breakdown).length:0,[a]),m=a?T(a.total_size_bytes):"—",B=a?T(a.database_size_bytes):"—",x=a?.file_count??0;return t.jsxs("div",{className:"flex-1 overflow-y-auto p-6 space-y-6 animate-fade-in-up custom-scrollbar",children:[t.jsx("div",{className:"flex items-center justify-between",children:t.jsxs("div",{children:[t.jsxs("h1",{className:"text-2xl font-bold flex items-center gap-3",children:[t.jsx(_,{className:"w-7 h-7 text-primary"}),"Insights"]}),t.jsx("p",{className:"text-text-secondary mt-1 text-sm",children:"Analytics and visualizations of your personal data"})]})}),r&&t.jsx("div",{className:"glass-card bg-error/10 text-error text-sm",children:r}),e&&!a&&t.jsx("div",{className:"glass-card flex items-center justify-center py-16",children:t.jsx(P,{className:"w-8 h-8 text-primary animate-spin"})}),a&&t.jsxs(t.Fragment,{children:[t.jsx("div",{className:"grid grid-cols-1 md:grid-cols-5 gap-4",children:[{label:"Total Files",value:x.toLocaleString(),icon:E,color:"text-primary-light"},{label:"Indexed Files Size",value:m,icon:k,color:"text-accent"},{label:"Database Size",value:B,icon:L,color:"text-primary"},{label:"File Types",value:C.toString(),icon:$,color:"text-success"},{label:"Top Used",value:(a?.top_files?.length??0).toString(),icon:_,color:"text-warning"}].map(({label:l,value:v,icon:j,color:M})=>t.jsxs("div",{className:"glass-card flex flex-col items-center justify-center py-6 px-4",children:[t.jsx(j,{className:`w-6 h-6 ${M} mb-2`}),t.jsx("span",{className:`text-xl font-bold ${M} text-center`,children:v}),t.jsx("span",{className:"text-text-secondary text-xs mt-1 text-center uppercase tracking-wider font-semibold",children:l})]},l))}),t.jsxs("div",{className:"grid grid-cols-1 lg:grid-cols-3 gap-4 flex-1",children:[t.jsxs("div",{className:"glass-card lg:col-span-2 flex flex-col min-h-[600px] h-full overflow-hidden",children:[t.jsxs("div",{className:"flex items-center justify-between mb-4 shrink-0",children:[t.jsxs("h2",{className:"text-lg font-bold text-primary flex items-center gap-2",children:[t.jsx(k,{className:"w-5 h-5"}),"File Type Hierarchy"]}),t.jsxs("div",{className:"flex items-center bg-black/5 p-1 rounded-xl border border-black/5 shadow-inner",children:[t.jsxs("button",{onClick:()=>f("3d"),className:`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold transition-all ${h==="3d"?"bg-primary text-white shadow-lg":"text-text-secondary hover:text-text-primary"}`,children:[t.jsx(z,{className:"w-3.5 h-3.5"})," 3D CRYSTAL"]}),t.jsxs("button",{onClick:()=>f("2d"),className:`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold transition-all ${h==="2d"?"bg-primary text-white shadow-lg":"text-text-secondary hover:text-text-primary"}`,children:[t.jsx(R,{className:"w-3.5 h-3.5"})," 2D TREEMAP"]})]})]}),i?.folders&&Object.keys(i.folders).length>0?t.jsx("div",{className:"flex-1 min-h-0 flex flex-col relative",children:h==="3d"?t.jsx(te,{allFiles:i.folders,activeFilter:s,onFilterChange:p,initialMode:"type"}):t.jsx(G,{allFiles:i.folders,activeFilter:s,onFilterChange:p,initialMode:"type"})}):t.jsx("div",{className:"flex-1 flex flex-col items-center justify-center text-text-secondary text-sm bg-white/5 rounded-2xl border border-white/5",children:n?t.jsxs("div",{className:"flex flex-col items-center gap-3",children:[t.jsx(P,{className:"w-8 h-8 text-primary animate-spin"}),t.jsx("p",{children:"Loading folder structure..."})]}):t.jsxs("div",{className:"flex flex-col items-center gap-3 opacity-60",children:[t.jsx(z,{className:"w-12 h-12"}),t.jsx("p",{children:"No file hierarchy data available."})]})})]}),t.jsxs("div",{className:"glass-card space-y-6",children:[s&&t.jsxs("div",{className:"bg-primary/10 border border-primary/20 rounded-xl flex items-center justify-between p-3 shrink-0 shadow-sm animate-fade-in-up",children:[t.jsxs("div",{className:"flex items-center gap-3",children:[t.jsx(E,{className:"w-4 h-4 text-primary"}),t.jsxs("span",{className:"text-xs font-bold text-primary uppercase",children:[s," Active"]})]}),t.jsx("button",{onClick:()=>p(null),className:"text-[9px] font-black bg-primary/20 text-primary hover:bg-primary/30 px-2 py-1 rounded transition-all",children:"CLEAR"})]}),t.jsxs("div",{children:[t.jsxs("h2",{className:"text-lg font-semibold mb-3 flex items-center gap-2 text-text-primary",children:[t.jsx(Y,{className:"w-5 h-5 text-warning"}),"Top Files"]}),N?t.jsx("div",{className:"flex items-center justify-center py-12",children:t.jsx(P,{className:"w-6 h-6 text-primary animate-spin"})}):o.length>0?t.jsx("div",{className:"space-y-2",children:o.slice(0,10).map(l=>t.jsxs("div",{className:"group flex items-center justify-between text-sm bg-white/5 hover:bg-white/10 rounded-xl px-4 py-3 transition-all border border-white/5",children:[t.jsx("span",{className:"truncate text-text-primary font-medium",children:l.path.split(/[\\/]/).pop()}),t.jsx("span",{className:"text-primary-light text-xs font-mono font-bold shrink-0 ml-2",children:T(l.size)})]},l.path))}):t.jsx("div",{className:"text-center py-8 opacity-40",children:t.jsx("p",{className:"text-text-secondary text-sm",children:s?`No ${s} files found`:"No files indexed yet"})})]}),!N&&w.length>0&&t.jsxs("div",{children:[t.jsxs("h2",{className:"text-lg font-semibold mb-3 flex items-center gap-2 text-text-primary",children:[t.jsx(W,{className:"w-5 h-5 text-accent"}),"Cold Files"]}),t.jsx("div",{className:"space-y-2",children:w.slice(0,8).map(l=>t.jsxs("div",{className:"group flex items-center justify-between text-sm bg-white/5 hover:bg-white/10 rounded-xl px-4 py-3 transition-all border border-white/5",children:[t.jsx("span",{className:"truncate text-text-primary font-medium",children:l.path.split(/[\\/]/).pop()}),t.jsx("span",{className:"text-accent text-xs font-bold shrink-0 ml-2",children:l.usage_count!==void 0?`${l.usage_count} hits`:T(l.size||0)})]},l.path))})]})]})]}),a.error&&t.jsxs("div",{className:"glass-card bg-warning/10 text-warning text-sm",children:["Partial data — some statistics unavailable: ",a.error]})]}),!e&&a&&x===0&&t.jsxs("div",{className:"glass-card text-center py-12",children:[t.jsx(_,{className:"w-12 h-12 text-primary/20 mx-auto mb-4"}),t.jsx("p",{className:"text-text-secondary",children:"Index some files to generate insights about your personal data."})]})]})}export{oe as InsightsPage};
