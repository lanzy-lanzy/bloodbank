/* ==========================================================================
   Aether — liquid glass landing page (entry module)
   1. ui.js   : cursor lighting, tilt cards, GSAP scroll timeline, nav
   2. scene.js: Three.js liquid-glass blob with a custom GLSL shader
                that reacts to scroll velocity and the pointer
   ========================================================================== */

/* Flag for CSS: enables reveal start-states (content stays visible w/o JS) */
document.documentElement.classList.add("js");

gsap.registerPlugin(ScrollTrigger);

/* ------------------------------------------------------------------ *
 *  UI module — owns pointer/scroll interaction state (ui.js)
 * ------------------------------------------------------------------ */
import { pointer, scroll } from "./ui.js";

/* ------------------------------------------------------------------ *
 *  Three.js scene — dynamic import so a WebGL failure never kills UI
 * ------------------------------------------------------------------ */
import * as THREE from "three";

const canvas = document.getElementById("scene");

let renderer;
try {
  renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: true,                       // CSS glow shows through
    powerPreference: "high-performance",
  });
} catch {
  canvas.style.display = "none";       // graceful fallback: pure CSS mood
  throw new Error("WebGL unavailable — falling back to CSS backdrop.");
}

renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2)); // perf cap
renderer.setSize(innerWidth, innerHeight);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, innerWidth / innerHeight, 0.1, 100);
camera.position.z = 6;

/* ---------- Liquid-glass shader ---------- *
 * Vertex: 3 octaves of classic simplex noise displace the sphere along
 * its normal  ->  "liquid" wobble, amplified by uDistort.
 * Fragment: fresnel rim + wrap diffuse + Blinn specular + a second
 * fresnel-heavy "thin glass" shell  ->  frosted-glass look.           */

const NOISE_GLSL = /* glsl */ `
  // -- Ashima simplex 3D noise (public domain) --
  vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
  vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
  vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
  vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
  float snoise(vec3 v){
    const vec2 C=vec2(1.0/6.0,2.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
    vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
    vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g;
    vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
    vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy;
    i=mod289(i);
    vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))
      +i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
    float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx;
    vec4 j=p-49.0*floor(p*ns.z*ns.z);
    vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);
    vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy;
    vec4 h=1.0-abs(x)-abs(y); vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
    vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
    vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
    vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y);
    vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
    vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
    p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
    vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0);
    m=m*m;
    return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
  }
  float fbm(vec3 p){
    return 0.50*snoise(p) + 0.30*snoise(p*2.1+7.0) + 0.20*snoise(p*4.3+11.0);
  }
`;

const uniforms = {
  uTime:    { value: 0 },
  uDistort: { value: 0.32 },   // base wobble; scroll velocity pushes it up
  uPointer: { value: new THREE.Vector2(0.5, 0.5) },
  uColorA:  { value: new THREE.Color("#7c5cff") },
  uColorB:  { value: new THREE.Color("#22d3ee") },
  uColorC:  { value: new THREE.Color("#f472b6") },
};

/* Vertex: displaced sphere; recompute normal from two neighbour samples */
const vertexShader = /* glsl */ `
  uniform float uTime;
  uniform float uDistort;
  varying vec3 vNormal;
  varying vec3 vViewDir;
  varying float vNoise;
  ${NOISE_GLSL}
  float displace(vec3 p){
    return fbm(p * 1.6 + vec3(0.0, 0.0, uTime * 0.25)) * uDistort;
  }
  void main(){
    float d = displace(position);
    vNoise = d;
    vec3 pos = normalize(position) * (1.0 + d);
    // approximate normal via two tangent samples
    vec3 t1 = normalize(cross(normal, vec3(0.0, 1.0, 0.0) + 0.001));
    vec3 t2 = normalize(cross(normal, t1));
    float e = 0.08;
    vec3 pA = normalize(position + t1 * e); pA *= 1.0 + displace(pA);
    vec3 pB = normalize(position + t2 * e); pB *= 1.0 + displace(pB);
    vNormal = normalize(cross(pA - pos, pB - pos));
    vec4 mv = modelViewMatrix * vec4(pos, 1.0);
    vViewDir = normalize(-mv.xyz);
    gl_Position = projectionMatrix * mv;
  }
`;

/* Fragment: fresnel rim + pointer-lit specular + glass tint mix */
const fragmentShader = /* glsl */ `
  uniform float uTime;
  uniform vec2 uPointer;
  uniform vec3 uColorA;
  uniform vec3 uColorB;
  uniform vec3 uColorC;
  varying vec3 vNormal;
  varying vec3 vViewDir;
  varying float vNoise;
  void main(){
    vec3 N = normalize(vNormal);
    vec3 V = normalize(vViewDir);
    // Fresnel: edges glow like the rim of thick glass
    float fres = pow(1.0 - max(dot(N, V), 0.0), 2.6);
    // Two lights: fixed key + cursor-tracking point light
    vec3 L1 = normalize(vec3(-0.6, 0.8, 0.9));
    vec3 Lp = normalize(vec3((uPointer - 0.5) * 2.4, 1.2));
    float spec = pow(max(dot(reflect(-L1, N), V), 0.0), 38.0)
               + pow(max(dot(reflect(-Lp, N), V), 0.0), 60.0) * 1.4;
    float diff = 0.5 + 0.5 * dot(N, L1);
    // body colour drifts with noise + time
    vec3 tint = mix(uColorA, uColorB, 0.5 + 0.5 * sin(vNoise * 7.0 + uTime * 0.3));
    tint = mix(tint, uColorC, fres * 0.55);
    vec3 col = tint * diff * 0.55          // frosted body
             + vec3(0.85, 0.92, 1.0) * fres * 0.9   // rim light
             + vec3(1.0) * spec;                    // specular dots
    float alpha = 0.30 + fres * 0.55 + spec * 0.5;
    gl_FragColor = vec4(col, clamp(alpha, 0.0, 0.9));
  }
`;

const blobMat = new THREE.ShaderMaterial({
  vertexShader, fragmentShader, uniforms,
  transparent: true, depthWrite: false,
});
const blob = new THREE.Mesh(new THREE.IcosahedronGeometry(1.55, 48), blobMat);
blob.position.set(1.6, -0.1, 0);           // sits behind hero's right side
scene.add(blob);

/* Thin outer shell: extra fresnel = "there is another glass surface" */
const shell = new THREE.Mesh(
  new THREE.IcosahedronGeometry(1.55, 24),
  new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, side: THREE.BackSide,
    uniforms,
    vertexShader: /* glsl */ `
      varying vec3 vN; varying vec3 vV;
      void main(){
        vec4 mv = modelViewMatrix * vec4(position * 1.18, 1.0);
        vN = normalize(normalMatrix * normal);
        vV = normalize(-mv.xyz);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: /* glsl */ `
      varying vec3 vN; varying vec3 vV;
      void main(){
        float f = pow(1.0 - max(dot(normalize(vN), normalize(vV)), 0.0), 3.2);
        gl_FragColor = vec4(vec3(0.75, 0.85, 1.0), f * 0.35);
      }`,
  }),
);
shell.position.copy(blob.position);
scene.add(shell);

/* Small orbiting glass chips catch the eye and add parallax depth */
const chipGeo = new THREE.IcosahedronGeometry(0.16, 12);
const chips = [];
for (let i = 0; i < 3; i++) {
  const m = new THREE.Mesh(chipGeo, blobMat.clone());
  m.material.uniforms = uniforms;             // share clock/pointer uniforms
  scene.add(m);
  chips.push({ mesh: m, r: 2.4 + i * 0.5, speed: 0.22 + i * 0.07, phase: i * 2.1 });
}

/* ---------- Render loop (pauses when hidden — battery friendly) ---------- */
const clock = new THREE.Clock();
let running = true;
let smoothVel = 0;

function tick() {
  if (!running) return;
  requestAnimationFrame(tick);

  const t = clock.getElapsedTime();
  uniforms.uTime.value = t;
  uniforms.uPointer.value.set(pointer.x, 1 - pointer.y);

  // ease scroll velocity into the shader's distortion (liquid slosh)
  smoothVel += (Math.min(Math.abs(scroll.velocity), 4) - smoothVel) * 0.06;
  uniforms.uDistort.value = 0.32 + smoothVel * 0.22;

  // pointer pulls the blob subtly; scroll spins + drifts it down
  blob.rotation.y = t * 0.12 + pointer.x * 0.5;
  blob.rotation.x = Math.sin(t * 0.2) * 0.12 + (pointer.y - 0.5) * 0.4;
  shell.rotation.copy(blob.rotation);
  blob.position.y = -0.1 - smoothVel * 0.25;
  shell.position.y = blob.position.y;

  chips.forEach((c, i) => {
    const a = t * c.speed + c.phase;
    c.mesh.position.set(
      blob.position.x + Math.cos(a) * c.r,
      blob.position.y + Math.sin(a * 0.8) * 0.9,
      Math.sin(a) * 0.8,
    );
  });

  renderer.render(scene, camera);
}
tick();

document.addEventListener("visibilitychange", () => {
  const wasRunning = running;
  running = !document.hidden;
  if (running && !wasRunning) tick();
});

/* ---------- Resize ---------- */
let resizeTimer;
addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    camera.aspect = innerWidth / innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
    ScrollTrigger.refresh();
  }, 150);
});
