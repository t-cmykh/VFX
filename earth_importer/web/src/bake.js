"use strict";
// Texture du LiDAR IGN prise sur la 3D Google : la 3D Google texturee est rendue vue de dessus
// (camera orthographique, haut de l'image = Nord), a la resolution d'origine des textures Google,
// en dalles de 4096 px au plus si besoin, puis plaquee sur les maillages LiDAR (decoupes selon les
// memes dalles) par projection verticale (UV = position Est/Nord du sommet).
// Les faces verticales (facades) recoivent donc une texture etiree.

const BAKE = {
  background: 0x808080,

  tileMax: 4096,   // cote max d'une dalle de texture : 4096 x 4096 = limite des canvas de Safari iOS
  maxTiles: 6,     // au plus 6 x 6 dalles (au-dela, la resolution baisse ; le log le dit)
  pad: 2,          // marge (m) autour de chaque dalle : les triangles a cheval restent dans leur texture

  // Decoupage de la zone : resolution d'origine des textures Google (mpt = m/texel), en dalles
  plan(half, mpt, renderer, preview) {
    const device = Math.min(BAKE.tileMax, renderer.capabilities.maxTextureSize,
                            renderer.capabilities.maxRenderbufferSize || Infinity);
    const need = Math.ceil(2 * half / (mpt > 0 ? mpt : 0.1));
    let n = preview ? 1 : Math.max(1, Math.ceil(need / device));
    const capped = n > BAKE.maxTiles;
    n = Math.min(n, BAKE.maxTiles);
    const pad = n > 1 ? BAKE.pad : 0, cell = 2 * half / n;
    const size = Math.min(device, Math.ceil(need * (cell + 2 * pad) / (2 * half) / 4) * 4);
    return { n, size: Math.max(size, 256), cell, pad, half, need, capped };
  },

  // tuiles Google (format exporteur, UV convention rocktree) -> plan + JPEG de chaque dalle [ligne][colonne]
  // (ligne 0 = Nord, colonne 0 = Ouest)
  async topView(tiles, half, mpt, flipV, ui, preview = false) {
    const renderer = new THREE.WebGLRenderer({ antialias: false, preserveDrawingBuffer: true });
    renderer.outputEncoding = THREE.LinearEncoding;  // octets des JPEG recopies tels quels
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(BAKE.background);
    const urls = [];
    let top = -Infinity, bottom = Infinity, done = 0;
    const textured = tiles.filter((t) => t.uv && t.texture);
    try {
      for (const t of textured) {
        const url = URL.createObjectURL(new Blob([t.texture], { type: "image/jpeg" }));
        urls.push(url);
        const img = new Image();
        img.src = url;
        await img.decode();
        const tex = new THREE.Texture(img);  // flipY : convention OpenGL, comme previewUv
        tex.needsUpdate = true;
        tex.minFilter = THREE.LinearMipmapLinearFilter;
        tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping;
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.BufferAttribute(Float32Array.from(t.pos), 3));
        g.setAttribute("uv", new THREE.BufferAttribute(previewUv(t.uv, flipV), 2));
        g.setIndex(new THREE.BufferAttribute(t.faces instanceof Uint32Array ? t.faces : Uint32Array.from(t.faces), 1));
        scene.add(new THREE.Mesh(g, new THREE.MeshBasicMaterial({ map: tex, side: THREE.DoubleSide })));
        for (let i = 1; i < t.pos.length; i += 3) { top = Math.max(top, t.pos[i]); bottom = Math.min(bottom, t.pos[i]); }
        if (++done % 50 === 0) ui.progress("Texture LiDAR : lecture des images Google", done, textured.length);
      }
      if (!textured.length) return null;
      const plan = BAKE.plan(half, mpt, renderer, preview), { n, size, cell, pad } = plan;
      const target = new THREE.WebGLRenderTarget(size, size);
      const px = new Uint8Array(size * size * 4);
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = size;
      const ctx = canvas.getContext("2d"), img = ctx.createImageData(size, size), row = 4 * size;
      plan.images = [];
      for (let r = 0; r < n; r++) {
        plan.images.push([]);
        for (let c = 0; c < n; c++) {
          ui.progress("Texture LiDAR : rendu vue de dessus", r * n + c + 1, n * n);
          await new Promise((res) => setTimeout(res, 0));
          // vue de dessus : X = Est vers la droite, -Z = Nord vers le haut ; dalle (r, c) + marge
          const x0 = -half + c * cell - pad, yTop = half - r * cell + pad, w = cell + 2 * pad;
          const cam = new THREE.OrthographicCamera(x0, x0 + w, yTop, yTop - w, 1, top - bottom + 200);
          cam.position.set(0, top + 100, 0);
          cam.up.set(0, 0, -1);
          cam.lookAt(0, bottom, 0);
          renderer.setRenderTarget(target);
          renderer.render(scene, cam);
          renderer.readRenderTargetPixels(target, 0, 0, size, size, px);
          renderer.setRenderTarget(null);
          // lignes WebGL du bas vers le haut -> image du haut (Nord) vers le bas
          for (let y = 0; y < size; y++) img.data.set(px.subarray((size - 1 - y) * row, (size - y) * row), y * row);
          ctx.putImageData(img, 0, 0);
          const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.9));
          if (!blob) throw new Error(`Texture LiDAR : image de ${size} px refusée par le navigateur`);
          plan.images[r].push(new Uint8Array(await blob.arrayBuffer()));
        }
      }
      target.dispose();
      return plan;
    } finally {
      scene.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) { if (o.material.map) o.material.map.dispose(); o.material.dispose(); } });
      urls.forEach((u) => URL.revokeObjectURL(u));
      renderer.dispose();
      renderer.forceContextLoss();
    }
  },

  // Decoupe une tuile LiDAR selon les dalles du plan (centre des triangles) ; chaque morceau recoit ses UV
  // de projection verticale (convention rocktree, comme les tuiles Google) et la texture de sa dalle.
  split(tile, plan, flipV) {
    const { n, cell, pad, half } = plan, p = tile.pos, f = tile.faces, w = cell + 2 * pad;
    const buckets = Array.from({ length: n * n }, () => []);
    for (let i = 0; i < f.length; i += 3) {
      const a = 3 * f[i], b = 3 * f[i + 1], c = 3 * f[i + 2];
      const e = (p[a] + p[b] + p[c]) / 3, no = -(p[a + 2] + p[b + 2] + p[c + 2]) / 3;
      const col = Math.min(n - 1, Math.max(0, Math.floor((e + half) / cell)));
      const r = Math.min(n - 1, Math.max(0, Math.floor((half - no) / cell)));
      buckets[r * n + col].push(f[i], f[i + 1], f[i + 2]);
    }
    const out = [];
    buckets.forEach((keep, k) => {
      if (!keep.length) return;
      const r = Math.floor(k / n), c = k % n, x0 = -half + c * cell - pad, y0 = half - (r + 1) * cell - pad;
      const remap = new Map(), faces = new Uint32Array(keep.length);
      keep.forEach((v, i) => { if (!remap.has(v)) remap.set(v, remap.size); faces[i] = remap.get(v); });
      const pos = new Float64Array(3 * remap.size), uv = new Float64Array(2 * remap.size);
      for (const [src, d] of remap) {
        pos[3 * d] = p[3 * src]; pos[3 * d + 1] = p[3 * src + 1]; pos[3 * d + 2] = p[3 * src + 2];
        uv[2 * d] = (p[3 * src] - x0) / w;
        const v = (-p[3 * src + 2] - y0) / w;  // Nord en haut (convention OpenGL)
        uv[2 * d + 1] = flipV ? 1 - v : v;      // inverse de uvForObj : les exporteurs la reappliquent
      }
      const suffix = n > 1 ? `_r${r}c${c}` : "";
      out.push({ ...tile, name: tile.name + suffix, label: (tile.label || tile.name) + suffix, pos, faces, uv,
                 texture: plan.images[r][c], texName: `lidar${suffix || "_texture"}`, product: tile.name });
    });
    return out;
  },

};
