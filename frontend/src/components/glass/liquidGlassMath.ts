/**
 * Liquid-glass displacement math (Snell's law + convex squircle bezel).
 * Based on the refraction model from:
 * https://www.samuelbreuel.com/articles/liquid-glass-in-the-browser
 */

export type SurfaceKind = "convexSquircle" | "convexCircle";

export interface GlassMapOptions {
  width: number;
  height: number;
  radius: number;
  bezelWidth: number;
  thickness: number;
  ior?: number;
  surface?: SurfaceKind;
}

export interface GlassMaps {
  displacementUrl: string;
  specularUrl: string;
  scale: number;
}

const IOR_AIR = 1;
const DEFAULT_IOR_GLASS = 1.5;

function convexSquircle(t: number): number {
  const clamped = Math.max(0, Math.min(1, t));
  return (1 - (1 - clamped) ** 4) ** 0.25;
}

function convexCircle(t: number): number {
  const clamped = Math.max(0, Math.min(1, t));
  return (1 - (1 - clamped) ** 2) ** 0.5;
}

function surfaceFn(kind: SurfaceKind): (t: number) => number {
  return kind === "convexCircle" ? convexCircle : convexSquircle;
}

function surfaceDerivative(f: (t: number) => number, t: number): number {
  const delta = 0.001;
  return (f(t + delta) - f(t - delta)) / (2 * delta);
}

/** Signed distance to rounded-rect boundary (negative inside). */
export function sdfRoundedRect(
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
): number {
  const cx = x - width / 2;
  const cy = y - height / 2;
  const bx = width / 2 - radius;
  const by = height / 2 - radius;
  const qx = Math.abs(cx) - bx;
  const qy = Math.abs(cy) - by;
  const outside = Math.hypot(Math.max(qx, 0), Math.max(qy, 0));
  const inside = Math.min(Math.max(qx, qy), 0);
  return outside + inside - radius;
}

function gradientSdf(
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
): { x: number; y: number } {
  const e = 1;
  const dx = sdfRoundedRect(x + e, y, width, height, radius)
    - sdfRoundedRect(x - e, y, width, height, radius);
  const dy = sdfRoundedRect(x, y + e, width, height, radius)
    - sdfRoundedRect(x, y - e, width, height, radius);
  const len = Math.hypot(dx, dy) || 1;
  return { x: dx / len, y: dy / len };
}

/**
 * Radial displacement magnitude at normalized bezel distance t ∈ [0, 1].
 */
export function displacementMagnitude(
  t: number,
  thickness: number,
  iorGlass: number,
  surface: SurfaceKind,
): number {
  const f = surfaceFn(surface);
  const height = f(t) * thickness;
  const dfdt = surfaceDerivative(f, t);
  const thetaI = Math.atan(dfdt);
  const sinR = (IOR_AIR / iorGlass) * Math.sin(thetaI);
  if (Math.abs(sinR) > 1) return 0;
  const thetaR = Math.asin(sinR);
  return Math.abs(Math.tan(thetaI - thetaR) * height);
}

export function buildGlassMaps(opts: GlassMapOptions): GlassMaps {
  const {
    width,
    height,
    radius,
    bezelWidth,
    thickness,
    ior = DEFAULT_IOR_GLASS,
    surface = "convexSquircle",
  } = opts;

  const w = Math.max(32, Math.round(width));
  const h = Math.max(32, Math.round(height));
  const bezel = Math.max(4, Math.min(bezelWidth, Math.min(w, h) * 0.35));
  const r = Math.max(4, Math.min(radius, Math.min(w, h) / 2 - 1));

  const dispCanvas = document.createElement("canvas");
  dispCanvas.width = w;
  dispCanvas.height = h;
  const dispCtx = dispCanvas.getContext("2d");
  if (!dispCtx) {
    return { displacementUrl: "", specularUrl: "", scale: 1 };
  }

  const specCanvas = document.createElement("canvas");
  specCanvas.width = w;
  specCanvas.height = h;
  const specCtx = specCanvas.getContext("2d");
  if (!specCtx) {
    return { displacementUrl: "", specularUrl: "", scale: 1 };
  }

  const dispImage = dispCtx.createImageData(w, h);
  const specImage = specCtx.createImageData(w, h);

  let maxMag = 0;
  const vectors: Array<{ ix: number; dx: number; dy: number; spec: number }> = [];

  const light = { x: -0.35, y: -0.85 };
  const lightLen = Math.hypot(light.x, light.y) || 1;
  light.x /= lightLen;
  light.y /= lightLen;

  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const sdf = sdfRoundedRect(x + 0.5, y + 0.5, w, h, r);
      const ix = y * w + x;
      if (sdf >= 0) {
        vectors.push({ ix, dx: 0, dy: 0, spec: 0 });
        continue;
      }
      const distIn = -sdf;
      if (distIn > bezel) {
        vectors.push({ ix, dx: 0, dy: 0, spec: 0 });
        continue;
      }
      const t = distIn / bezel;
      const mag = displacementMagnitude(t, thickness, ior, surface);
      const grad = gradientSdf(x + 0.5, y + 0.5, w, h, r);
      const dx = -grad.x * mag;
      const dy = -grad.y * mag;
      const ndotl = Math.max(0, -grad.x * light.x + -grad.y * light.y);
      const spec = ndotl ** 2.2 * (1 - t * 0.35);
      maxMag = Math.max(maxMag, mag);
      vectors.push({ ix, dx, dy, spec });
    }
  }

  const scale = Math.max(1, maxMag);
  for (const { ix, dx, dy, spec } of vectors) {
    const nx = dx / scale;
    const ny = dy / scale;
    const di = ix * 4;
    dispImage.data[di] = 128 + nx * 127;
    dispImage.data[di + 1] = 128 + ny * 127;
    dispImage.data[di + 2] = 128;
    dispImage.data[di + 3] = 255;

    const si = ix * 4;
    const specVal = Math.round(Math.min(255, spec * 255));
    specImage.data[si] = specVal;
    specImage.data[si + 1] = specVal;
    specImage.data[si + 2] = specVal;
    specImage.data[si + 3] = specVal;
  }

  dispCtx.putImageData(dispImage, 0, 0);
  specCtx.putImageData(specImage, 0, 0);

  return {
    displacementUrl: dispCanvas.toDataURL("image/png"),
    specularUrl: specCanvas.toDataURL("image/png"),
    scale,
  };
}

export function supportsSvgBackdropFilter(): boolean {
  if (typeof window === "undefined") return false;
  const ua = navigator.userAgent;
  const chromium = /Chrome|Chromium|Edg\//.test(ua) && !/Firefox/.test(ua);
  return chromium;
}
