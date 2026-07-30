// Renders the real generateCrystalVariants() output to an SVG so the silhouette
// and facet layout can be inspected without the browser.
//   node --experimental-strip-types _crystal_preview.mjs > crystals.svg
// Facet counts go to stderr — a face family whose planes never bite is a silent
// no-op, so the numbers matter as much as the picture.
import { generateCrystalVariants, CRYSTAL_VARIANTS } from './src/renderer/geometry/icosahedron.ts';

const variants = generateCrystalVariants(CRYSTAL_VARIANTS);

const COLS = 5;
const W = 260, H = 260, S = 105;

function project(x, y, z, yaw, pitch) {
  // simple orbit camera
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  let X = x * cy - z * sy, Z = x * sy + z * cy;
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  let Y = y * cp - Z * sp; Z = y * sp + Z * cp;
  return [X, Y, Z];
}

/** Distinct planar facets = distinct face normals (coplanar fan triangles share one). */
function facetCount(m) {
  const seen = new Set();
  for (let i = 0; i < m.indexCount; i += 3) {
    const o = i * 6;
    seen.add([3, 4, 5].map(k => m.vertices[o + k].toFixed(3)).join(','));
  }
  return seen.size;
}

let svg = '';
variants.forEach((m, vi) => {
  const yaw = 0.6 + vi * 0.55, pitch = 0.30;
  const tris = [];
  for (let i = 0; i < m.indexCount; i += 3) {
    const p = [], n = [];
    for (let k = 0; k < 3; k++) {
      const o = (i + k) * 6;
      const [px, py, pz] = project(m.vertices[o], m.vertices[o+1], m.vertices[o+2], yaw, pitch);
      p.push([px, py, pz]);
      if (k === 0) {
        const [nx, ny, nz] = project(m.vertices[o+3], m.vertices[o+4], m.vertices[o+5], yaw, pitch);
        n.push(nx, ny, nz);
      }
    }
    const depth = (p[0][2] + p[1][2] + p[2][2]) / 3;
    if (n[2] <= 0) continue; // backface cull
    const lambert = Math.max(0.12, n[2] * 0.75 + 0.25);
    tris.push({ p, depth, lambert });
  }
  tris.sort((a, b) => a.depth - b.depth);

  const facets = facetCount(m);
  process.stderr.write(`variant ${String(vi).padStart(2)} · ${String(m.indexCount/3).padStart(4)} tris · ${String(facets).padStart(3)} facets\n`);

  const ox = (vi % COLS) * W, oy = Math.floor(vi / COLS) * H;
  svg += `<g transform="translate(${ox},${oy})"><rect width="${W}" height="${H}" fill="#0d0a1a"/>`;
  for (const t of tris) {
    const pts = t.p.map(q => `${(W/2 + q[0]*S).toFixed(1)},${(H/2 - q[1]*S).toFixed(1)}`).join(' ');
    const c = Math.round(t.lambert * 210);
    svg += `<polygon points="${pts}" fill="rgb(${Math.round(c*0.85)},${Math.round(c*0.55)},${c})" stroke="rgba(255,255,255,0.16)" stroke-width="0.4"/>`;
  }
  svg += `<text x="8" y="18" fill="#9f8fd0" font-family="monospace" font-size="11">v${vi} · ${m.indexCount/3} tris · ${facets} facets</text></g>`;
});

const rows = Math.ceil(variants.length / COLS);
const out = `<svg xmlns="http://www.w3.org/2000/svg" width="${W*COLS}" height="${H*rows}" viewBox="0 0 ${W*COLS} ${H*rows}">${svg}</svg>`;
process.stdout.write(out);
