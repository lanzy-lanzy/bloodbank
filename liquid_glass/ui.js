/* ==========================================================================
   ui.js — DOM-side interactions + shared interaction state
   • pointer / scroll objects: written here, read by the Three.js loop
   • cursor lighting on glass panels (--mx/--my custom props)
   • 3D tilt cards
   • GSAP ScrollTrigger: reveals, layered parallax, nav condense, velocity
   • form micro-feedback
   ========================================================================== */

/* Shared interaction state — single source of truth, imported by main.js */
export const pointer = { x: 0.5, y: 0.5 };   // normalised viewport coords
export const scroll = { velocity: 0 };        // viewport-units/s, eased in shader

const prefersReduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const finePointer = matchMedia("(pointer: fine)").matches;

/* ---------- 1. Pointer tracking (rAF-throttled, no layout reads) ---------- */
let pending = false;
let rawX = 0.5, rawY = 0.5;

addEventListener("pointermove", (e) => {
  rawX = e.clientX / innerWidth;
  rawY = e.clientY / innerHeight;
  if (pending) return;
  pending = true;
  requestAnimationFrame(() => {
    pending = false;
    pointer.x = rawX;
    pointer.y = rawY;
    // global glow follows the cursor (sits under the noise layer)
    document.getElementById("glow").style.setProperty("--px", `${rawX * 100}%`);
    document.getElementById("glow").style.setProperty("--py", `${rawY * 100}%`);
  });
}, { passive: true });

/* ---------- 2. Per-card cursor spotlight + tilt ---------- */
if (finePointer) {
  document.querySelectorAll(".glass").forEach((card) => {
    card.addEventListener("pointermove", (e) => {
      const r = card.getBoundingClientRect();   // cached element-local math
      const x = (e.clientX - r.left) / r.width;
      const y = (e.clientY - r.top) / r.height;
      card.style.setProperty("--mx", `${x * 100}%`);
      card.style.setProperty("--my", `${y * 100}%`);
      if (prefersReduced || !card.classList.contains("tilt")) return;
      // gentle tilt: max 6deg, springy return handled by transition
      card.style.transform =
        `perspective(900px) rotateY(${(x - 0.5) * 6}deg) rotateX(${(0.5 - y) * 6}deg)`;
    });
    card.addEventListener("pointerleave", () => {
      card.style.transform = "";
      card.style.setProperty("--mx", "50%");
      card.style.setProperty("--my", "0%");
    });
  });
}

/* ---------- 3. GSAP scroll animations ---------- */
/* Hero entrance timeline */
gsap.timeline({ defaults: { ease: "power3.out" } })
  .from(".glass--nav", { y: -70, opacity: 0, duration: 0.8 })
  .from("#hero .eyebrow", { y: 24, opacity: 0, duration: 0.6 }, "-=0.4")
  .from("h1", { y: 44, opacity: 0, duration: 0.9 }, "-=0.35")
  .from(".lede", { y: 28, opacity: 0, duration: 0.7 }, "-=0.6")
  .from(".hero-actions .btn", { y: 22, opacity: 0, stagger: 0.1, duration: 0.55 }, "-=0.5")
  .from(".hero-stats > li", { y: 18, opacity: 0, stagger: 0.08, duration: 0.5 }, "-=0.4")
  .from(".hero-panel", { x: 60, opacity: 0, duration: 0.9 }, "-=0.9");

/* Reveal-on-scroll for everything tagged [data-reveal] */
gsap.utils.toArray("[data-reveal]").forEach((el) => {
  gsap.to(el, {
    opacity: 1, y: 0, duration: 1, ease: "power2.out",
    scrollTrigger: { trigger: el, start: "top 86%" },
  });
  // start state lives in JS so no-JS users see content immediately
  gsap.set(el, { y: prefersReduced ? 0 : 46 });
});

/* Layered parallax: each [data-speed] translates at its own rate */
gsap.utils.toArray("[data-speed]").forEach((el) => {
  gsap.to(el, {
    yPercent: (i, t) => (1 - parseFloat(t.dataset.speed)) * 120,
    ease: "none",
    scrollTrigger: {
      trigger: el.closest("section"), start: "top bottom", end: "bottom top",
      scrub: 1,
    },
  });
});

/* Nav condenses after the hero */
ScrollTrigger.create({
  start: "top -80",
  end: 99999,
  toggleClass: { className: "nav--compact", targets: ".glass--nav" },
});

/* Feed normalised scroll velocity to the shader (main.js reads this) */
ScrollTrigger.create({
  onUpdate: (self) => { scroll.velocity = self.getVelocity() / 500 },
});

/* Section titles drift + fade slightly as they pass, for depth feel */
gsap.utils.toArray(".section-title").forEach((title) => {
  gsap.to(title, {
    opacity: 0.25, scale: 0.96, ease: "none",
    scrollTrigger: { trigger: title, start: "top 30%", end: "top -10%", scrub: true },
  });
});

/* ---------- 4. CTA form micro-feedback ---------- */
const form = document.querySelector(".cta-form");
if (form) {
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const input = form.querySelector("input");
    const note = document.querySelector(".form-note");
    if (!input.value || !input.checkValidity()) {
      gsap.fromTo(form, { x: -8 }, { x: 0, duration: 0.5, ease: "elastic.out(1, 0.3)" });
      note.textContent = "That email doesn't look right — try again.";
      return;
    }
    note.textContent = "You're on the list. Welcome to the refraction. ✦";
    gsap.fromTo(note, { opacity: 0, y: 8 }, { opacity: 1, y: 0, duration: 0.5 });
    gsap.to(".cta-card", { scale: 1.015, duration: 0.3, yoyo: true, repeat: 1 });
    input.value = "";
  });
}

/* Smooth anchor scrolling that respects reduced-motion */
document.querySelectorAll('a[href^="#"]').forEach((a) => {
  a.addEventListener("click", (e) => {
    const target = document.querySelector(a.getAttribute("href"));
    if (!target) return;
    e.preventDefault();
    target.scrollIntoView({ behavior: prefersReduced ? "auto" : "smooth", block: "start" });
  });
});
