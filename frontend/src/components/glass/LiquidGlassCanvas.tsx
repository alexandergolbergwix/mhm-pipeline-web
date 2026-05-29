/**
 * Liquid-glass background — full-screen R3F canvas behind the UI.
 *
 * Each orb is a low-poly icosphere with @react-three/drei's
 * MeshTransmissionMaterial (an extension of MeshPhysicalMaterial adding
 * thickness, distortion, chromaticAberration — the trick that gives
 * Apple's "Liquid Glass" its real-time refraction). The orbs drift on
 * Perlin-ish curves so what's behind them keeps morphing — without us
 * having to capture DOM content into a texture, the refracted "world"
 * is the scene's own background gradient + the other orbs.
 *
 * pointer-events: none — the UI sits ABOVE the canvas; the canvas only
 * provides ambient depth.
 *
 * prefers-reduced-motion (and ?glass=off URL param) → no-op render so
 * weaker devices fall back to the CSS-only frosted-glass layer that's
 * already in index.css.
 */

import { Canvas, useFrame } from "@react-three/fiber";
import { MeshTransmissionMaterial } from "@react-three/drei";
import { Suspense, useMemo, useRef } from "react";
import * as THREE from "three";

interface OrbSpec {
  color: string;
  scale: number;
  // base position
  x: number; y: number; z: number;
  // drift speed (rad/s on each axis)
  sx: number; sy: number; sz: number;
  // amplitude
  ax: number; ay: number; az: number;
  phase: number;
}

const ORBS: OrbSpec[] = [
  { color: "#77cce5", scale: 2.1,  x: -2.6, y:  0.9, z: -0.6,
    sx: 0.12, sy: 0.18, sz: 0.07, ax: 0.6,  ay: 0.4, az: 0.3,  phase: 0    },
  { color: "#004027", scale: 2.6,  x:  2.4, y: -0.8, z:  0.4,
    sx: 0.09, sy: 0.13, sz: 0.06, ax: 0.7,  ay: 0.5, az: 0.4,  phase: 1.6  },
  { color: "#ffffff", scale: 1.4,  x:  0.2, y:  1.8, z: -1.0,
    sx: 0.16, sy: 0.10, sz: 0.08, ax: 0.5,  ay: 0.6, az: 0.3,  phase: 3.0  },
  { color: "#77cce5", scale: 1.7,  x: -0.8, y: -1.6, z:  0.8,
    sx: 0.14, sy: 0.20, sz: 0.05, ax: 0.4,  ay: 0.7, az: 0.5,  phase: 4.2  },
];


export function LiquidGlassCanvas() {
  // Respect reduced-motion + a manual opt-out (?glass=off) so demo
  // captures can use the plain CSS path on weak machines.
  if (typeof window !== "undefined") {
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const off    = new URLSearchParams(window.location.search).get("glass") === "off";
    if (reduce || off) return null;
  }

  return (
    <div
      aria-hidden
      style={{
        position: "fixed", inset: 0, pointerEvents: "none",
        zIndex: 0, mixBlendMode: "screen",
      }}
    >
      <Canvas
        dpr={[1, 1.5]}                /* cap retina cost on integrated GPUs */
        camera={{ position: [0, 0, 6], fov: 50 }}
        gl={{ antialias: false, alpha: true }}
        frameloop="always"
      >
        <ambientLight intensity={0.4} />
        <directionalLight position={[3, 4, 5]} intensity={0.7} />
        <Suspense fallback={null}>
          {ORBS.map((o, i) => <Orb key={i} {...o} />)}
        </Suspense>
      </Canvas>
    </div>
  );
}


function Orb(spec: OrbSpec) {
  const ref = useRef<THREE.Mesh>(null!);
  const base = useMemo(() => new THREE.Vector3(spec.x, spec.y, spec.z), [spec]);

  useFrame(({ clock }) => {
    const t = clock.getElapsedTime() + spec.phase;
    if (!ref.current) return;
    ref.current.position.set(
      base.x + Math.sin(t * spec.sx) * spec.ax,
      base.y + Math.sin(t * spec.sy) * spec.ay,
      base.z + Math.sin(t * spec.sz) * spec.az,
    );
    // Slow tumble so specular highlights catch.
    ref.current.rotation.y += 0.0015;
    ref.current.rotation.x += 0.001;
  });

  return (
    <mesh ref={ref} scale={spec.scale}>
      <icosahedronGeometry args={[1, 4]} />
      {/* MeshTransmissionMaterial — the heart of the liquid-glass effect.
          Real refraction via `transmission`, edge color via `attenuationColor`,
          subtle prism-fringe via `chromaticAberration`, gentle organic distortion. */}
      <MeshTransmissionMaterial
        transmission={1}
        roughness={0.08}
        thickness={1.6}
        ior={1.42}
        chromaticAberration={0.04}
        distortion={0.25}
        distortionScale={0.35}
        temporalDistortion={0.15}
        backside
        attenuationDistance={3}
        attenuationColor={spec.color}
        color={spec.color}
        resolution={256}
        anisotropy={0.05}
      />
    </mesh>
  );
}
