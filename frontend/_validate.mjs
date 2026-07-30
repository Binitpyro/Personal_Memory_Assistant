const { generateCrystalVariants, CRYSTAL_VARIANTS } = await import('./src/renderer/geometry/icosahedron.ts');
const vs = generateCrystalVariants(CRYSTAL_VARIANTS);
let bad = 0;
vs.forEach((m, i) => {
  let nan = 0, maxR = 0, minR = Infinity, huge = 0;
  for (let k = 0; k < m.vertexCount; k++) {
    const o = k * 6;
    const x = m.vertices[o], y = m.vertices[o+1], z = m.vertices[o+2];
    const nx = m.vertices[o+3], ny = m.vertices[o+4], nz = m.vertices[o+5];
    if ([x,y,z,nx,ny,nz].some(v => !Number.isFinite(v))) nan++;
    const r = Math.hypot(x,y,z);
    if (Number.isFinite(r)) { maxR = Math.max(maxR, r); minR = Math.min(minR, r); }
    if (r > 1.001) huge++;
    const nl = Math.hypot(nx,ny,nz);
    if (Number.isFinite(nl) && Math.abs(nl - 1) > 1e-3) huge++;
  }
  const flag = (nan || huge) ? '  <== PROBLEM' : '';
  if (nan || huge) bad++;
  console.log(`v${String(i).padStart(2)}: verts=${String(m.vertexCount).padStart(5)} idx=${String(m.indexCount).padStart(5)} maxR=${maxR.toFixed(3)} minR=${minR.toFixed(3)} NaN=${nan} outOfRange=${huge}${flag}`);
});
console.log(bad ? `\n${bad} variant(s) BAD` : '\nall variants clean');
