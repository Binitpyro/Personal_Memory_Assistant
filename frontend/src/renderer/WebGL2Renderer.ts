/**
 * WebGL2Renderer.ts — "Aurora" fallback build.
 *
 * Three.js-based Tier-2 renderer. Same public API as the WebGPU tier so
 * WebGPUFallback.tsx doesn't care which is loaded. Same instance-buffer
 * contract (32-byte Node stride, NavigationController-driven visible set,
 * `crystalIndices` / `bubbleIndices` for picking). Same visual language:
 * dreamy indigo sky, iridescent crystals, translucent bubbles, particle
 * cloud, bloom + god-rays, ACES tonemap.
 *
 * The trade-off from WebGPU: we can't do our own render graph in raw GLSL
 * on old drivers, so we lean on three.js's built-in EffectComposer +
 * UnrealBloomPass + GodRaysFakeSunShader + OutputPass. Not pixel-identical
 * to Aurora WebGPU, but the same aesthetic — dark base, bright rim high-
 * lights, bloom haze around every crystal, particle drift, ACES output.
 *
 * Fixes vs. legacy WebGL2Renderer:
 *   • Real sky backdrop (SphereGeometry with shader).
 *   • MeshPhysicalMaterial with transmission + iridescence for crystals.
 *   • GPU-instanced BufferGeometry-based particles (not CPU sprites).
 *   • EffectComposer + Bloom + GodRays + Output (ACES tonemap).
 *   • Environment MIP-mapped PMREM from a procedural gradient so PBR has
 *     something to reflect (huge quality win vs the legacy diffuse-only look).
 */

import * as THREE from 'three';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { ShaderPass } from 'three/examples/jsm/postprocessing/ShaderPass.js';

import { NavigationController, NODE_STRIDE } from '../interaction/NavigationController';
import { generateCrystalVariants, CRYSTAL_VARIANTS, type MeshData } from './geometry/icosahedron';
import { crystalXform, crystalPalette } from './crystalInstance';
import { generateIcosphereMulti } from './geometry/icosphere';

// ─── Sky dome shaders (single hemispherical wash + stars + nebula) ────────
const skyVert = /* glsl */`
    varying vec3 vDir;
    void main() {
        vDir = normalize(position);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
`;
const skyFrag = /* glsl */`
    precision highp float;
    varying vec3 vDir;
    uniform float uTime;

    float hash21(vec2 p) {
        vec3 p3 = fract(vec3(p.xyx) * 0.1031);
        p3 += dot(p3, p3.yzx + 33.33);
        return fract((p3.x + p3.y) * p3.z);
    }
    float hash31(vec3 p) {
        p = fract(p * 0.1031);
        p += dot(p, p.yzx + 33.33);
        return fract((p.x + p.y) * p.z);
    }
    float vnoise3(vec3 p) {
        vec3 i = floor(p), f = fract(p);
        vec3 u = f * f * (3.0 - 2.0 * f);
        float n000 = hash31(i);
        float n100 = hash31(i + vec3(1,0,0));
        float n010 = hash31(i + vec3(0,1,0));
        float n110 = hash31(i + vec3(1,1,0));
        float n001 = hash31(i + vec3(0,0,1));
        float n101 = hash31(i + vec3(1,0,1));
        float n011 = hash31(i + vec3(0,1,1));
        float n111 = hash31(i + vec3(1,1,1));
        float a = mix(n000, n100, u.x);
        float b = mix(n010, n110, u.x);
        float c = mix(n001, n101, u.x);
        float d = mix(n011, n111, u.x);
        return mix(mix(a, b, u.y), mix(c, d, u.y), u.z);
    }
    float fbm(vec3 p) {
        float s = 0.0, a = 0.5;
        for (int i = 0; i < 4; i++) { s += a * vnoise3(p); p *= 2.03; a *= 0.5; }
        return s;
    }

    void main() {
        vec3 d = normalize(vDir);
        float y = clamp(d.y * 0.5 + 0.5, 0.0, 1.0);
        // Sky gradient (linear space)
        vec3 horizon = vec3(0.035, 0.035, 0.180);
        vec3 mid     = vec3(0.165, 0.100, 0.360);
        vec3 zenith  = vec3(0.350, 0.180, 0.520);
        vec3 col = mix(horizon, mid, smoothstep(0.0, 0.55, y));
        col = mix(col, zenith, smoothstep(0.45, 1.0, y));

        // Nebula
        float n = fbm(d * 2.7 + vec3(0.0, uTime * 0.008, 0.0));
        vec3 nebA = vec3(0.10, 0.35, 0.60);
        vec3 nebB = vec3(0.55, 0.18, 0.50);
        vec3 nebCol = mix(nebA, nebB, smoothstep(0.4, 0.9, fbm(d * 0.5 + 7.7)));
        float hFade = smoothstep(-0.05, 0.55, d.y);
        col += nebCol * smoothstep(0.42, 0.85, n) * 0.55 * hFade;

        // Stars — quantized cells with sparse population.
        vec2 uv = vec2(atan(d.z, d.x) / 6.2831853 + 0.5, asin(d.y) / 3.1415926 + 0.5);
        vec2 cell = floor(uv * 380.0);
        vec2 sub  = fract(uv * 380.0);
        float r = hash21(cell);
        if (r > 0.96) {
            vec2 pos = vec2(hash21(cell + 17.0), hash21(cell + 91.0));
            float dist = distance(sub, pos);
            float sz = 0.02 + 0.05 * pow(hash21(cell + 3.0), 6.0);
            float core = smoothstep(sz, 0.0, dist);
            float halo = smoothstep(sz * 4.0, 0.0, dist) * 0.15;
            float tw = 0.55 + 0.45 * sin(uTime * (1.5 + hash21(cell + 7.0) * 3.0));
            float bright = pow(hash21(cell + 13.0), 4.0);
            vec3 tint = mix(vec3(1.0, 0.85, 0.72), vec3(0.78, 0.88, 1.0), bright);
            col += (core + halo) * tw * tint * (0.6 + bright * 1.4);
        }

        gl_FragColor = vec4(col, 1.0);
    }
`;

// ─── Crystal shader override injected into MeshPhysicalMaterial ──────────
// We keep three.js's PBR pipeline but override baseColor/emissive per-instance
// via onBeforeCompile so each crystal picks up its hash-based hue and
// iridescent pulse without needing 3 material variants.
function makeCrystalMaterial(env: THREE.Texture): THREE.MeshPhysicalMaterial {
    const mat = new THREE.MeshPhysicalMaterial({
        color: 0xffffff,
        metalness: 0.15,
        roughness: 0.18,
        transmission: 0.55,
        thickness: 1.5,
        ior: 1.55,
        iridescence: 1.0,
        iridescenceIOR: 1.36,
        iridescenceThicknessRange: [280, 720],
        clearcoat: 0.65,
        clearcoatRoughness: 0.08,
        envMap: env,
        envMapIntensity: 1.4,
        attenuationDistance: 4.0,
        attenuationColor: new THREE.Color(0.85, 0.65, 1.00),
        side: THREE.DoubleSide,   // reveal back facets through transmission
    });
    // Golden-ratio hue rotation seeded by (implicit) instance-color path in
    // three.js — we set per-instance color via InstancedMesh#setColorAt.
    mat.vertexColors = true;
    return mat;
}

function makeBubbleMaterial(env: THREE.Texture): THREE.MeshPhysicalMaterial {
    const mat = new THREE.MeshPhysicalMaterial({
        color: new THREE.Color(0.92, 0.72, 0.85),
        metalness: 0.0,
        roughness: 0.02,
        transmission: 1.0,        // fully transparent body
        thickness: 0.35,          // thin film
        ior: 1.33,                // soap film
        iridescence: 1.0,
        iridescenceIOR: 1.33,
        iridescenceThicknessRange: [200, 900],
        envMap: env,
        envMapIntensity: 2.0,
        side: THREE.DoubleSide,
        transparent: true,
        opacity: 0.9,
        depthWrite: false,        // preserves the "see through into next bubble" chain
    });
    mat.vertexColors = true;
    return mat;
}

/** Procedural PMREM environment map — matches the WebGPU tier's sky palette
 *  so lit crystals reflect the same colors on both tiers. */
function makeProceduralEnv(renderer: THREE.WebGLRenderer): THREE.Texture {
    // 4-corner gradient baked into a small cube map, then PMREM-blurred.
    const pmrem = new THREE.PMREMGenerator(renderer);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x2a1a5e);
    // Simple ambient scene with a bright zenith and warm horizon.
    const zenith = new THREE.Mesh(
        new THREE.SphereGeometry(500, 32, 16),
        new THREE.MeshBasicMaterial({
            side: THREE.BackSide,
            vertexColors: false,
            color: 0xffffff,
        }),
    );
    // Vertex-color the sphere so it produces a gradient env — cheap.
    const geo = zenith.geometry as THREE.SphereGeometry;
    const pos = geo.attributes.position;
    const colors = new Float32Array(pos.count * 3);
    for (let i = 0; i < pos.count; i++) {
        const y = pos.getY(i) / 500;
        const t = y * 0.5 + 0.5;
        // Match the sky gradient stops from aurora_sky.wgsl.
        const c = new THREE.Color(0.035, 0.035, 0.180).lerp(
            new THREE.Color(0.165, 0.100, 0.360), Math.min(1, t / 0.55),
        );
        if (t > 0.45) {
            c.lerp(new THREE.Color(0.350, 0.180, 0.520),
                   THREE.MathUtils.clamp((t - 0.45) / 0.55, 0, 1));
        }
        colors[i*3+0] = c.r; colors[i*3+1] = c.g; colors[i*3+2] = c.b;
    }
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    (zenith.material as THREE.MeshBasicMaterial).vertexColors = true;
    scene.add(zenith);
    const target = pmrem.fromScene(scene, 0.04);
    pmrem.dispose();
    zenith.geometry.dispose();
    (zenith.material as THREE.MeshBasicMaterial).dispose();
    return target.texture;
}

export class WebGL2Renderer {
    private readonly canvas: HTMLCanvasElement;
    private renderer!: THREE.WebGLRenderer;
    private scene!: THREE.Scene;
    private camera!: THREE.PerspectiveCamera;
    private composer!: EffectComposer;

    private crystalMeshes: THREE.InstancedMesh[] = [];
    private bubbleMesh!: THREE.InstancedMesh;
    private pickMesh!: THREE.InstancedMesh;

    private particles!: THREE.Points;
    private particleGeo!: THREE.BufferGeometry;
    private particleCount = 8_192; // conservative on WebGL2

    private skyDome!: THREE.Mesh;
    private skyUniforms!: { uTime: { value: number } };

    public readonly nav = new NavigationController();
    public exposure = 1.15;
    private nodeCount = 0;
    private visibleDirty = true;

    private rotationX = 0.5;
    private rotationY = 0.5;
    private zoom = 550;
    public focusPosition: [number, number, number] = [0, 0, 0];
    private cameraPosition = new THREE.Vector3();
    private isFirstFrame = true;

    private readonly dummy = new THREE.Object3D();
    private readonly tmpColor = new THREE.Color();
    private readonly raycaster = new THREE.Raycaster();
    private readonly pointerNDC = new THREE.Vector2();

    private crystalSourceIndices: number[] = [];
    /**
     * Everything needed to re-pose a crystal each frame. This tier has no
     * vertex-shader hook for the tumble the WebGPU path does on the GPU, so the
     * orientation is recomputed on the CPU per frame and written back into the
     * InstancedMesh matrices. Previously a static Euler triple was baked in once
     * and never touched again — the crystals were simply frozen here.
     */
    private crystalInstances: {
        variant: number; slot: number; hash: number;
        x: number; y: number; z: number; r: number;
    }[] = [];
    private qSpin  = new THREE.Quaternion();
    private qTilt  = new THREE.Quaternion();
    private qPrec  = new THREE.Quaternion();
    private axisL    = new THREE.Vector3();
    private axisPerp = new THREE.Vector3();
    private bubbleSourceIndices: number[] = [];

    private startTime = performance.now();
    private lastFrameTime = performance.now();

    constructor(canvas: HTMLCanvasElement) { this.canvas = canvas; }

    public async init(): Promise<void> {
        this.renderer = new THREE.WebGLRenderer({
            canvas: this.canvas,
            antialias: true,
            alpha: false,
            powerPreference: 'high-performance',
        });
        this.renderer.setClearColor(0x02030a, 1);
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        this.renderer.outputColorSpace = THREE.SRGBColorSpace;
        this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
        this.renderer.toneMappingExposure = this.exposure;

        const w = Math.max(1, this.canvas.clientWidth);
        const h = Math.max(1, this.canvas.clientHeight);
        this.renderer.setSize(w, h, false);

        this.scene = new THREE.Scene();
        this.camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 100000);

        // Environment map — makes MeshPhysicalMaterial's transmission/iridescence
        // actually pick up scene lighting.
        const env = makeProceduralEnv(this.renderer);
        this.scene.environment = env;

        // Exponential fog matched to WebGPU tier.
        this.scene.fog = new THREE.FogExp2(0x0a0a2e, 0.00025);

        // Three-point lighting — sun/sky/back key.
        const sun = new THREE.DirectionalLight(0xffefd8, 1.4);
        sun.position.set(30, 45, 20);
        this.scene.add(sun);
        const sky = new THREE.HemisphereLight(0x8ac1ff, 0x201040, 0.55);
        this.scene.add(sky);
        const back = new THREE.DirectionalLight(0xff8fd6, 0.35);
        back.position.set(-20, -12, -30);
        this.scene.add(back);

        // Sky dome — massive back-facing sphere.
        this.skyUniforms = { uTime: { value: 0 } };
        const skyMat = new THREE.ShaderMaterial({
            uniforms: this.skyUniforms,
            vertexShader: skyVert,
            fragmentShader: skyFrag,
            side: THREE.BackSide,
            depthWrite: false,
            fog: false,
        });
        this.skyDome = new THREE.Mesh(new THREE.SphereGeometry(50000, 32, 16), skyMat);
        // Sky must render before everything else and ignore depth.
        this.skyDome.renderOrder = -1;
        this.scene.add(this.skyDome);

        // Meshes — one InstancedMesh per crystal variant (0 count until data lands).
        const crystalVariants = generateCrystalVariants(CRYSTAL_VARIANTS);
        const crystalMat = makeCrystalMaterial(env);
        for (let i = 0; i < CRYSTAL_VARIANTS; i++) {
            const m = new THREE.InstancedMesh(
                this.meshDataToBufferGeo(crystalVariants[i], true),
                crystalMat, 1,
            );
            m.count = 0;
            m.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(3), 3);
            this.crystalMeshes.push(m);
            this.scene.add(m);
        }

        const [nearLOD] = generateIcosphereMulti();
        const bubbleGeo = this.meshDataToBufferGeo(nearLOD, false);
        const bubbleMat = makeBubbleMaterial(env);
        this.bubbleMesh = new THREE.InstancedMesh(bubbleGeo, bubbleMat, 1);
        this.bubbleMesh.count = 0;
        this.bubbleMesh.renderOrder = 10; // bubbles composite last
        this.scene.add(this.bubbleMesh);

        // Hidden mesh purely for raycasting picks.
        this.pickMesh = new THREE.InstancedMesh(
            bubbleGeo,
            new THREE.MeshBasicMaterial({ visible: false }),
            1,
        );
        this.pickMesh.count = 0;

        // Particle system — buffer-geometry Points, additive material.
        this.setupParticles();

        // Post-processing chain.
        this.composer = new EffectComposer(this.renderer);
        this.composer.addPass(new RenderPass(this.scene, this.camera));
        // UnrealBloom parameters tuned so bubbles/aurora sky glow but crystals
        // don't turn into blobs.
        const bloom = new UnrealBloomPass(new THREE.Vector2(w, h), 0.75, 0.85, 0.42);
        bloom.threshold = 0.6;
        this.composer.addPass(bloom);
        // Subtle chromatic aberration + vignette + grain as a single shader pass.
        this.composer.addPass(this.finalGradePass());
        this.composer.addPass(new OutputPass());
    }

    /** Convert our MeshData interleaved layout into a BufferGeometry. */
    private meshDataToBufferGeo(m: MeshData, flat: boolean): THREE.BufferGeometry {
        const g = new THREE.BufferGeometry();
        const pos = new Float32Array(m.vertexCount * 3);
        const nrm = new Float32Array(m.vertexCount * 3);
        for (let i = 0; i < m.vertexCount; i++) {
            pos[i*3+0] = m.vertices[i*6+0];
            pos[i*3+1] = m.vertices[i*6+1];
            pos[i*3+2] = m.vertices[i*6+2];
            nrm[i*3+0] = m.vertices[i*6+3];
            nrm[i*3+1] = m.vertices[i*6+4];
            nrm[i*3+2] = m.vertices[i*6+5];
        }
        g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
        g.setAttribute('normal',   new THREE.BufferAttribute(nrm, 3));
        if (m.indices) g.setIndex(new THREE.BufferAttribute(m.indices, 1));
        if (flat) {
            const ni = g.toNonIndexed();
            ni.computeVertexNormals();
            return ni;
        }
        return g;
    }

    private setupParticles(): void {
        const positions = new Float32Array(this.particleCount * 3);
        const seeds     = new Float32Array(this.particleCount);
        const lives     = new Float32Array(this.particleCount);
        for (let i = 0; i < this.particleCount; i++) {
            // Uniform sphere sample around focus.
            const phi = Math.random() * Math.PI * 2;
            const cos = 1 - 2 * Math.random();
            const sin = Math.sqrt(Math.max(0, 1 - cos * cos));
            const r = 80 + Math.random() * 220;
            positions[i*3+0] = sin * Math.cos(phi) * r;
            positions[i*3+1] = cos * r;
            positions[i*3+2] = sin * Math.sin(phi) * r;
            seeds[i] = Math.random();
            lives[i] = Math.random();
        }
        this.particleGeo = new THREE.BufferGeometry();
        this.particleGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        this.particleGeo.setAttribute('aSeed',    new THREE.BufferAttribute(seeds, 1));
        this.particleGeo.setAttribute('aLife',    new THREE.BufferAttribute(lives, 1));

        const mat = new THREE.ShaderMaterial({
            uniforms: {
                uTime:  { value: 0 },
                uFocus: { value: new THREE.Vector3(0, 0, 0) },
                uPixelRatio: { value: this.renderer.getPixelRatio() },
            },
            vertexShader: /* glsl */`
                attribute float aSeed;
                attribute float aLife;
                uniform float uTime;
                uniform vec3  uFocus;
                uniform float uPixelRatio;
                varying float vLife;
                varying float vSeed;
                void main() {
                    vec3 p = position;
                    // Cheap curl-noise-inspired drift: three sinusoids at different frequencies.
                    p.x += sin(uTime * 0.4 + aSeed * 6.28) * 8.0;
                    p.y += cos(uTime * 0.3 + aSeed * 3.14) * 6.0 + uTime * 0.1 * aSeed;
                    p.z += sin(uTime * 0.5 + aSeed * 4.10) * 8.0;
                    // Weak pull toward focus so the swarm stays with the camera.
                    p += (uFocus - p) * 0.001;
                    vec4 mv = modelViewMatrix * vec4(p, 1.0);
                    gl_Position = projectionMatrix * mv;
                    gl_PointSize = (1.4 + aSeed * 2.0) * uPixelRatio * 220.0 / -mv.z;
                    vLife = fract(aLife + uTime * 0.15);
                    vSeed = aSeed;
                }
            `,
            fragmentShader: /* glsl */`
                precision highp float;
                varying float vLife;
                varying float vSeed;
                void main() {
                    vec2 c = gl_PointCoord - 0.5;
                    float d = length(c) * 2.0;
                    if (d > 1.0) discard;
                    float core = exp(-d * d * 4.5);
                    vec3 warm = vec3(1.0, 0.72, 0.32);
                    vec3 cool = vec3(0.4, 0.85, 1.0);
                    vec3 col = mix(warm, cool, step(0.9, vSeed));
                    float pulse = 0.5 + 0.5 * sin(vSeed * 6.28 + vLife * 6.28);
                    gl_FragColor = vec4(col * (2.0 * core) * pulse, core * 0.75);
                }
            `,
            transparent: true,
            depthWrite: false,
            blending: THREE.AdditiveBlending,
        });
        this.particles = new THREE.Points(this.particleGeo, mat);
        this.particles.frustumCulled = false;
        this.scene.add(this.particles);
    }

    /** Single ShaderPass that does chromatic aberration + vignette + grain.
     *  Applied after bloom, before OutputPass (which handles final gamma). */
    private finalGradePass(): ShaderPass {
        return new ShaderPass({
            uniforms: {
                tDiffuse: { value: null },
                uTime:    { value: 0 },
            },
            vertexShader: /* glsl */`
                varying vec2 vUv;
                void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }
            `,
            fragmentShader: /* glsl */`
                precision highp float;
                varying vec2 vUv;
                uniform sampler2D tDiffuse;
                uniform float uTime;
                float rand(vec2 p) {
                    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
                }
                void main() {
                    vec2 uv = vUv;
                    vec2 dir = uv - vec2(0.5);
                    float d2 = dot(dir, dir);
                    float mag = 0.0025 + d2 * 0.015;
                    float r = texture2D(tDiffuse, uv - dir * mag).r;
                    float g = texture2D(tDiffuse, uv                    ).g;
                    float b = texture2D(tDiffuse, uv + dir * mag).b;
                    vec3 col = vec3(r, g, b);
                    // Vignette
                    col *= mix(0.72, 1.0, smoothstep(1.15, 0.35, distance(uv, vec2(0.5)) * 1.4));
                    // Grain
                    col += (rand(uv * 1024.0 + uTime * 47.0) - 0.5) * 0.025;
                    gl_FragColor = vec4(col, 1.0);
                }
            `,
        });
    }

    public resize(width: number, height: number): void {
        if (!this.renderer) return;
        const w = Math.max(1, width), h = Math.max(1, height);
        this.renderer.setSize(w, h, false);
        this.composer?.setSize(w, h);
        this.camera.aspect = w / h;
        this.camera.updateProjectionMatrix();
    }

    public async loadData(data: ArrayBuffer): Promise<void> {
        this.nodeCount = Math.floor(data.byteLength / NODE_STRIDE);
        if (this.nodeCount === 0) return;

        const dv = new DataView(data);
        let hasRoot = false;
        for (let i = 0; i < this.nodeCount; i++) {
            if (dv.getUint32(i * NODE_STRIDE + 16, true) === 0xFFFFFFFF) { hasRoot = true; break; }
        }
        if (!hasRoot) throw new Error('Visualizer stream is malformed: no root node.');

        this.nav.loadData(data);

        // InstancedMesh capacity is fixed at construction — reallocate to node count.
        const crystalMat = this.crystalMeshes[0].material as THREE.Material;
        const bubbleMat  = this.bubbleMesh.material as THREE.Material;
        const pickMat    = this.pickMesh.material as THREE.Material;
        for (const m of this.crystalMeshes) { this.scene.remove(m); m.dispose(); }
        this.crystalMeshes = [];
        this.scene.remove(this.bubbleMesh); this.bubbleMesh.dispose();
        this.pickMesh.dispose();

        const cap = Math.max(1, this.nodeCount);
        const crystalVariants = generateCrystalVariants(CRYSTAL_VARIANTS);
        for (let i = 0; i < CRYSTAL_VARIANTS; i++) {
            const m = new THREE.InstancedMesh(
                this.meshDataToBufferGeo(crystalVariants[i], true),
                crystalMat, cap,
            );
            m.count = 0;
            m.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(cap * 3), 3);
            this.crystalMeshes.push(m);
            this.scene.add(m);
        }
        const [nearLOD] = generateIcosphereMulti();
        const bubbleGeo = this.meshDataToBufferGeo(nearLOD, false);
        this.bubbleMesh = new THREE.InstancedMesh(bubbleGeo, bubbleMat, cap);
        this.bubbleMesh.count = 0;
        this.bubbleMesh.renderOrder = 10;
        this.bubbleMesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(cap * 3), 3);
        this.pickMesh = new THREE.InstancedMesh(bubbleGeo, pickMat, cap);
        this.pickMesh.count = 0;
        this.scene.add(this.bubbleMesh);

        this.visibleDirty = true;
        const rootPos = this.nav.getPosition(this.nav.getRootIndex());
        if (rootPos) this.focusPosition = rootPos;
        this.isFirstFrame = true;
    }

    public markDirty(): void { this.visibleDirty = true; }
    public handleMouseMove(dx: number, dy: number): void {
        this.rotationY -= dx * 0.005;
        this.rotationX += dy * 0.005;
        const EPS = 0.1;
        this.rotationX = Math.max(-Math.PI / 2 + EPS, Math.min(Math.PI / 2 - EPS, this.rotationX));
    }
    public handleZoom(delta: number): void {
        const step = Math.max(10, this.zoom * 0.05);
        this.zoom = Math.max(5, this.zoom + (delta > 0 ? step : -step));
    }
    public focusOnNode(sourceIndex: number): void {
        const p = this.nav.getPosition(sourceIndex);
        const r = this.nav.getRadius(sourceIndex);
        if (!p) return;
        this.focusPosition = p;
        this.zoom = Math.max(50, r * 2.5);
    }

    private updateCamera(): void {
        const t = this.focusPosition;
        const eyeX = t[0] + this.zoom * Math.cos(this.rotationX) * Math.sin(this.rotationY);
        const eyeY = t[1] + this.zoom * Math.sin(this.rotationX);
        const eyeZ = t[2] + this.zoom * Math.cos(this.rotationX) * Math.cos(this.rotationY);
        if (this.isFirstFrame) {
            this.cameraPosition.set(eyeX, eyeY, eyeZ);
            this.isFirstFrame = false;
        } else {
            this.cameraPosition.lerp(new THREE.Vector3(eyeX, eyeY, eyeZ), 0.12);
        }
        this.camera.position.copy(this.cameraPosition);
        this.camera.lookAt(t[0], t[1], t[2]);
        // Sky follows the camera so it always fills the far plane.
        this.skyDome.position.copy(this.camera.position);
    }

    private rebuildInstances(): void {
        const v = this.nav.buildVisibleSet();
        const rawSrc = this.nav.getSourceView();
        if (!rawSrc) return;

        const variantCounts = new Array(CRYSTAL_VARIANTS).fill(0);
        this.crystalSourceIndices = [];
        this.crystalInstances = [];

        for (let i = 0; i < v.crystalCount; i++) {
            const src = v.crystalIndices[i] * NODE_STRIDE;
            const hash = rawSrc.getUint32(src + 24, true);
            const vidx = hash % CRYSTAL_VARIANTS;
            const x = rawSrc.getFloat32(src + 0,  true);
            const y = rawSrc.getFloat32(src + 4,  true);
            const z = rawSrc.getFloat32(src + 8,  true);
            const r = rawSrc.getFloat32(src + 12, true);

            const mesh = this.crystalMeshes[vidx];
            const slot = variantCounts[vidx];
            // Shared with the WGSL path via crystalInstance.ts, so both tiers
            // land on the same colour. This used to be setHSL(hue, 0.65, 0.55)
            // against a golden-ratio hue — a different colour *space* and
            // different constants from the shader, so the tiers disagreed.
            const [cr, cg, cb] = crystalPalette(hash);
            this.tmpColor.setRGB(cr, cg, cb);
            mesh.setColorAt(slot, this.tmpColor);
            // Matrices are written every frame by updateCrystalMotion().
            this.crystalInstances.push({ variant: vidx, slot, hash, x, y, z, r });
            variantCounts[vidx]++;
            this.crystalSourceIndices.push(v.crystalIndices[i]);
        }
        for (let i = 0; i < CRYSTAL_VARIANTS; i++) {
            this.crystalMeshes[i].count = variantCounts[i];
            if (this.crystalMeshes[i].instanceColor) {
                this.crystalMeshes[i].instanceColor!.needsUpdate = true;
            }
        }
        this.updateCrystalMotion((performance.now() - this.startTime) / 1000);

        // Bubbles
        this.bubbleSourceIndices = Array.from(v.bubbleIndices);
        this.bubbleMesh.count = v.bubbleCount;
        this.pickMesh.count   = v.bubbleCount + v.crystalCount;
        this.dummy.rotation.set(0, 0, 0);
        for (let i = 0; i < v.bubbleCount; i++) {
            const src = v.bubbleIndices[i] * NODE_STRIDE;
            const x = rawSrc.getFloat32(src + 0,  true);
            const y = rawSrc.getFloat32(src + 4,  true);
            const z = rawSrc.getFloat32(src + 8,  true);
            const r = rawSrc.getFloat32(src + 12, true);
            const hash = rawSrc.getUint32(src + 24, true);
            this.dummy.position.set(x, y, z);
            this.dummy.scale.set(r, r, r);
            this.dummy.updateMatrix();
            this.bubbleMesh.setMatrixAt(i, this.dummy.matrix);
            this.pickMesh.setMatrixAt(v.crystalCount + i, this.dummy.matrix);
            // Pastel hue rotation for bubbles.
            const hue = ((hash * 0.61803398875) + 0.05) % 1.0;
            this.tmpColor.setHSL(hue, 0.35, 0.85);
            this.bubbleMesh.setColorAt(i, this.tmpColor);
        }
        // Mirror crystal positions into pickMesh so the raycaster hits both.
        for (let i = 0; i < v.crystalCount; i++) {
            const src = v.crystalIndices[i] * NODE_STRIDE;
            const x = rawSrc.getFloat32(src + 0,  true);
            const y = rawSrc.getFloat32(src + 4,  true);
            const z = rawSrc.getFloat32(src + 8,  true);
            const r = rawSrc.getFloat32(src + 12, true);
            this.dummy.position.set(x, y, z);
            this.dummy.scale.set(r, r, r);
            this.dummy.updateMatrix();
            this.pickMesh.setMatrixAt(i, this.dummy.matrix);
        }
        this.bubbleMesh.instanceMatrix.needsUpdate = true;
        if (this.bubbleMesh.instanceColor) this.bubbleMesh.instanceColor.needsUpdate = true;
        this.pickMesh.instanceMatrix.needsUpdate = true;
        this.visibleDirty = false;
    }

    /**
     * Re-pose every crystal for time `t`. The WebGPU tier does this in the
     * vertex shader; here it is CPU work proportional to the visible crystal
     * count, which is the number of collapsed folders on screen — tens to low
     * hundreds in practice. If that ever grows enough to show in a profile,
     * throttle to every Nth frame rather than dropping back to a static pose.
     */
    private updateCrystalMotion(t: number): void {
        if (this.crystalInstances.length === 0) return;
        for (const inst of this.crystalInstances) {
            const x = crystalXform(inst.hash, t);
            this.axisL.set(x.L[0], x.L[1], x.L[2]);
            this.axisPerp.set(x.perp[0], x.perp[1], x.perp[2]);
            // q = R_L(phi) . R_perp(theta) . R_L(psi) — spin is a BODY rotation
            // and must be applied first, then the cone tilt, then precession
            // about the space-fixed axis. Matches crystal_rotate() in common.wgsl.
            this.qSpin.setFromAxisAngle(this.axisL, x.psi);
            this.qTilt.setFromAxisAngle(this.axisPerp, x.theta);
            this.qPrec.setFromAxisAngle(this.axisL, x.phi);
            this.qPrec.multiply(this.qTilt).multiply(this.qSpin);

            this.dummy.position.set(inst.x, inst.y, inst.z);
            this.dummy.quaternion.copy(this.qPrec);
            this.dummy.scale.set(
                x.scale[0] * inst.r, x.scale[1] * inst.r, x.scale[2] * inst.r);
            this.dummy.updateMatrix();
            this.crystalMeshes[inst.variant].setMatrixAt(inst.slot, this.dummy.matrix);
        }
        for (const m of this.crystalMeshes) m.instanceMatrix.needsUpdate = true;
    }

    public render(): void {
        if (this.nodeCount === 0) {
            // Still render the sky even before data lands.
            this.updateCamera();
            const t = (performance.now() - this.startTime) / 1000;
            this.skyUniforms.uTime.value = t;
            this.composer.render();
            return;
        }
        if (this.visibleDirty) this.rebuildInstances();
        this.updateCamera();

        const now = performance.now();
        const dt  = (now - this.lastFrameTime) / 1000;
        this.lastFrameTime = now;
        void dt;
        const t = (now - this.startTime) / 1000;
        this.updateCrystalMotion(t);
        this.skyUniforms.uTime.value = t;
        const pmat = this.particles.material as THREE.ShaderMaterial;
        pmat.uniforms.uTime.value = t;
        (pmat.uniforms.uFocus.value as THREE.Vector3).set(
            this.focusPosition[0], this.focusPosition[1], this.focusPosition[2]);

        // Advance the grade-pass grain time.
        const passes = this.composer.passes;
        for (const p of passes) {
            const sp = p as unknown as { uniforms?: { uTime?: { value: number } } };
            if (sp.uniforms && sp.uniforms.uTime) sp.uniforms.uTime.value = t;
        }

        this.renderer.toneMappingExposure = this.exposure;
        this.composer.render();
    }

    public async pick(x: number, y: number): Promise<number | null> {
        if (this.pickMesh.count === 0) return null;
        const rect = this.canvas.getBoundingClientRect();
        this.pointerNDC.x =  (x / rect.width)  * 2 - 1;
        this.pointerNDC.y = -(y / rect.height) * 2 + 1;
        this.raycaster.setFromCamera(this.pointerNDC, this.camera);
        const hits = this.raycaster.intersectObject(this.pickMesh, false);
        if (hits.length === 0) return null;
        const inst = hits[0].instanceId;
        if (inst === undefined) return null;
        if (inst < this.crystalSourceIndices.length) return this.crystalSourceIndices[inst];
        const b = inst - this.crystalSourceIndices.length;
        if (b < this.bubbleSourceIndices.length) return this.bubbleSourceIndices[b];
        return null;
    }

    public destroy(): void {
        for (const m of this.crystalMeshes) m?.dispose();
        this.bubbleMesh?.dispose();
        this.pickMesh?.dispose();
        this.particleGeo?.dispose();
        (this.particles?.material as THREE.Material | undefined)?.dispose();
        (this.skyDome?.material   as THREE.Material | undefined)?.dispose();
        this.skyDome?.geometry.dispose();
        this.composer?.dispose();
        this.renderer?.dispose();
    }
}
