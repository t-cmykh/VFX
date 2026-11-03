"use strict";
// Texture du LiDAR IGN prise sur la 3D Google : la 3D Google texturee est rendue vue de dessus
// (camera orthographique, haut de l'image = Nord) dans une image carree couvrant la zone, puis
// plaquee sur les maillages LiDAR par projection verticale (UV = position Est/Nord du sommet).
// Les faces verticales (facades) recoivent donc une texture etiree.

const BAKE = {
  background: 0x808080,

  // tuiles Google (format exporteur, UV convention rocktree) -> JPEG (Uint8Array) de size x size px
  async topView(tiles, half, size, flipV, ui) {
    const renderer = new THREE.WebGLRenderer({ antialias: false, preserveDrawingBuffer: true });
    size = Math.min(size, renderer.capabilities.maxTextureSize, renderer.capabilities.maxRenderbufferSize || Infinity);
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
      // vue de dessus : X = Est vers la droite, -Z = Nord vers le haut
      const cam = new THREE.OrthographicCamera(-half, half, half, -half, 1, top - bottom + 200);
      cam.position.set(0, top + 100, 0);
      cam.up.set(0, 0, -1);
      cam.lookAt(0, bottom, 0);
      ui.progress("Texture LiDAR : rendu vue de dessus", 0, 0);
      const target = new THREE.WebGLRenderTarget(size, size);
      renderer.setRenderTarget(target);
      renderer.render(scene, cam);
      const px = new Uint8Array(size * size * 4);
      renderer.readRenderTargetPixels(target, 0, 0, size, size, px);
      renderer.setRenderTarget(null);
      target.dispose();
      // lignes WebGL du bas vers le haut -> image du haut (Nord) vers le bas
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = size;
      const ctx = canvas.getContext("2d"), img = ctx.createImageData(size, size), row = 4 * size;
      for (let y = 0; y < size; y++) img.data.set(px.subarray((size - 1 - y) * row, (size - y) * row), y * row);
      ctx.putImageData(img, 0, 0);
      const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.9));
      return { jpeg: new Uint8Array(await blob.arrayBuffer()), size };
    } finally {
      scene.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) { if (o.material.map) o.material.map.dispose(); o.material.dispose(); } });
      urls.forEach((u) => URL.revokeObjectURL(u));
      renderer.dispose();
      renderer.forceContextLoss();
    }
  },

  // UV de projection verticale d'une tuile LiDAR, en convention rocktree (comme les tuiles Google)
  planarUv(tile, half, flipV) {
    const p = tile.pos, uv = new Float64Array(2 * p.length / 3), side = 2 * half;
    for (let i = 0, k = 0; i < p.length; i += 3, k += 2) {
      uv[k] = (p[i] + half) / side;
      const v = (-p[i + 2] + half) / side;  // Nord en haut (convention OpenGL)
      uv[k + 1] = flipV ? 1 - v : v;         // inverse de uvForObj : les exporteurs la reappliquent
    }
    return uv;
  },
};
