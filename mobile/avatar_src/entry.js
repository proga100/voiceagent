// Alomat 3D avatar — esbuild entry. Bundled to a single CLASSIC script
// (assets/avatar/avatar-bundle.js) because Android WebView blocks ES-module
// fetches from file:// (CORS origin "null"); the GLB is embedded as base64 for
// the same reason. Rebuild: see avatar_src/build.sh.
import * as THREE from './vendor/three/three.module.js';
import { GLTFLoader } from './vendor/three/addons/loaders/GLTFLoader.js';
import glbB64 from './nigora-3d.glb'; // esbuild --loader:.glb=base64

// Bridge back to Flutter (JavaScriptChannel named "AvatarBridge").
const bridge = (msg) => { try { if (window.AvatarBridge) AvatarBridge.postMessage(msg); } catch (_) {} };

const mount = document.getElementById('avatar3d');
// Budget-GPU settings: no MSAA, 1x pixel ratio, low-power hint. At phone
// sizes the visual difference is minor; the fill-rate cost is ~4x lower,
// which keeps the WebView from competing with realtime audio (Mali-G52).
const renderer = new THREE.WebGLRenderer({
  antialias: false, alpha: true, powerPreference: 'low-power',
});
renderer.setPixelRatio(1);
renderer.setClearColor(0x000000, 0);           // fully transparent clear
renderer.outputColorSpace = THREE.SRGBColorSpace;
mount.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const cam = new THREE.PerspectiveCamera(26, 1, 0.01, 100);
scene.add(new THREE.HemisphereLight(0xffffff, 0x9aa7b5, 2.2));
const key = new THREE.DirectionalLight(0xffffff, 2.0); key.position.set(0.6, 1.4, 2); scene.add(key);
const fill = new THREE.DirectionalLight(0xffffff, 0.5); fill.position.set(-1.2, 0.4, 1); scene.add(fill);

const morphMeshes = [];
const bones = {}, base = {};
let baseHipsY = null;
let t = 0, blinkTimer = 2.5;
let driveLoud = 0, dLow = 0.5, dMid = 0.3, dHigh = 0.2;   // targets from audio
let cLoud = 0, cLow = 0.5, cMid = 0.3, cHigh = 0.2;        // smoothed
const clamp = (x) => Math.max(0, Math.min(1, x));

// Camera framing snapshot (set after the GLB loads, re-applied on resize).
let frameCX = 0, frameCZ = 0, frameHeadY = 0, frameZ = 0, framed = false;

function setMorph(name, val) {
  for (const m of morphMeshes) { const i = m.dict[name]; if (i !== undefined) m.mesh.morphTargetInfluences[i] = val; }
}
function addRot(name, dx, dy, dz) {
  const b = bones[name], r = base[name];
  if (b && r) { b.rotation.x = r.x + dx; b.rotation.y = r.y + dy; b.rotation.z = r.z + dz; }
}
// The host calls avatarDrive(loud, lowShare, midShare, highShare); avatarSetOpen = close/interrupt fallback.
window.avatarDrive = (loud, low, mid, high) => { driveLoud = clamp(loud); dLow = low; dMid = mid; dHigh = high; };
window.avatarSetOpen = (v) => { driveLoud = clamp(v || 0); };
window.__morph = (name) => { for (const m of morphMeshes) { const i = m.dict[name]; if (i !== undefined) return m.mesh.morphTargetInfluences[i]; } return null; };

// Renderer + camera track the WebView size; the PiP shrink is just a container resize.
function resize() {
  const w = mount.clientWidth || window.innerWidth;
  const h = mount.clientHeight || window.innerHeight;
  if (!w || !h) return;
  // updateStyle=true (default): the canvas needs CSS w×h too, else it shows
  // at drawing-buffer size (w×pixelRatio) and the viewport crops its corner.
  renderer.setSize(w, h);
  cam.aspect = w / h;
  cam.updateProjectionMatrix();
  if (framed) { cam.position.set(frameCX, frameHeadY + 0.02, frameZ); cam.lookAt(frameCX, frameHeadY, frameCZ); }
}
window.addEventListener('resize', resize);
resize();

// Decode the embedded GLB (base64 -> ArrayBuffer) and parse it directly —
// no fetch, so no file:// CORS to fight.
function glbBuffer() {
  const bin = atob(glbB64);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return buf.buffer;
}

new GLTFLoader().parse(glbBuffer(), '', (gltf) => {
  const root = gltf.scene;
  scene.add(root);
  root.traverse(o => {
    if (o.morphTargetDictionary && o.morphTargetInfluences) morphMeshes.push({ mesh: o, dict: o.morphTargetDictionary });
    if (o.isBone) bones[o.name] = o;
  });
  for (const nm of ['Head','Neck','Spine','Spine1','Spine2','Hips']) if (bones[nm]) base[nm] = bones[nm].rotation.clone();
  if (bones['Hips']) baseHipsY = bones['Hips'].position.y;
  const box = new THREE.Box3().setFromObject(root);
  const size = box.getSize(new THREE.Vector3());
  const c = box.getCenter(new THREE.Vector3());
  const headY = box.max.y - size.y * 0.07;           // frame head + shoulders
  frameCX = c.x; frameCZ = c.z; frameHeadY = headY; frameZ = box.max.z + size.y * 0.62; framed = true;
  cam.position.set(c.x, headY + 0.02, frameZ);
  cam.lookAt(c.x, headY, c.z);
  window.__avatarReady = true;
  bridge('ready');
}, (e) => {
  const msg = (e && e.message) ? e.message : String(e);
  bridge('error:' + msg);
});

const clock = new THREE.Clock();
let frameAcc = 0;
function animate() {
  requestAnimationFrame(animate);
  // Cap rendering at ~30 fps: half the GPU/CPU cost, imperceptible for a
  // talking head. dt-based smoothing below stays correct because the
  // accumulated delta is what gets applied.
  frameAcc += clock.getDelta();
  if (frameAcc < 0.033) return;
  const dt = Math.min(0.05, frameAcc); frameAcc = 0; t += dt;
  cLoud += (driveLoud - cLoud) * Math.min(1, dt * 18);
  const k = Math.min(1, dt * 14);
  cLow += (dLow - cLow) * k; cMid += (dMid - cMid) * k; cHigh += (dHigh - cHigh) * k;
  const L = cLoud;
  setMorph('jawOpen',   L * 0.55);
  setMorph('viseme_aa', clamp(L * (0.35 + cLow * 0.7)));
  setMorph('viseme_O',  clamp(L * cLow * 0.5));
  setMorph('viseme_U',  clamp(L * cLow * 0.25));
  setMorph('viseme_E',  clamp(L * cMid * 0.95));
  setMorph('viseme_I',  clamp(L * cHigh * 0.8));
  setMorph('viseme_SS', clamp(L * cHigh * 0.3));
  setMorph('mouthSmileLeft', 0.10 * (1 - L)); setMorph('mouthSmileRight', 0.10 * (1 - L)); // smile at rest
  blinkTimer -= dt; let blink = 0;
  if (blinkTimer <= 0) { const s = -blinkTimer; if (s < 0.15) blink = Math.sin((s / 0.15) * Math.PI); else blinkTimer = 2 + Math.random() * 3; }
  setMorph('eyeBlinkLeft', blink); setMorph('eyeBlinkRight', blink);
  const sway = Math.sin(t * 0.6), slow = Math.sin(t * 0.23), breath = Math.sin(t * 1.3);
  addRot('Spine2', breath * 0.02, 0, sway * 0.01);
  addRot('Spine1', breath * 0.01, slow * 0.02, 0);
  addRot('Neck', 0, slow * 0.05, sway * 0.015);
  addRot('Head', Math.sin(t * 0.45) * 0.02 + L * 0.05, Math.sin(t * 0.27) * 0.05 + (L > 0.1 ? Math.sin(t * 3.0) * L * 0.04 : 0), sway * 0.02);
  if (bones['Hips'] && baseHipsY != null) bones['Hips'].position.y = baseHipsY + breath * 0.004;
  renderer.render(scene, cam);
}
animate();
