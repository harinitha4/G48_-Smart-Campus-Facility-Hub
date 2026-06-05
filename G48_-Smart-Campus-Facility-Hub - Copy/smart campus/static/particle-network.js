(() => {
  const DOT_COUNT = 80;
  const LINE_DISTANCE = 180;

  const BG_COLOR = '#0a1628';
  const DOT_COLOR = '#00c4cc';

  // Tuned for smooth animation
  const SPEED = 0.18; // base velocity magnitude
  const ATTRACTION_RADIUS = 140;
  const ATTRACTION_STRENGTH = 0.0009;
  const FRICTION = 0.999;

  const canvas = document.getElementById('particle-canvas');
  if (!canvas) return;

  const ctx = canvas.getContext('2d', { alpha: true });

  /** @type {{x:number,y:number,vx:number,vy:number}[]} */
  let dots = [];
  let w = 0;
  let h = 0;
  let dpr = Math.max(1, window.devicePixelRatio || 1);

  const mouse = {
    x: 0,
    y: 0,
    active: false,
  };

  function rand(min, max) {
    return Math.random() * (max - min) + min;
  }

  function resize() {
    w = window.innerWidth;
    h = window.innerHeight;

    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';

    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // Re-seed dots on first resize or when dimensions change significantly
    dots = Array.from({ length: DOT_COUNT }).map(() => {
      const speedAngle = rand(0, Math.PI * 2);
      const vx = Math.cos(speedAngle) * rand(0.05, SPEED);
      const vy = Math.sin(speedAngle) * rand(0.05, SPEED);

      return {
        x: rand(0, w),
        y: rand(0, h),
        vx,
        vy,
      };
    });
  }

  function drawBackground() {
    // Use solid navy base; gradients can be handled by CSS.
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = BG_COLOR;
    ctx.fillRect(0, 0, w, h);
  }

  function step() {
    if (!w || !h) return;

    drawBackground();

    // Update positions + bounce
    for (const p of dots) {
      // Mouse attraction (gentle)
      if (mouse.active) {
        const dx = mouse.x - p.x;
        const dy = mouse.y - p.y;
        const dist2 = dx * dx + dy * dy;
        if (dist2 < ATTRACTION_RADIUS * ATTRACTION_RADIUS) {
          const dist = Math.sqrt(dist2) || 1;
          const nx = dx / dist;
          const ny = dy / dist;

          // stronger when closer
          const falloff = 1 - dist / ATTRACTION_RADIUS;
          p.vx += nx * falloff * ATTRACTION_STRENGTH * dist;
          p.vy += ny * falloff * ATTRACTION_STRENGTH * dist;
        }
      }

      p.vx *= FRICTION;
      p.vy *= FRICTION;

      p.x += p.vx;
      p.y += p.vy;

      // Bounce off edges
      if (p.x <= 0) {
        p.x = 0;
        p.vx *= -1;
      } else if (p.x >= w) {
        p.x = w;
        p.vx *= -1;
      }

      if (p.y <= 0) {
        p.y = 0;
        p.vy *= -1;
      } else if (p.y >= h) {
        p.y = h;
        p.vy *= -1;
      }
    }

    // Draw connecting lines
    ctx.lineWidth = 1;

    for (let i = 0; i < dots.length; i++) {
      const a = dots[i];

      for (let j = i + 1; j < dots.length; j++) {
        const b = dots[j];

        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const dist = Math.sqrt(dx * dx + dy * dy);

        if (dist <= LINE_DISTANCE) {
          const t = 1 - dist / LINE_DISTANCE; // 0..1
          const opacity = 0.02 + t * 0.55; // closer => more opaque

          ctx.strokeStyle = `rgba(0, 196, 204, ${opacity})`;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }
    }

    // Draw dots with glow
    for (const p of dots) {
      const speed = Math.hypot(p.vx, p.vy);
      const r = 2.0 + Math.min(2.2, speed * 10) * 0.25;

      // outer glow
      ctx.beginPath();
      ctx.fillStyle = `rgba(0, 196, 204, 0.12)`;
      ctx.shadowColor = DOT_COLOR;
      ctx.shadowBlur = 16;
      ctx.arc(p.x, p.y, r * 2.0, 0, Math.PI * 2);
      ctx.fill();

      // core
      ctx.shadowBlur = 10;
      ctx.beginPath();
      ctx.fillStyle = DOT_COLOR;
      ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
      ctx.fill();

      // reset shadow to avoid affecting subsequent draw
      ctx.shadowBlur = 0;
    }

    requestAnimationFrame(step);
  }

  function bindMouse() {
    const onMove = (e) => {
      mouse.x = e.clientX;
      mouse.y = e.clientY;
      mouse.active = true;
    };

    window.addEventListener('mousemove', onMove, { passive: true });
    window.addEventListener('mouseleave', () => {
      mouse.active = false;
    });

    // touch support
    window.addEventListener(
      'touchmove',
      (e) => {
        if (!e.touches || !e.touches.length) return;
        const t = e.touches[0];
        mouse.x = t.clientX;
        mouse.y = t.clientY;
        mouse.active = true;
      },
      { passive: true }
    );

    window.addEventListener('touchend', () => {
      mouse.active = false;
    });
  }

  // Start
  resize();
  bindMouse();

  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(resize, 100);
  });

  requestAnimationFrame(step);
})();

