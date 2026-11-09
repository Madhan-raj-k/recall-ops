// Bioluminescent particle field drifting upward as you descend. Original RecallOps scene.
const canvas = document.getElementById("abyss");
const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function hasWebGL() {
  try { const c = document.createElement("canvas"); return !!(c.getContext("webgl2") || c.getContext("webgl")); } catch { return false; }
}

async function start() {
  if (!hasWebGL()) { document.documentElement.classList.add("no-webgl"); canvas.remove(); return; }
  let THREE;
  try { THREE = await import("https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js"); }
  catch { document.documentElement.classList.add("no-webgl"); canvas.remove(); return; }

  const mobile = window.innerWidth < 768;
  const COUNT = mobile ? 1800 : 5000;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: true, powerPreference: "low-power" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, mobile ? 1.5 : 1.75));
  renderer.setSize(window.innerWidth, window.innerHeight, false);
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x02040b, 0.035);
  const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 120);
  camera.position.set(0, 12, 0);
  const CX = mobile ? 0 : 5.5; // vortex sits right of the headline on desktop

  // Particles in a loose helical column; drift speed + phase per particle.
  const pos = new Float32Array(COUNT * 3), col = new Float32Array(COUNT * 3), seed = new Float32Array(COUNT), size = new Float32Array(COUNT);
  const palette = [new THREE.Color("#67e8f9"), new THREE.Color("#2dd4bf"), new THREE.Color("#a78bfa"), new THREE.Color("#e0f2fe")];
  for (let i = 0; i < COUNT; i++) {
    const r = 1.5 + Math.pow(Math.random(), 0.7) * 11;
    const a = Math.random() * Math.PI * 2;
    pos[i * 3] = Math.cos(a) * r;
    pos[i * 3 + 1] = (Math.random() - 0.5) * 60;
    pos[i * 3 + 2] = Math.sin(a) * r;
    const c = palette[Math.random() < 0.55 ? 0 : Math.random() < 0.5 ? 1 : Math.random() < 0.75 ? 2 : 3];
    col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
    seed[i] = Math.random();
    size[i] = Math.random() < 0.04 ? 3.2 + Math.random() * 2.5 : 0.8 + Math.random() * 1.4;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
  geo.setAttribute("seed", new THREE.BufferAttribute(seed, 1));
  geo.setAttribute("psize", new THREE.BufferAttribute(size, 1));

  const uniforms = { uTime: { value: 0 }, uDepth: { value: 0 }, uPulse: { value: 0 }, uPx: { value: renderer.getPixelRatio() }, uCx: { value: CX } };
  const mat = new THREE.ShaderMaterial({
    uniforms, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, vertexColors: true,
    vertexShader: `
      attribute float seed; attribute float psize; varying vec3 vC; varying float vA;
      uniform float uTime, uDepth, uPulse, uPx, uCx;
      void main(){
        vec3 p = position;
        float t = uTime * (0.25 + seed * 0.5);
        p.y = mod(p.y + t * 2.2 + uDepth * 60.0 + 30.0, 60.0) - 30.0 - 18.0;   // rise toward the camera: we are sinking
        float ang = uTime * 0.09 * (0.5 + seed) + p.y * 0.06;           // slow helical swirl
        p.xz = mat2(cos(ang), -sin(ang), sin(ang), cos(ang)) * p.xz;
        p.x += sin(uTime * 0.6 + seed * 40.0) * 0.25 + uCx;
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        gl_Position = projectionMatrix * mv;
        float tw = 0.55 + 0.45 * sin(uTime * (1.0 + seed * 2.5) + seed * 60.0);
        gl_PointSize = psize * (1.0 + uPulse * 1.2) * uPx * (70.0 / -mv.z);
        vC = color; vA = tw * (0.25 + 0.75 * smoothstep(55.0, 4.0, -mv.z));
      }`,
    fragmentShader: `
      varying vec3 vC; varying float vA; uniform float uPulse;
      void main(){
        float d = length(gl_PointCoord - 0.5);
        float a = smoothstep(0.5, 0.0, d); a *= a;
        gl_FragColor = vec4(vC * (1.5 + uPulse), min(1.0, a * vA * 1.7));
      }`,
  });
  const points = new THREE.Points(geo, mat);
  scene.add(points);

  let depth = 0, targetDepth = 0, visible = true, last = performance.now(), t = 0;
  window.addEventListener("scroll", () => {
    const max = document.documentElement.scrollHeight - window.innerHeight;
    targetDepth = max > 0 ? window.scrollY / max : 0;
  }, { passive: true });
  window.addEventListener("recall-pulse", () => { uniforms.uPulse.value = 1.0; });
  document.addEventListener("visibilitychange", () => { visible = !document.hidden; if (visible) { last = performance.now(); loop(); } });
  window.addEventListener("resize", () => {
    camera.aspect = window.innerWidth / window.innerHeight; camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight, false);
  });
  let mx = 0, my = 0;
  window.addEventListener("pointermove", (e) => { mx = e.clientX / window.innerWidth - 0.5; my = e.clientY / window.innerHeight - 0.5; }, { passive: true });

  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now; t += dt;
    depth += (targetDepth - depth) * 0.06;
    uniforms.uTime.value = t; uniforms.uDepth.value = depth;
    uniforms.uPulse.value *= 0.95;
    camera.position.x += (CX * 0.15 + mx * 1.6 - camera.position.x) * 0.03;
    camera.position.z += (my * 1.6 - camera.position.z) * 0.03;
    camera.lookAt(CX, -40, 0.001);
    scene.fog.density = 0.022 + depth * 0.02;
    renderer.render(scene, camera);
  }
  function loop() { if (!visible) return; frame(performance.now()); requestAnimationFrame(loop); }
  if (reduce) { frame(performance.now()); window.addEventListener("scroll", () => frame(performance.now()), { passive: true }); }
  else loop();
  window.__abyssReady = true;
}
start();
