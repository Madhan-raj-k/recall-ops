// Ambient background: drifting dots with faint links and a slow pulse. Purely decorative.
(() => {
  const c = document.getElementById("bgfx"); if (!c) return;
  const x = c.getContext("2d"); let W, H, D = [], dpr = Math.min(window.devicePixelRatio || 1, 2);
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const mouse = { x: -1e4, y: -1e4 };
  function size() {
    W = innerWidth; H = innerHeight; c.width = W * dpr; c.height = H * dpr;
    c.style.width = W + "px"; c.style.height = H + "px"; x.setTransform(dpr, 0, 0, dpr, 0, 0);
    const n = Math.round(Math.min(140, (W * H) / 11000));
    D = Array.from({ length: n }, () => ({
      x: Math.random() * W, y: Math.random() * H,
      vx: (Math.random() - .5) * .25, vy: (Math.random() - .5) * .25,
      r: Math.random() * 1.8 + .7, p: Math.random() * Math.PI * 2
    }));
  }
  addEventListener("resize", size); addEventListener("mousemove", e => { mouse.x = e.clientX; mouse.y = e.clientY; });
  size();
  let t = 0;
  function frame() {
    t += .016; x.clearRect(0, 0, W, H);
    for (const d of D) {
      d.x += d.vx; d.y += d.vy;
      if (d.x < -10) d.x = W + 10; if (d.x > W + 10) d.x = -10;
      if (d.y < -10) d.y = H + 10; if (d.y > H + 10) d.y = -10;
      const mx = d.x - mouse.x, my = d.y - mouse.y, md = Math.hypot(mx, my);
      if (md < 120) { d.x += mx / md * .6; d.y += my / md * .6; }
    }
    for (let i = 0; i < D.length; i++) for (let j = i + 1; j < D.length; j++) {
      const a = D[i], b = D[j], dx = a.x - b.x, dy = a.y - b.y, dd = dx * dx + dy * dy;
      if (dd < 15000) { x.strokeStyle = `rgba(160,170,255,${(1 - dd / 15000) * .2})`; x.lineWidth = .6;
        x.beginPath(); x.moveTo(a.x, a.y); x.lineTo(b.x, b.y); x.stroke(); }
    }
    for (const d of D) {
      const a = .45 + .4 * Math.sin(t * 1.5 + d.p);
      x.fillStyle = `rgba(220,225,255,${a})`; x.beginPath(); x.arc(d.x, d.y, d.r, 0, 6.283); x.fill();
    }
    if (!reduce) requestAnimationFrame(frame);
  }
  frame();
})();
