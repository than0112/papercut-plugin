import * as THREE from "three";

let cached: THREE.CanvasTexture | null = null;

/**
 * Procedural paper-fibre bump texture (shared, generated once).
 * Used as a bumpMap with a small scale so it adds tactile grain under the
 * lights without changing the colours of the source art.
 */
export function paperGrainTexture(): THREE.CanvasTexture {
  if (cached) return cached;
  const size = 256;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  const img = ctx.createImageData(size, size);
  let seed = 1337;
  const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let i = 0; i < size * size; i++) {
    const v = 128 + (rand() - 0.5) * 70;
    img.data[i * 4] = img.data[i * 4 + 1] = img.data[i * 4 + 2] = v;
    img.data[i * 4 + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  // a few long soft fibres
  ctx.globalAlpha = 0.08;
  ctx.strokeStyle = "#fff";
  for (let i = 0; i < 120; i++) {
    ctx.beginPath();
    const x = rand() * size;
    const y = rand() * size;
    ctx.moveTo(x, y);
    ctx.quadraticCurveTo(x + rand() * 30 - 15, y + rand() * 30 - 15, x + rand() * 40 - 20, y + rand() * 40 - 20);
    ctx.stroke();
  }
  cached = new THREE.CanvasTexture(canvas);
  cached.wrapS = cached.wrapT = THREE.RepeatWrapping;
  cached.colorSpace = THREE.NoColorSpace;
  return cached;
}
