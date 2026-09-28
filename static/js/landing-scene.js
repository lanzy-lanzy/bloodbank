// Three.js hero scene for the public landing page: a drifting field of
// biconcave red blood cells + dust particles, reacting to cursor, click and
// scroll. Purely decorative — no business logic lives here.
// Loads as a module so a failed CDN import can never break the page.
import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js";

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

try {
  const canvas = document.getElementById("hero-canvas");
  if (canvas) {
    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
      if (!renderer.getContext()) throw new Error("WebGL unavailable");
    } catch (err) {
      canvas.style.display = "none"; // CSS gradient backdrop remains
    }

    if (renderer) {
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setSize(window.innerWidth, window.innerHeight);

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(42, window.innerWidth / window.innerHeight, 0.1, 100);
      camera.position.set(0, 0, 15);

      scene.add(new THREE.AmbientLight(0xffffff, 0.55));
      const key = new THREE.DirectionalLight(0xffffff, 1.4);
      key.position.set(6, 9, 7);
      scene.add(key);
      const rim = new THREE.PointLight(0xe11d48, 90, 60, 2);
      rim.position.set(-8, -5, 6);
      scene.add(rim);
      const cool = new THREE.PointLight(0x3859c7, 55, 50, 2);
      cool.position.set(9, 5, -6);
      scene.add(cool);

      // Biconcave disc profile (thin centre, thick rim) lathed around Y.
      const PROFILE = [
        [0, 0.10], [0.28, 0.19], [0.55, 0.235], [0.78, 0.15],
        [0.845, 0.03], [0.845, 0], [0.845, -0.03],
        [0.78, -0.15], [0.55, -0.235], [0.28, -0.19], [0, -0.10],
      ].map(([x, y]) => new THREE.Vector2(x, y));
      const cellGeometry = new THREE.LatheGeometry(PROFILE, 56);
      const cellMaterial = new THREE.MeshStandardMaterial({
        color: 0xd12b4d, roughness: 0.35, metalness: 0.08,
      });

      const COUNT = 44;
      const cells = new THREE.InstancedMesh(cellGeometry, cellMaterial, COUNT);
      const dummy = new THREE.Object3D();
      const state = [];
      for (let i = 0; i < COUNT; i++) {
        state.push({
          x: (Math.random() - 0.5) * 20,
          y: (Math.random() - 0.5) * 12,
          z: -6 + Math.random() * 10,
          rx: Math.random() * Math.PI * 2,
          ry: Math.random() * Math.PI * 2,
          rz: Math.random() * Math.PI * 2,
          spinX: (Math.random() - 0.5) * 0.5,
          spinY: (Math.random() - 0.5) * 0.7,
          bobAmp: 0.25 + Math.random() * 0.5,
          bobSpeed: 0.4 + Math.random() * 0.8,
          phase: Math.random() * Math.PI * 2,
          scale: 0.45 + Math.random() * 0.85,
        });
      }

      // Dust particles for depth.
      const dustCount = 220;
      const dustPositions = new Float32Array(dustCount * 3);
      for (let i = 0; i < dustCount; i++) {
        dustPositions[i * 3] = (Math.random() - 0.5) * 26;
        dustPositions[i * 3 + 1] = (Math.random() - 0.5) * 16;
        dustPositions[i * 3 + 2] = -8 + Math.random() * 12;
      }
      const dustGeometry = new THREE.BufferGeometry();
      dustGeometry.setAttribute("position", new THREE.BufferAttribute(dustPositions, 3));
      const dust = new THREE.Points(dustGeometry, new THREE.PointsMaterial({
        color: 0xe11d48, size: 0.055, transparent: true, opacity: 0.5,
        depthWrite: false, blending: THREE.AdditiveBlending,
      }));

      const group = new THREE.Group();
      group.add(cells);
      group.add(dust);
      scene.add(group);

      // --- interaction state -------------------------------------------------
      const pointer = { x: 0, y: 0, tx: 0, ty: 0 };
      let scrollProgress = 0;
      let pulse = 1;
      const cursorWorld = new THREE.Vector3();
      const REPEL_RADIUS = 3.2;

      window.addEventListener("pointermove", (e) => {
        pointer.tx = (e.clientX / window.innerWidth) * 2 - 1;
        pointer.ty = -(e.clientY / window.innerHeight) * 2 + 1;
      }, { passive: true });

      const pulseUp = () => {
        if (window.gsap) {
          window.gsap.timeline()
            .to({ v: pulse }, {
              v: 1.16, duration: 0.18, ease: "power2.out",
              onUpdate() { pulse = this.targets()[0].v; },
            })
            .to({ v: pulse }, {
              v: 1, duration: 0.9, ease: "elastic.out(1, 0.4)",
              onUpdate() { pulse = this.targets()[0].v; },
            });
        } else {
          pulse = 1.12;
          setTimeout(() => { pulse = 1; }, 220);
        }
      };
      window.addEventListener("pointerdown", pulseUp, { passive: true });

      const updateScrollProgress = () => {
        const max = document.documentElement.scrollHeight - window.innerHeight;
        scrollProgress = max > 0 ? Math.min(window.scrollY / max, 1) : 0;
      };
      window.addEventListener("scroll", updateScrollProgress, { passive: true });
      updateScrollProgress();

      const resize = () => {
        renderer.setSize(window.innerWidth, window.innerHeight);
        camera.aspect = window.innerWidth / window.innerHeight;
        camera.updateProjectionMatrix();
      };
      window.addEventListener("resize", resize);

      const writeInstances = (time) => {
        for (let i = 0; i < COUNT; i++) {
          const s = state[i];
          dummy.position.set(
            s.x + Math.sin(time * s.bobSpeed + s.phase) * s.bobAmp,
            s.y + Math.cos(time * s.bobSpeed * 0.8 + s.phase) * s.bobAmp * 0.8,
            s.z,
          );
          const dx = dummy.position.x - cursorWorld.x;
          const dy = dummy.position.y - cursorWorld.y;
          const dist = Math.hypot(dx, dy);
          if (dist < REPEL_RADIUS && dist > 1e-4) {
            const f = ((REPEL_RADIUS - dist) / REPEL_RADIUS) * 1.5;
            dummy.position.x += (dx / dist) * f;
            dummy.position.y += (dy / dist) * f;
          }
          dummy.rotation.set(
            s.rx + time * s.spinX,
            s.ry + time * s.spinY,
            s.rz,
          );
          dummy.scale.setScalar(s.scale * pulse);
          dummy.updateMatrix();
          cells.setMatrixAt(i, dummy.matrix);
        }
        cells.instanceMatrix.needsUpdate = true;
      };

      const clock = new THREE.Clock();

      if (reducedMotion) {
        // Static frame only: no loop, no listener-driven motion.
        writeInstances(1.2);
        renderer.render(scene, camera);
      } else {
        let running = true;
        let rafId = null;
        const loop = () => {
          if (!running) { rafId = null; return; }
          rafId = requestAnimationFrame(loop);
          const time = clock.getElapsedTime();

          pointer.x += (pointer.tx - pointer.x) * 0.05;
          pointer.y += (pointer.ty - pointer.y) * 0.05;
          camera.position.x = pointer.x * 1.3;
          camera.position.y = pointer.y * 0.9;
          camera.position.z = 15 - scrollProgress * 2.5;
          camera.lookAt(0, 0, 0);

          // Project the cursor onto the z=0 plane so cells can dodge it.
          cursorWorld.set(pointer.x, pointer.y, 0.5).unproject(camera);
          const dir = cursorWorld.sub(camera.position).normalize();
          const dist = -camera.position.z / dir.z;
          cursorWorld.copy(camera.position).add(dir.multiplyScalar(dist));

          group.rotation.y = time * 0.045 + scrollProgress * 1.7;
          dust.rotation.y = time * 0.02;
          writeInstances(time);

          canvas.style.opacity = String(Math.max(1 - scrollProgress * 0.6, 0.25));
          renderer.render(scene, camera);
        };
        const setRunning = (on) => {
          const was = running;
          running = on;
          if (on && !was) { clock.getDelta(); loop(); }
        };
        document.addEventListener("visibilitychange", () => setRunning(!document.hidden));
        loop();
        window.__landingSceneActive = true; // surfaced for smoke checks
      }
    }
  }
} catch (err) {
  // Decorative scene: any failure must leave a fully usable page behind.
  const c = document.getElementById("hero-canvas");
  if (c) c.style.display = "none";
}
