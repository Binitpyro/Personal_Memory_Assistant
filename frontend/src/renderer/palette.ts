/**
 * The vitrine grade.
 *
 * The renderer held 93 hardcoded colour literals across WebGPURenderer.ts,
 * WebGL2Renderer.ts and 12 .wgsl files, and read no CSS variables at all — so a
 * token change could not reach the 3D view and the shell drifted away from it.
 * This module is the single source both tiers now read.
 *
 * The grade itself moved from a violet aurora night to a lamp-lit specimen
 * case: ink ground, warm brass key, cool daylight fill through glass, brass
 * rim. No violet sky. Bloom is pulled well down — glow was the register the
 * whole redesign is moving away from.
 *
 * SCOPE GUARD: palette, light colours and bloom intensity only. No lighting
 * maths, no OIT, no tier selection.
 *
 * Values are display-referred and divided by 255, matching what the shaders
 * already assumed — `common.wgsl`'s sky mid is vec3(0.165,0.100,0.360) and
 * `WebGL2Renderer.ts` independently wrote the same colour as 0x2a1a5e.
 */

export type RGB = readonly [number, number, number];

const rgb = (hex: number): RGB => [
  ((hex >> 16) & 0xff) / 255,
  ((hex >> 8) & 0xff) / 255,
  (hex & 0xff) / 255,
];

export const VITRINE = {
  /** Case interior, seen past the specimens. */
  skyHorizon: rgb(0x0a0806),
  skyMid: rgb(0x1c1815),
  /** Warm lamp glow at the top of the case, standing in for the old zenith. */
  skyZenith: rgb(0x3a2e20),

  /** Deepest ground — clear colour and fog. */
  ground: rgb(0x02030a),
  fog: rgb(0x0a0806),
  /** Flat scene background for the WebGL2 tier. */
  background: rgb(0x1c1815),

  /** Three-point rig. Warm key from above, cool daylight fill, brass rim. */
  keyLight: rgb(0xffefd8),
  fillLight: rgb(0x8ea8c4),
  rimLight: rgb(0xd9a866),
  /** Hemisphere ground bounce (WebGL2 tier). */
  groundBounce: rgb(0x2a2018),

  /** Ribbon wash in the upper case — brass and cool steel, not blue/magenta. */
  washWarm: rgb(0x735c38),
  washCool: rgb(0x4d5766),
  /** Below the horizon line. */
  washBelow: rgb(0x050403),

  /** Raking light through the glass. */
  godrayTint: rgb(0xffc78c),

  /** Motes. */
  moteWarm: rgb(0xffb852),
  moteCool: rgb(0x9eb3c7),

  /** Bubble/specimen body tints. */
  bubbleBase: rgb(0xdbc7a8),
  bubbleInner: rgb(0xffdbb8),
  crystalAttenuation: rgb(0xd9c2a0),
  crystalShade: rgb(0xbfae8c),
  crystalEdge: rgb(0xf2e0c0),

  /** Bloom strength. Glow was named as slop; this is a uniform, not maths. */
  bloomStrength: 0.35,
} as const;

const v3 = (c: RGB) => `vec3<f32>(${c.map((n) => n.toFixed(4)).join(', ')})`;

/**
 * A WGSL const block prepended to `common.wgsl`, which every shader module is
 * already concatenated onto — so one injection reaches all twelve.
 */
export function wgslPalette(): string {
  return [
    '// ── Injected by renderer/palette.ts — do not edit here ──',
    `const PMA_SKY_HORIZON  = ${v3(VITRINE.skyHorizon)};`,
    `const PMA_SKY_MID      = ${v3(VITRINE.skyMid)};`,
    `const PMA_SKY_ZENITH   = ${v3(VITRINE.skyZenith)};`,
    `const PMA_KEY          = ${v3(VITRINE.keyLight)};`,
    `const PMA_FILL         = ${v3(VITRINE.fillLight)};`,
    `const PMA_RIM          = ${v3(VITRINE.rimLight)};`,
    `const PMA_WASH_WARM    = ${v3(VITRINE.washWarm)};`,
    `const PMA_WASH_COOL    = ${v3(VITRINE.washCool)};`,
    `const PMA_WASH_BELOW   = ${v3(VITRINE.washBelow)};`,
    `const PMA_GODRAY_TINT  = ${v3(VITRINE.godrayTint)};`,
    `const PMA_MOTE_WARM    = ${v3(VITRINE.moteWarm)};`,
    `const PMA_MOTE_COOL    = ${v3(VITRINE.moteCool)};`,
    `const PMA_BUBBLE_BASE  = ${v3(VITRINE.bubbleBase)};`,
    `const PMA_BUBBLE_INNER = ${v3(VITRINE.bubbleInner)};`,
    `const PMA_CRYSTAL_SHADE= ${v3(VITRINE.crystalShade)};`,
    `const PMA_CRYSTAL_EDGE = ${v3(VITRINE.crystalEdge)};`,
    '',
  ].join('\n');
}

/** 0xRRGGBB for the three.js tier, which takes hex or Color triples. */
export const hex = (c: RGB): number =>
  (Math.round(c[0] * 255) << 16) | (Math.round(c[1] * 255) << 8) | Math.round(c[2] * 255);
