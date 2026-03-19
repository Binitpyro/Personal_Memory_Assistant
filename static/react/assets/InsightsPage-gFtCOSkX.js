import{c as x,j as t,o as G,u as T,q as S,C as B,t as A,n as L}from"./index-Bxg2buyl.js";import{a as d}from"./echarts-Dso2f9nO.js";import{F as z,L as R}from"./FileTypeTreemap-DDXHvm7M.js";import{L as P}from"./trash-2-BoMIXI5U.js";import{H as O}from"./hard-drive-CoFAuwiX.js";const V=[["path",{d:"M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z",key:"hh9hay"}],["path",{d:"m3.3 7 8.7 5 8.7-5",key:"g66t2b"}],["path",{d:"M12 22V12",key:"d0xqtd"}]],M=x("box",V);const D=[["path",{d:"M21 12c.552 0 1.005-.449.95-.998a10 10 0 0 0-8.953-8.951c-.55-.055-.998.398-.998.95v8a1 1 0 0 0 1 1z",key:"pzmjnu"}],["path",{d:"M21.21 15.89A10 10 0 1 1 8 2.83",key:"k2fpak"}]],F=x("chart-pie",D);const I=[["path",{d:"M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z",key:"1oefj6"}],["path",{d:"M14 2v5a1 1 0 0 0 1 1h5",key:"wfsgrz"}],["path",{d:"M11 18h2",key:"12mj7e"}],["path",{d:"M12 12v6",key:"3ahymv"}],["path",{d:"M9 13v-.5a.5.5 0 0 1 .5-.5h5a.5.5 0 0 1 .5.5v.5",key:"qbrxap"}]],k=x("file-type",I);const q=[["path",{d:"M12 3q1 4 4 6.5t3 5.5a1 1 0 0 1-14 0 5 5 0 0 1 1-3 1 1 0 0 0 5 0c0-2-1.5-3-1.5-5q0-2 2.5-4",key:"1slcih"}]],W=x("flame",q);const Y=[["path",{d:"m10 20-1.25-2.5L6 18",key:"18frcb"}],["path",{d:"M10 4 8.75 6.5 6 6",key:"7mghy3"}],["path",{d:"m14 20 1.25-2.5L18 18",key:"1chtki"}],["path",{d:"m14 4 1.25 2.5L18 6",key:"1b4wsy"}],["path",{d:"m17 21-3-6h-4",key:"15hhxa"}],["path",{d:"m17 3-3 6 1.5 3",key:"11697g"}],["path",{d:"M2 12h6.5L10 9",key:"kv9z4n"}],["path",{d:"m20 10-1.5 2 1.5 2",key:"1swlpi"}],["path",{d:"M22 12h-6.5L14 15",key:"1mxi28"}],["path",{d:"m4 10 1.5 2L4 14",key:"k9enpj"}],["path",{d:"m7 21 3-6-1.5-3",key:"j8hb9u"}],["path",{d:"m7 3 3 6h4",key:"1otusx"}]],X=x("snowflake",Y);const $=[["path",{d:"M16 7h6v6",key:"box55l"}],["path",{d:"m22 7-8.5 8.5-5-5L2 17",key:"1t1m79"}]],H=x("trending-up",$);class Z{device;numElements=0;elementBuffer;mortonCodeBuffer;bvhNodeBuffer;mortonPipeline;constructor(e){this.device=e}async initializePipelines(){const e=this.device.createShaderModule({label:"Morton Code Generation",code:`
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
            `});this.mortonPipeline=this.device.createComputePipeline({label:"Morton Pipeline",layout:"auto",compute:{module:e,entryPoint:"main"}})}async uploadData(e){const r=new Uint32Array(e,0,1);this.numElements=r[0];const a=4,s=e.byteLength-a;this.elementBuffer=this.device.createBuffer({size:s,usage:GPUBufferUsage.STORAGE|GPUBufferUsage.COPY_DST|GPUBufferUsage.VERTEX,mappedAtCreation:!0}),new Uint8Array(this.elementBuffer.getMappedRange()).set(new Uint8Array(e,a)),this.elementBuffer.unmap(),this.mortonCodeBuffer=this.device.createBuffer({size:this.numElements*8,usage:GPUBufferUsage.STORAGE|GPUBufferUsage.COPY_SRC|GPUBufferUsage.COPY_DST}),this.bvhNodeBuffer=this.device.createBuffer({size:Math.max(1,this.numElements*2-1)*32,usage:GPUBufferUsage.STORAGE|GPUBufferUsage.COPY_SRC})}build(){if(!this.numElements)return;const e=this.device.createCommandEncoder(),r=e.beginComputePass(),a=this.device.createBindGroup({layout:this.mortonPipeline.getBindGroupLayout(0),entries:[{binding:0,resource:{buffer:this.elementBuffer}},{binding:1,resource:{buffer:this.mortonCodeBuffer}}]});r.setPipeline(this.mortonPipeline),r.setBindGroup(0,a),r.dispatchWorkgroups(Math.ceil(this.numElements/256)),r.end(),this.device.queue.submit([e.finish()])}getBVHBuffer(){return this.bvhNodeBuffer}getElementBuffer(){return this.elementBuffer}getElementCount(){return this.numElements}destroy(){this.elementBuffer&&this.elementBuffer.destroy(),this.mortonCodeBuffer&&this.mortonCodeBuffer.destroy(),this.bvhNodeBuffer&&this.bvhNodeBuffer.destroy(),this.numElements=0}}const K=`// bubble_mboit.wgsl\r
// Render file elements as iridescent bubbles, folders as sharp crystals\r
\r
struct CameraUniform {\r
    viewProj: mat4x4<f32>,\r
    planes: array<vec4<f32>, 6>,\r
    eyePosition: vec3<f32>,\r
};\r
\r
@group(0) @binding(0) var<uniform> camera: CameraUniform;\r
\r
struct VertexInput {\r
    @location(0) position: vec3<f32>,\r
    @location(1) normal: vec3<f32>,\r
    @location(2) instancePos: vec3<f32>,\r
    @location(3) instanceSize: f32,\r
    @location(4) typeHash: u32,\r
};\r
\r
struct VertexOutput {\r
    @builtin(position) clip_position: vec4<f32>,\r
    @location(0) view_depth: f32,\r
    @location(1) world_position: vec3<f32>,\r
    @location(2) normal: vec3<f32>,\r
    @location(3) @interpolate(flat) type_hash: u32,\r
    @location(4) is_folder: f32,\r
};\r
\r
@vertex\r
fn vs_main(in: VertexInput) -> VertexOutput {\r
    var out: VertexOutput;\r
    \r
    let is_folder = step(in.instanceSize, 0.0); // 1.0 if folder (negative size), 0.0 if file\r
    let actual_size = abs(in.instanceSize);\r
    \r
    let worldPos = in.position * actual_size + in.instancePos;\r
    out.world_position = worldPos;\r
    out.clip_position = camera.viewProj * vec4<f32>(worldPos, 1.0);\r
    out.normal = in.normal;\r
    out.view_depth = out.clip_position.w; \r
    out.type_hash = in.typeHash;\r
    out.is_folder = is_folder;\r
    \r
    return out;\r
}\r
\r
struct MBOITOutput {\r
    @location(0) moments: vec4<f32>,  // b0, b1, b2, b3\r
    @location(1) color: vec4<f32>,    // accumulated premultiplied color\r
};\r
\r
// Simple pseudo-random for iridescence\r
fn rando(h: u32) -> f32 {\r
    return f32(h % 100u) / 100.0;\r
}\r
\r
@fragment\r
fn fs_main(in: VertexOutput) -> MBOITOutput {\r
    var out: MBOITOutput;\r
    \r
    let viewDir = normalize(camera.eyePosition - in.world_position);\r
    let N = normalize(in.normal);\r
    let dt = dot(viewDir, N);\r
    \r
    var finalColor: vec3<f32>;\r
    var alpha: f32;\r
\r
    if (in.is_folder > 0.5) {\r
        // Crystal / Folder rendering (solid, distinct color)\r
        let baseColor = vec3<f32>(0.2, 0.6, 0.9) + rando(in.type_hash) * vec3<f32>(0.4, 0.2, 0.1);\r
        let highlight = pow(clamp(dt, 0.0, 1.0), 5.0);\r
        finalColor = baseColor + vec3<f32>(highlight);\r
        alpha = 0.9;\r
    } else {\r
        // Bubble / File rendering (iridescent, mostly transparent)\r
        let thickness = 400.0 + rando(in.type_hash) * 400.0; // nm\r
        let phase = dt * thickness * 0.01;\r
        \r
        let iridescence = vec3<f32>(\r
            0.5 + 0.5 * sin(phase),\r
            0.5 + 0.5 * sin(phase + 2.094),\r
            0.5 + 0.5 * sin(phase + 4.188)\r
        );\r
        \r
        let rim = pow(1.0 - clamp(dt, 0.0, 1.0), 3.0);\r
        finalColor = iridescence * rim;\r
        alpha = rim * 0.8 + 0.1; // mostly transparent except at edges\r
    }\r
    \r
    let depth = in.view_depth;\r
    \r
    let d2 = depth * depth;\r
    let d3 = d2 * depth;\r
    let momentWeights = vec4<f32>(1.0, depth, d2, d3);\r
    \r
    out.moments = momentWeights * alpha;\r
    out.color = vec4<f32>(finalColor * alpha, alpha);\r
    \r
    return out;\r
}`,J=`// oit_resolve.wgsl\r
// Solves the Hamburger moment problem over a full-screen quad to composite transparency.\r
\r
struct VertexOutput {\r
    @builtin(position) clip_position: vec4<f32>,\r
    @location(0) uv: vec2<f32>,\r
};\r
\r
@vertex\r
fn vs_main(@builtin(vertex_index) in_vertex_index: u32) -> VertexOutput {\r
    var out: VertexOutput;\r
    // Full screen triangle\r
    let x = f32((in_vertex_index << 1u) & 2u);\r
    let y = f32(in_vertex_index & 2u);\r
    out.uv = vec2<f32>(x * 0.5, y * 0.5);\r
    out.clip_position = vec4<f32>(x - 1.0, 1.0 - y, 0.0, 1.0);\r
    return out;\r
}\r
\r
@group(0) @binding(0) var momentTex: texture_2d<f32>;\r
@group(0) @binding(1) var colorTex: texture_2d<f32>;\r
\r
@fragment\r
fn fs_main(in: VertexOutput) -> @location(0) vec4<f32> {\r
    let intCoord = vec2<i32>(in.clip_position.xy);\r
    \r
    let moments = textureLoad(momentTex, intCoord, 0);\r
    let colorAcc = textureLoad(colorTex, intCoord, 0);\r
    \r
    // MBOIT Resolve math (Peters et al. 2017)\r
    // For a real implementation, you'd reconstruct the transmittance function \r
    // from the 4 moments (b0, b1, b2, b3), finding polynomial roots.\r
    // For this boilerplate, we perform a simplified naive accumulation fallback.\r
    \r
    let totalAlpha = moments.x; // b0 is total optical depth/alpha implicitly\r
    if (totalAlpha < 0.001) {\r
        discard;\r
    }\r
    \r
    // Very naive resolve: average color / alpha, and exponentiate for background\r
    // Production MBOIT requires solving a 2x2 eigenvalue problem per pixel.\r
    let avgColor = colorAcc.rgb / max(colorAcc.a, 0.0001);\r
    let finalTransmittance = exp(-totalAlpha * 2.0); // Beer-Lambert approximation\r
    \r
    return vec4<f32>(avgColor, 1.0 - finalTransmittance);\r
}`;class Q{canvas;device;context;format;momentTexture;colorTexture;depthTexture;cameraBuffer;geometryBuffer;bubblePipeline;resolvePipeline;renderBindGroup;resolveBindGroup;bvh;rotationX=.5;rotationY=.5;zoom=250;constructor(e){this.canvas=e}async init(){if(!navigator.gpu)throw new Error("WebGPU not supported on this browser.");const e=await navigator.gpu.requestAdapter();if(!e)throw new Error("No appropriate GPUAdapter found.");this.device=await e.requestDevice({requiredLimits:{maxStorageBufferBindingSize:e.limits.maxStorageBufferBindingSize,maxComputeWorkgroupStorageSize:e.limits.maxComputeWorkgroupStorageSize,maxBufferSize:e.limits.maxBufferSize}}),this.context=this.canvas.getContext("webgpu"),this.format=navigator.gpu.getPreferredCanvasFormat(),this.context.configure({device:this.device,format:this.format,alphaMode:"premultiplied"}),this.bvh=new Z(this.device),await this.createGeometryBuffer(),await this.setupTextures(),await this.setupPipelines()}async createGeometryBuffer(){const e=new Float32Array([-.5,-.5,.5,0,0,1,.5,-.5,.5,0,0,1,.5,.5,.5,0,0,1,-.5,-.5,.5,0,0,1,.5,.5,.5,0,0,1,-.5,.5,.5,0,0,1,-.5,-.5,-.5,0,0,-1,.5,.5,-.5,0,0,-1,.5,-.5,-.5,0,0,-1,-.5,-.5,-.5,0,0,-1,-.5,.5,-.5,0,0,-1,.5,.5,-.5,0,0,-1,.5,-.5,-.5,1,0,0,.5,.5,.5,1,0,0,.5,-.5,.5,1,0,0,.5,-.5,-.5,1,0,0,.5,.5,-.5,1,0,0,.5,.5,.5,1,0,0,-.5,-.5,-.5,-1,0,0,-.5,-.5,.5,-1,0,0,-.5,.5,.5,-1,0,0,-.5,-.5,-.5,-1,0,0,-.5,.5,.5,-1,0,0,-.5,.5,-.5,-1,0,0,-.5,.5,-.5,0,1,0,.5,.5,.5,0,1,0,.5,.5,-.5,0,1,0,-.5,.5,-.5,0,1,0,-.5,.5,.5,0,1,0,.5,.5,.5,0,1,0,-.5,-.5,-.5,0,-1,0,.5,-.5,-.5,0,-1,0,.5,-.5,.5,0,-1,0,-.5,-.5,-.5,0,-1,0,.5,-.5,.5,0,-1,0,-.5,-.5,.5,0,-1,0]);this.geometryBuffer=this.device.createBuffer({size:e.byteLength,usage:GPUBufferUsage.VERTEX,mappedAtCreation:!0}),new Float32Array(this.geometryBuffer.getMappedRange()).set(e),this.geometryBuffer.unmap()}async setupTextures(){const e={width:this.canvas.width,height:this.canvas.height};this.momentTexture=this.device.createTexture({size:e,format:"rgba16float",usage:GPUTextureUsage.RENDER_ATTACHMENT|GPUTextureUsage.TEXTURE_BINDING}),this.colorTexture=this.device.createTexture({size:e,format:"rgba16float",usage:GPUTextureUsage.RENDER_ATTACHMENT|GPUTextureUsage.TEXTURE_BINDING}),this.depthTexture=this.device.createTexture({size:e,format:"depth32float",usage:GPUTextureUsage.RENDER_ATTACHMENT})}async setupPipelines(){await this.bvh.initializePipelines();const e=this.device.createShaderModule({code:K});this.bubblePipeline=this.device.createRenderPipeline({layout:"auto",vertex:{module:e,entryPoint:"vs_main",buffers:[{arrayStride:24,attributes:[{shaderLocation:0,offset:0,format:"float32x3"},{shaderLocation:1,offset:12,format:"float32x3"}]},{arrayStride:20,stepMode:"instance",attributes:[{shaderLocation:2,offset:0,format:"float32x3"},{shaderLocation:3,offset:12,format:"float32"},{shaderLocation:4,offset:16,format:"uint32"}]}]},fragment:{module:e,entryPoint:"fs_main",targets:[{format:"rgba16float",blend:{color:{srcFactor:"one",dstFactor:"one",operation:"add"},alpha:{srcFactor:"one",dstFactor:"one",operation:"add"}}},{format:"rgba16float",blend:{color:{srcFactor:"one",dstFactor:"one",operation:"add"},alpha:{srcFactor:"one",dstFactor:"one",operation:"add"}}}]},primitive:{topology:"triangle-list"},depthStencil:{depthWriteEnabled:!1,depthCompare:"less-equal",format:"depth32float"}});const r=this.device.createShaderModule({code:J});this.resolvePipeline=this.device.createRenderPipeline({layout:"auto",vertex:{module:r,entryPoint:"vs_main"},fragment:{module:r,entryPoint:"fs_main",targets:[{format:this.format,blend:{color:{srcFactor:"src-alpha",dstFactor:"one-minus-src-alpha",operation:"add"},alpha:{srcFactor:"one",dstFactor:"one-minus-src-alpha",operation:"add"}}}]}})}async loadData(e){await this.bvh.uploadData(e),this.bvh.build(),this.cameraBuffer=this.device.createBuffer({size:256,usage:GPUBufferUsage.UNIFORM|GPUBufferUsage.COPY_DST}),this.updateCamera(),this.renderBindGroup=this.device.createBindGroup({layout:this.bubblePipeline.getBindGroupLayout(0),entries:[{binding:0,resource:{buffer:this.cameraBuffer}}]}),this.resolveBindGroup=this.device.createBindGroup({layout:this.resolvePipeline.getBindGroupLayout(0),entries:[{binding:0,resource:this.momentTexture.createView()},{binding:1,resource:this.colorTexture.createView()}]})}handleMouseMove(e,r){this.rotationY-=e*.01,this.rotationX-=r*.01,this.rotationX=Math.max(-Math.PI/2+.1,Math.min(Math.PI/2-.1,this.rotationX))}handleZoom(e){this.zoom=Math.max(10,Math.min(200,this.zoom+e*.05))}updateCamera(){const e=this.canvas.width/this.canvas.height,r=this.perspective(45*Math.PI/180,e,.1,5e3),a=this.zoom*Math.cos(this.rotationX)*Math.sin(this.rotationY),s=this.zoom*Math.sin(this.rotationX),n=this.zoom*Math.cos(this.rotationX)*Math.cos(this.rotationY),l=this.lookAt([a,s,n],[0,0,0],[0,1,0]),o=this.multiply(r,l);this.device.queue.writeBuffer(this.cameraBuffer,0,o),this.device.queue.writeBuffer(this.cameraBuffer,64,new Float32Array(24)),this.device.queue.writeBuffer(this.cameraBuffer,160,new Float32Array([a,s,n]))}perspective(e,r,a,s){const n=1/Math.tan(e/2),l=new Float32Array(16);return l[0]=n/r,l[5]=n,l[10]=s/(a-s),l[11]=-1,l[14]=a*s/(a-s),l}lookAt(e,r,a){const s=this.normalize(this.subtract(e,r)),n=this.normalize(this.cross(a,s)),l=this.cross(s,n),o=new Float32Array(16);return o[0]=n[0],o[4]=n[1],o[8]=n[2],o[12]=-this.dot(n,e),o[1]=l[0],o[5]=l[1],o[9]=l[2],o[13]=-this.dot(l,e),o[2]=s[0],o[6]=s[1],o[10]=s[2],o[14]=-this.dot(s,e),o[3]=0,o[7]=0,o[11]=0,o[15]=1,o}multiply(e,r){const a=new Float32Array(16);for(let s=0;s<4;s++)for(let n=0;n<4;n++)a[s*4+n]=e[s*4+0]*r[0+n]+e[s*4+1]*r[4+n]+e[s*4+2]*r[8+n]+e[s*4+3]*r[12+n];return a}subtract(e,r){return[e[0]-r[0],e[1]-r[1],e[2]-r[2]]}normalize(e){const r=Math.sqrt(e[0]*e[0]+e[1]*e[1]+e[2]*e[2]);return[e[0]/r,e[1]/r,e[2]/r]}cross(e,r){return[e[1]*r[2]-e[2]*r[1],e[2]*r[0]-e[0]*r[2],e[0]*r[1]-e[1]*r[0]]}dot(e,r){return e[0]*r[0]+e[1]*r[1]+e[2]*r[2]}render(){if(!this.bvh.getElementCount()||!this.cameraBuffer){const s=this.device.createCommandEncoder();s.beginRenderPass({colorAttachments:[{view:this.context.getCurrentTexture().createView(),loadOp:"clear",clearValue:[.945,.96,.878,1],storeOp:"store"}]}).end(),this.device.queue.submit([s.finish()]);return}this.updateCamera();const e=this.device.createCommandEncoder(),r=e.beginRenderPass({colorAttachments:[{view:this.momentTexture.createView(),loadOp:"clear",clearValue:[0,0,0,0],storeOp:"store"},{view:this.colorTexture.createView(),loadOp:"clear",clearValue:[0,0,0,0],storeOp:"store"}],depthStencilAttachment:{view:this.depthTexture.createView(),depthClearValue:1,depthLoadOp:"clear",depthStoreOp:"store"}});r.setPipeline(this.bubblePipeline),r.setBindGroup(0,this.renderBindGroup),r.setVertexBuffer(0,this.geometryBuffer),r.setVertexBuffer(1,this.bvh.getElementBuffer()),r.draw(36,this.bvh.getElementCount(),0,0),r.end();const a=e.beginRenderPass({colorAttachments:[{view:this.context.getCurrentTexture().createView(),loadOp:"clear",clearValue:[.945,.96,.878,1],storeOp:"store"}]});a.setPipeline(this.resolvePipeline),a.setBindGroup(0,this.resolveBindGroup),a.draw(6),a.end(),this.device.queue.submit([e.finish()])}destroy(){this.momentTexture&&this.momentTexture.destroy(),this.colorTexture&&this.colorTexture.destroy(),this.depthTexture&&this.depthTexture.destroy(),this.cameraBuffer&&this.cameraBuffer.destroy(),this.geometryBuffer&&this.geometryBuffer.destroy(),this.bvh&&this.bvh.destroy(),this.device&&this.device.destroy()}}const ee=()=>{const i=d.useRef(null),e=d.useRef(null),r=d.useRef(0),[a,s]=d.useState(!1),n=d.useRef({x:0,y:0}),[l,o]=d.useState(null);d.useEffect(()=>{if(!i.current)return;const u=i.current,h=new Q(u);e.current=h;let m=!1;return(async()=>{try{if(await h.init(),m)return;const p=await G();if(m)return;if(p.byteLength>4)await h.loadData(p);else{m||o("No 3D data available. Please index some files first.");return}const w=()=>{h.render(),r.current=requestAnimationFrame(w)};r.current=requestAnimationFrame(w)}catch(p){console.error("Failed to initialize or fetch WebGPU data:",p),m||o(p instanceof Error?p.message:"Unknown error loading 3D data")}})(),()=>{m=!0,r.current&&cancelAnimationFrame(r.current),e.current&&e.current.destroy()}},[]);const g=u=>{s(!0),n.current={x:u.clientX,y:u.clientY}},v=u=>{if(!a||!e.current)return;const h=u.clientX-n.current.x,m=u.clientY-n.current.y;e.current.handleMouseMove(h,m),n.current={x:u.clientX,y:u.clientY}},f=()=>s(!1),y=u=>{e.current&&e.current.handleZoom(u.deltaY)};return l?t.jsx("div",{className:"w-full h-full min-h-[400px] flex items-center justify-center bg-error/5 text-error rounded-3xl border border-error/20",children:t.jsxs("div",{className:"text-center p-6",children:[t.jsx("p",{className:"font-bold mb-2",children:"Failed to load Crystal Dreamscape"}),t.jsx("p",{className:"text-xs opacity-80",children:l})]})}):t.jsxs("div",{className:"w-full h-full min-h-[500px] relative bg-[#f1f5e0] rounded-3xl overflow-hidden border border-white/40 shadow-inner",children:[t.jsxs("div",{className:"absolute top-6 left-8 z-10 pointer-events-none",children:[t.jsxs("h2",{className:"text-2xl font-bold text-primary flex items-center gap-3",children:[t.jsx("span",{className:"w-3 h-3 bg-accent rounded-full animate-pulse shadow-[0_0_12px_rgba(142,72,234,0.6)]"}),"Crystal Dreamscape 3D"]}),t.jsx("p",{className:"text-text-secondary text-[10px] font-bold mt-2 tracking-widest uppercase opacity-60",children:"DreamScape 3D"})]}),t.jsx("canvas",{ref:i,className:"w-full h-full cursor-grab active:cursor-grabbing block",style:{minHeight:"500px",height:"100%",width:"100%"},onMouseDown:g,onMouseMove:v,onMouseUp:f,onMouseLeave:f,onWheel:y})]})},te=({allFiles:i,activeFilter:e,onFilterChange:r,initialMode:a})=>{const[s,n]=d.useState("checking");return d.useEffect(()=>{(async()=>{if(!navigator.gpu){n("unsupported");return}try{if(!await navigator.gpu.requestAdapter()){n("unsupported");return}n("supported")}catch(o){console.error("WebGPU initialization failed: ",o),n("unsupported")}})()},[]),s==="checking"?t.jsx("div",{className:"w-full h-[600px] bg-slate-900 flex items-center justify-center rounded-lg border border-slate-800",children:t.jsxs("div",{className:"flex flex-col items-center",children:[t.jsx("div",{className:"w-12 h-12 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin"}),t.jsx("p",{className:"mt-4 text-slate-400 font-mono text-sm",children:"Initializing GPU Infrastructure..."})]})}):s==="unsupported"?t.jsxs("div",{className:"w-full h-full flex flex-col",children:[t.jsx("div",{className:"bg-amber-900/30 border-l-4 border-amber-500 p-4 mb-4",children:t.jsxs("p",{className:"text-amber-200 text-sm",children:[t.jsx("span",{className:"font-bold",children:"WebGPU Not Available:"})," Your browser does not support WebGPU or it is disabled. Falling back to 2D Hardware-Accelerated Charts."]})}),t.jsx("div",{className:"flex-1 min-h-[600px]",children:t.jsx(z,{allFiles:i,activeFilter:e,onFilterChange:r,initialMode:a})})]}):t.jsx(ee,{})};function N(i){return i<1024?`${i} B`:i<1024*1024?`${(i/1024).toFixed(1)} KB`:i<1024*1024*1024?`${(i/(1024*1024)).toFixed(1)} MB`:`${(i/(1024*1024*1024)).toFixed(2)} GB`}function oe(){const{data:i,loading:e,error:r}=T(A,{cacheKey:"insights",refetchInterval:3e4}),{data:a,loading:s}=T(L,{cacheKey:"file-tree"}),[n,l]=d.useState(null),[o,g]=d.useState([]),[v,f]=d.useState([]),[y,u]=d.useState(!1),[h,m]=d.useState("3d"),b=d.useCallback(c=>{l(c)},[]);d.useEffect(()=>{if(!n){g(i?.top_files??[]),f(i?.cold_files??[]);return}let c=!1;return u(!0),S(n).then(j=>{c||(g(j.top_files??[]),f(j.cold_files??[]))}).catch(()=>{c||(g([]),f([]))}).finally(()=>{c||u(!1)}),()=>{c=!0}},[n,i]);const p=d.useMemo(()=>i?.type_breakdown?Object.keys(i.type_breakdown).length:0,[i]),w=i?N(i.total_size_bytes):"—",E=i?N(i.database_size_bytes):"—",_=i?.file_count??0;return t.jsxs("div",{className:"flex-1 overflow-y-auto p-6 space-y-6 animate-fade-in-up custom-scrollbar",children:[t.jsx("div",{className:"flex items-center justify-between",children:t.jsxs("div",{children:[t.jsxs("h1",{className:"text-2xl font-bold flex items-center gap-3",children:[t.jsx(B,{className:"w-7 h-7 text-primary"}),"Insights"]}),t.jsx("p",{className:"text-text-secondary mt-1 text-sm",children:"Analytics and visualizations of your personal data"})]})}),r&&t.jsx("div",{className:"glass-card bg-error/10 text-error text-sm",children:r}),e&&!i&&t.jsx("div",{className:"glass-card flex items-center justify-center py-16",children:t.jsx(P,{className:"w-8 h-8 text-primary animate-spin"})}),i&&t.jsxs(t.Fragment,{children:[t.jsx("div",{className:"grid grid-cols-1 md:grid-cols-5 gap-4",children:[{label:"Total Files",value:_.toLocaleString(),icon:k,color:"text-primary-light"},{label:"Indexed Files Size",value:w,icon:F,color:"text-accent"},{label:"Database Size",value:E,icon:O,color:"text-primary"},{label:"File Types",value:p.toString(),icon:H,color:"text-success"},{label:"Top Used",value:(i?.top_files?.length??0).toString(),icon:B,color:"text-warning"}].map(({label:c,value:j,icon:U,color:C})=>t.jsxs("div",{className:"glass-card flex flex-col items-center justify-center py-6 px-4",children:[t.jsx(U,{className:`w-6 h-6 ${C} mb-2`}),t.jsx("span",{className:`text-xl font-bold ${C} text-center`,children:j}),t.jsx("span",{className:"text-text-secondary text-xs mt-1 text-center uppercase tracking-wider font-semibold",children:c})]},c))}),t.jsxs("div",{className:"grid grid-cols-1 lg:grid-cols-3 gap-4 flex-1",children:[t.jsxs("div",{className:"glass-card lg:col-span-2 flex flex-col min-h-[600px] h-full overflow-hidden",children:[t.jsxs("div",{className:"flex items-center justify-between mb-4 shrink-0",children:[t.jsxs("h2",{className:"text-lg font-bold text-primary flex items-center gap-2",children:[t.jsx(F,{className:"w-5 h-5"}),"File Type Hierarchy"]}),t.jsxs("div",{className:"flex items-center bg-black/5 p-1 rounded-xl border border-black/5 shadow-inner",children:[t.jsxs("button",{onClick:()=>m("3d"),className:`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold transition-all ${h==="3d"?"bg-primary text-white shadow-lg":"text-text-secondary hover:text-text-primary"}`,children:[t.jsx(M,{className:"w-3.5 h-3.5"})," 3D CRYSTAL"]}),t.jsxs("button",{onClick:()=>m("2d"),className:`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold transition-all ${h==="2d"?"bg-primary text-white shadow-lg":"text-text-secondary hover:text-text-primary"}`,children:[t.jsx(R,{className:"w-3.5 h-3.5"})," 2D TREEMAP"]})]})]}),a?.folders&&Object.keys(a.folders).length>0?t.jsx("div",{className:"flex-1 min-h-0 flex flex-col relative",children:h==="3d"?t.jsx(te,{allFiles:a.folders,activeFilter:n,onFilterChange:b,initialMode:"type"}):t.jsx(z,{allFiles:a.folders,activeFilter:n,onFilterChange:b,initialMode:"type"})}):t.jsx("div",{className:"flex-1 flex flex-col items-center justify-center text-text-secondary text-sm bg-white/5 rounded-2xl border border-white/5",children:s?t.jsxs("div",{className:"flex flex-col items-center gap-3",children:[t.jsx(P,{className:"w-8 h-8 text-primary animate-spin"}),t.jsx("p",{children:"Loading folder structure..."})]}):t.jsxs("div",{className:"flex flex-col items-center gap-3 opacity-60",children:[t.jsx(M,{className:"w-12 h-12"}),t.jsx("p",{children:"No file hierarchy data available."})]})})]}),t.jsxs("div",{className:"glass-card space-y-6",children:[n&&t.jsxs("div",{className:"bg-primary/10 border border-primary/20 rounded-xl flex items-center justify-between p-3 shrink-0 shadow-sm animate-fade-in-up",children:[t.jsxs("div",{className:"flex items-center gap-3",children:[t.jsx(k,{className:"w-4 h-4 text-primary"}),t.jsxs("span",{className:"text-xs font-bold text-primary uppercase",children:[n," Active"]})]}),t.jsx("button",{onClick:()=>b(null),className:"text-[9px] font-black bg-primary/20 text-primary hover:bg-primary/30 px-2 py-1 rounded transition-all",children:"CLEAR"})]}),t.jsxs("div",{children:[t.jsxs("h2",{className:"text-lg font-semibold mb-3 flex items-center gap-2 text-text-primary",children:[t.jsx(W,{className:"w-5 h-5 text-warning"}),"Top Files"]}),y?t.jsx("div",{className:"flex items-center justify-center py-12",children:t.jsx(P,{className:"w-6 h-6 text-primary animate-spin"})}):o.length>0?t.jsx("div",{className:"space-y-2",children:o.slice(0,10).map(c=>t.jsxs("div",{className:"group flex items-center justify-between text-sm bg-white/5 hover:bg-white/10 rounded-xl px-4 py-3 transition-all border border-white/5",children:[t.jsx("span",{className:"truncate text-text-primary font-medium",children:c.path.split(/[\\/]/).pop()}),t.jsx("span",{className:"text-primary-light text-xs font-mono font-bold shrink-0 ml-2",children:N(c.size)})]},c.path))}):t.jsx("div",{className:"text-center py-8 opacity-40",children:t.jsx("p",{className:"text-text-secondary text-sm",children:n?`No ${n} files found`:"No files indexed yet"})})]}),!y&&v.length>0&&t.jsxs("div",{children:[t.jsxs("h2",{className:"text-lg font-semibold mb-3 flex items-center gap-2 text-text-primary",children:[t.jsx(X,{className:"w-5 h-5 text-accent"}),"Cold Files"]}),t.jsx("div",{className:"space-y-2",children:v.slice(0,8).map(c=>t.jsxs("div",{className:"group flex items-center justify-between text-sm bg-white/5 hover:bg-white/10 rounded-xl px-4 py-3 transition-all border border-white/5",children:[t.jsx("span",{className:"truncate text-text-primary font-medium",children:c.path.split(/[\\/]/).pop()}),t.jsx("span",{className:"text-accent text-xs font-bold shrink-0 ml-2",children:c.usage_count!==void 0?`${c.usage_count} hits`:N(c.size||0)})]},c.path))})]})]})]}),i.error&&t.jsxs("div",{className:"glass-card bg-warning/10 text-warning text-sm",children:["Partial data — some statistics unavailable: ",i.error]})]}),!e&&i&&_===0&&t.jsxs("div",{className:"glass-card text-center py-12",children:[t.jsx(B,{className:"w-12 h-12 text-primary/20 mx-auto mb-4"}),t.jsx("p",{className:"text-text-secondary",children:"Index some files to generate insights about your personal data."})]})]})}export{oe as InsightsPage};
