"use strict";
// Calage du LiDAR IGN sur la 3D Google (meme algorithme que core/align.py).
// Les deux maillages sont ramenes a des grilles de hauteur (max par cellule) dans le repere local,
// puis on cherche le decalage horizontal qui maximise leur correlation (NCC), d'abord grossierement
// (cellules de 4 m, +/- 40 m) puis de plus en plus finement (1 m, 0,5 m). Le decalage vertical est la mediane des ecarts.

const ALIGN = {
  // recherche en pyramide : cellule (m) et portee (+/- m) de chaque passe
  passes: [{ cell: 4, range: 40 }, { cell: 1, range: 4 }, { cell: 0.5, range: 1 }],
  maxHalf: 150,     // on ne compare que le centre de la zone (300 m x 300 m au plus) : rapide sur telephone
  minScore: 0.3,    // en dessous, le calage n'est pas fiable : on ne deplace rien
  minOverlap: 400,

  // points (Est, Nord, haut) d'un maillage au format exporteur (Y en haut, -Z = Nord) : tous les sommets,
  // plus des points a l'interieur des grands triangles (un tous les ~1 m) pour ne pas laisser de trous
  samples(tiles, half) {
    const E = [], N = [], H = [], lim = half + 20, step = 1.0;
    for (const t of tiles) {
      const p = t.pos, f = t.faces;
      for (let i = 0; i < p.length; i += 3) {
        if (Math.abs(p[i]) > lim || Math.abs(p[i + 2]) > lim) continue;
        E.push(p[i]); H.push(p[i + 1]); N.push(-p[i + 2]);
      }
      for (let i = 0; i < f.length; i += 3) {
        const a = 3 * f[i], b = 3 * f[i + 1], c = 3 * f[i + 2];
        const edge = Math.max(Math.hypot(p[a] - p[b], p[a + 2] - p[b + 2]), Math.hypot(p[b] - p[c], p[b + 2] - p[c + 2]),
                              Math.hypot(p[c] - p[a], p[c + 2] - p[a + 2]));
        if (edge <= 1.5 * step) continue;
        if (Math.abs(p[a]) > lim + edge || Math.abs(p[a + 2]) > lim + edge) continue;
        const k = Math.min(Math.ceil(edge / step), 40);
        for (let u = 0; u <= k; u++) for (let v = 0; v <= k - u; v++) {
          if (u === k || v === k || u + v === 0) continue;  // sommets deja pris
          const wa = u / k, wb = v / k, wc = 1 - wa - wb;
          E.push(wa * p[a] + wb * p[b] + wc * p[c]);
          H.push(wa * p[a + 1] + wb * p[b + 1] + wc * p[c + 1]);
          N.push(-(wa * p[a + 2] + wb * p[b + 2] + wc * p[c + 2]));
        }
      }
    }
    return { E, N, H };
  },

  grid(s, half, cell, dE = 0, dN = 0) {
    const n = Math.ceil(2 * half / cell), g = new Float32Array(n * n).fill(NaN);
    for (let i = 0; i < s.E.length; i++) {
      const x = Math.floor((s.E[i] + dE + half) / cell), y = Math.floor((s.N[i] + dN + half) / cell);
      if (x < 0 || y < 0 || x >= n || y >= n) continue;
      const k = y * n + x;
      if (!(g[k] >= s.H[i])) g[k] = s.H[i];
    }
    return { g, n };
  },

  // meilleur decalage (en cellules) de B pour ressembler a A, dans +/- r cellules
  search(A, B, r) {
    const n = A.n;
    let best = { score: -2, sx: 0, sy: 0 };
    for (let sy = -r; sy <= r; sy++) for (let sx = -r; sx <= r; sx++) {
      let m = 0, sa = 0, sb = 0, saa = 0, sbb = 0, sab = 0;
      for (let y = Math.max(0, sy); y < Math.min(n, n + sy); y++) {
        const ra = y * n, rb = (y - sy) * n - sx;
        for (let x = Math.max(0, sx); x < Math.min(n, n + sx); x++) {
          const a = A.g[ra + x], b = B.g[rb + x];
          if (a !== a || b !== b) continue;  // NaN
          m++; sa += a; sb += b; saa += a * a; sbb += b * b; sab += a * b;
        }
      }
      if (m < ALIGN.minOverlap) continue;
      const va = saa - sa * sa / m, vb = sbb - sb * sb / m;
      if (va <= 0 || vb <= 0) continue;
      const score = (sab - sa * sb / m) / Math.sqrt(va * vb);
      if (score > best.score) best = { score, sx, sy };
    }
    return best;
  },

  // decalage (Est, Nord, haut) a ajouter au LiDAR pour le poser sur Google, et score de correlation
  solve(ref, lidar, half) {
    half = Math.min(half, ALIGN.maxHalf);
    let dE = 0, dN = 0, score = -2, coarseScore = -2, A = null;
    for (const [k, pass] of ALIGN.passes.entries()) {
      A = ALIGN.grid(ref, half, pass.cell);
      const B = ALIGN.grid(lidar, half, pass.cell, dE, dN);
      const best = ALIGN.search(A, B, Math.round(pass.range / pass.cell));
      if (best.score <= -2) break;
      dE += best.sx * pass.cell; dN += best.sy * pass.cell; score = best.score;
      if (k === 0) coarseScore = best.score;
    }
    // vertical : mediane des ecarts une fois cale (cellules de la derniere passe)
    const cell = ALIGN.passes[ALIGN.passes.length - 1].cell;
    A = ALIGN.grid(ref, half, cell);
    const B = ALIGN.grid(lidar, half, cell, dE, dN), diffs = [];
    for (let k = 0; k < A.g.length; k++) if (A.g[k] === A.g[k] && B.g[k] === B.g[k]) diffs.push(A.g[k] - B.g[k]);
    diffs.sort((x, y) => x - y);
    const dH = diffs.length ? diffs[diffs.length >> 1] : 0;
    return { dE, dN, dH, score, coarseScore };
  },

  apply(tile, d) {
    const p = tile.pos;
    for (let i = 0; i < p.length; i += 3) { p[i] += d.dE; p[i + 1] += d.dH; p[i + 2] -= d.dN; }
  },
};

if (typeof module !== "undefined") module.exports = { ALIGN };
