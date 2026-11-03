"use strict";
// Apercu 3D (three.js) commun aux deux versions.
// Une tuile : { name, pos: Float32Array|Float64Array (Y en haut, X = Est, -Z = Nord),
//               faces: Uint32Array, uv: Float32Array|null (convention OpenGL, V deja corrige),
//               texture: Uint8Array|null (JPEG), color: optionnel (tuiles sans texture) }
// Couches (case a cocher en haut a droite de l'apercu) : tuiles "lidar_mns", "lidar_mnt", le reste = Google.

const LAYERS = [
  { key: "google", label: "Google 3D", color: 0x9ea4ad },
  { key: "lidar_mns", label: "LiDAR MNS", color: 0xd9925b },
  { key: "lidar_mnt", label: "LiDAR MNT", color: 0x6fae6a },
];
const layerOf = (t) => (t.name === "lidar_mns" || t.name === "lidar_mnt" ? t.name : "google");

const Preview = (() => {
  let renderer = null, scene = null, camera = null, controls = null, container = null, root = null;
  let frame = 0, urls = [];

  function init(el) {
    container = el;
    renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputEncoding = THREE.sRGBEncoding;
    el.append(renderer.domElement);
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x1b1e23);
    scene.add(new THREE.HemisphereLight(0xdfe6f0, 0x2a2e36, 0.45));
    const sun = new THREE.DirectionalLight(0xffffff, 0.75);
    sun.position.set(-0.6, 1, 0.35);
    scene.add(sun);
    camera = new THREE.PerspectiveCamera(45, 1, 0.5, 100000);
    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.screenSpacePanning = false;
    controls.maxPolarAngle = Math.PI * 0.495;
    new ResizeObserver(resize).observe(el);
    resize();
  }

  function resize() {
    if (!renderer) return;
    const w = container.clientWidth || 1, h = container.clientHeight || 1;
    renderer.setSize(w, h, false);
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  function clear() {
    if (!root) return;
    root.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      if (o.material) { if (o.material.map) o.material.map.dispose(); o.material.dispose(); }
    });
    scene.remove(root);
    root = null;
    urls.forEach((u) => URL.revokeObjectURL(u));
    urls = [];
  }

  function texture(jpeg) {
    const url = URL.createObjectURL(new Blob([jpeg], { type: "image/jpeg" }));
    urls.push(url);
    const tex = new THREE.TextureLoader().load(url);  // image HTML : flipY s'applique (convention OpenGL)
    tex.encoding = THREE.sRGBEncoding;
    tex.anisotropy = 4;
    tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping;
    return tex;
  }

  function show(el, tiles) {
    if (!renderer) init(el);
    clear();
    root = new THREE.Group();
    let tris = 0;
    for (const t of tiles) {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(Float32Array.from(t.pos), 3));
      g.setIndex(new THREE.BufferAttribute(t.faces instanceof Uint32Array ? t.faces : Uint32Array.from(t.faces), 1));
      let mat;
      if (t.uv && t.texture) {
        g.setAttribute("uv", new THREE.BufferAttribute(Float32Array.from(t.uv), 2));
        mat = new THREE.MeshBasicMaterial({ map: texture(t.texture), side: THREE.DoubleSide });
      } else {
        g.computeVertexNormals();
        const layer = LAYERS.find((l) => l.key === layerOf(t));
        mat = new THREE.MeshLambertMaterial({ color: layer.color, side: THREE.DoubleSide });
      }
      const mesh = new THREE.Mesh(g, mat);
      mesh.name = t.label || t.name;
      mesh.userData.layer = layerOf(t);
      root.add(mesh);
      tris += t.faces.length / 3;
    }
    scene.add(root);
    legend(tiles);
    frameAll();
    if (!frame) loop();
    return tris;
  }

  // cases a cocher pour masquer / afficher chaque couche presente
  function legend(tiles) {
    let box = container.querySelector(".layers");
    if (!box) {
      box = document.createElement("div");
      box.className = "layers";
      container.append(box);
    }
    box.innerHTML = "";
    const present = new Set(tiles.map(layerOf));
    for (const l of LAYERS) {
      if (!present.has(l.key)) continue;
      const lab = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = true;
      cb.dataset.layer = l.key;
      cb.onchange = () => root.children.forEach((m) => { if (m.userData.layer === l.key) m.visible = cb.checked; });
      const sw = document.createElement("span");
      sw.className = "swatch";
      sw.style.background = `#${l.color.toString(16).padStart(6, "0")}`;
      lab.append(cb, sw, document.createTextNode(l.label));
      box.append(lab);
    }
    box.hidden = present.size < 2;
  }

  function frameAll() {
    const box = new THREE.Box3().setFromObject(root);
    if (box.isEmpty()) return;
    const c = box.getCenter(new THREE.Vector3()), s = box.getSize(new THREE.Vector3());
    const d = Math.max(s.x, s.z, s.y * 2, 10);
    camera.near = Math.max(0.1, d / 2000);
    camera.far = d * 50;
    camera.updateProjectionMatrix();
    camera.position.set(c.x + d * 0.55, c.y + d * 0.65, c.z + d * 0.85);
    controls.target.copy(c);
    controls.update();
  }

  function loop() {
    frame = requestAnimationFrame(loop);
    if (!container.offsetParent) return;  // apercu masque : on ne dessine rien
    controls.update();
    renderer.render(scene, camera);
  }

  return { show, frameAll, resize, get ready() { return !!root; } };
})();
