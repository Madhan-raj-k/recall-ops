// Ambient background: white dots + links on black that react to scroll, clicks, typing and the mouse.
(() => {
  const c = document.getElementById("bgfx"); if (!c) return;
  const x = c.getContext("2d"); let W, H, D = [], R = [], dpr = Math.min(window.devicePixelRatio || 1, 2);
  const mouse = { x: -1e4, y: -1e4 }; let energy = 0, lastY = null;
  function size() {
    W = innerWidth; H = innerHeight; c.width = W * dpr; c.height = H * dpr;
    c.style.width = W + "px"; c.style.height = H + "px"; x.setTransform(dpr, 0, 0, dpr, 0, 0);
    const n = Math.round(Math.min(170, (W * H) / 9000));
    D = Array.from({ length: n }, () => ({
      x: Math.random() * W, y: Math.random() * H,
      vx: (Math.random() - .5) * .35, vy: (Math.random() - .5) * .35,
      r: Math.random() * 1.8 + .9, p: Math.random() * 6.283, z: Math.random() * .8 + .2
    }));
  }
  addEventListener("resize", size);
  addEventListener("mousemove", e => { mouse.x = e.clientX; mouse.y = e.clientY; });
  // scroll anywhere (window or inner panels): dots stream with parallax, motion speeds up
  addEventListener("scroll", e => {
    const t = e.target === document ? document.scrollingElement : e.target;
    const y = t && t.scrollTop || 0, dy = lastY == null ? 0 : y - lastY; lastY = y;
    for (const d of D) d.y -= dy * .35 * d.z;
    energy = Math.min(4, energy + Math.abs(dy) / 40);
  }, true);
  addEventListener("wheel", e => { energy = Math.min(4, energy + Math.abs(e.deltaY) / 120); }, { passive: true });
  // click: ripple ring + push dots outward
  addEventListener("pointerdown", e => {
    R.push({ x: e.clientX, y: e.clientY, r: 0, a: 1 });
    for (const d of D) { const dx = d.x - e.clientX, dy = d.y - e.clientY, m = Math.hypot(dx, dy) || 1;
      if (m < 260) { const f = (260 - m) / 260 * 4; d.vx += dx / m * f; d.vy += dy / m * f; } }
    energy = Math.min(4, energy + 1.5);
  });
  // typing: a little burst of life
  addEventListener("keydown", () => { energy = Math.min(4, energy + .4); });
  size();
  let t = 0;
  function frame() {
    t += .016; energy *= .96; x.clearRect(0, 0, W, H);
    const boost = 1 + energy * 1.6;
    for (const d of D) {
      d.vx *= .985; d.vy *= .985;
      if (Math.hypot(d.vx, d.vy) < .12) { d.vx += (Math.random() - .5) * .05; d.vy += (Math.random() - .5) * .05; }
      d.x += d.vx * boost; d.y += d.vy * boost;
      if (d.x < -10) d.x = W + 10; if (d.x > W + 10) d.x = -10;
      if (d.y < -10) d.y = H + 10; if (d.y > H + 10) d.y = -10;
      const mx = d.x - mouse.x, my = d.y - mouse.y, md = Math.hypot(mx, my) || 1;
      if (md < 140) { d.x += mx / md * 1.2; d.y += my / md * 1.2; }
    }
    const L = 16000 + energy * 3000;
    x.lineWidth = .7;
    for (let i = 0; i < D.length; i++) for (let j = i + 1; j < D.length; j++) {
      const a = D[i], b = D[j], dx = a.x - b.x, dy = a.y - b.y, dd = dx * dx + dy * dy;
      if (dd < L) { x.strokeStyle = `rgba(255,255,255,${(1 - dd / L) * (.28 + energy * .08)})`;
        x.beginPath(); x.moveTo(a.x, a.y); x.lineTo(b.x, b.y); x.stroke(); }
    }
    // links to the mouse
    for (const d of D) { const dx = d.x - mouse.x, dy = d.y - mouse.y, dd = dx * dx + dy * dy;
      if (dd < 40000) { x.strokeStyle = `rgba(255,255,255,${(1 - dd / 40000) * .45})`; x.beginPath(); x.moveTo(d.x, d.y); x.lineTo(mouse.x, mouse.y); x.stroke(); } }
    for (const d of D) {
      const a = Math.min(1, .6 + .35 * Math.sin(t * 2 + d.p) + energy * .1);
      x.fillStyle = `rgba(255,255,255,${a})`; x.beginPath(); x.arc(d.x, d.y, d.r * (1 + energy * .12), 0, 6.283); x.fill();
    }
    for (let i = R.length - 1; i >= 0; i--) { const r = R[i]; r.r += 6; r.a *= .94;
      x.strokeStyle = `rgba(255,255,255,${r.a})`; x.lineWidth = 1.5; x.beginPath(); x.arc(r.x, r.y, r.r, 0, 6.283); x.stroke();
      if (r.a < .03) R.splice(i, 1); }
    requestAnimationFrame(frame);
  }
  frame();
})();
