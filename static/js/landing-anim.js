// GSAP choreography for the public landing page: hero intro, scroll reveals
// and animated counters. Runs only if GSAP loaded; without it every element
// is already visible at its final state (all animations use gsap.from).
(function () {
  "use strict";
  var gsap = window.gsap;
  var ScrollTrigger = window.ScrollTrigger;
  if (!gsap || !ScrollTrigger) return;
  gsap.registerPlugin(ScrollTrigger);

  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduced) return;

  try {
    // --- hero + nav intro ---------------------------------------------------
    gsap.timeline({ defaults: { ease: "power3.out" } })
      .from(".landing-nav", { y: -18, opacity: 0, duration: 0.7 })
      .from(".hero-fade", { y: 26, opacity: 0, duration: 0.9, stagger: 0.12 }, "-=0.35");

    // --- scroll reveals -----------------------------------------------------
    gsap.utils.toArray("[data-reveal]").forEach(function (el) {
      gsap.from(el, {
        opacity: 0,
        y: 36,
        duration: 0.9,
        ease: "power3.out",
        delay: parseFloat(el.dataset.delay || "0"),
        scrollTrigger: { trigger: el, start: "top 88%", once: true },
      });
    });

    // --- animated counters (final value is server-rendered in markup) --------
    gsap.utils.toArray("[data-count]").forEach(function (el) {
      var target = parseFloat(el.dataset.count || "0");
      var suffix = el.dataset.suffix || "";
      if (!isFinite(target) || target === 0) return; // "0" stays as rendered
      var obj = { v: 0 };
      gsap.to(obj, {
        v: target,
        duration: 1.6,
        ease: "power2.out",
        scrollTrigger: { trigger: el, start: "top 88%", once: true },
        onUpdate: function () {
          el.textContent = Math.round(obj.v).toLocaleString() + suffix;
        },
      });
    });

    // --- CTA hover lift -----------------------------------------------------
    gsap.utils.toArray(".cta-lift").forEach(function (el) {
      el.addEventListener("mouseenter", function () {
        gsap.to(el, { y: -3, scale: 1.02, duration: 0.25, ease: "power2.out" });
      });
      el.addEventListener("mouseleave", function () {
        gsap.to(el, { y: 0, scale: 1, duration: 0.3, ease: "power2.out" });
      });
    });
  } catch (err) {
    // Decorative only: swallow so the page never breaks.
  }
})();
