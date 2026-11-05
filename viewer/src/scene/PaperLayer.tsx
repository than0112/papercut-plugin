import { useFrame, useThree } from "@react-three/fiber";
import { useTexture } from "@react-three/drei";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import type { LayerSpec, Scene } from "../types/scene";
import { assetUrl } from "./loadScene";
import { paperGrainTexture } from "./paperTexture";
import { useViewer } from "../store/viewerStore";

interface Props {
  layer: LayerSpec;
  scene: Scene;
  baseUrl: string;
}

/**
 * One paper layer: a full-frame alpha-cutout plane (geometry "plane").
 * "shape" / "extrude" geometry (mask outlines with thickness) arrive with the
 * segmentation pipeline; until then they render as planes with a console note.
 */
export function PaperLayer({ layer, scene, baseUrl }: Props) {
  const group = useRef<THREE.Group>(null);
  const texture = useTexture(assetUrl(baseUrl, layer.asset));
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;

  const explode = useViewer((s) => s.explode);
  const parallaxOn = useViewer((s) => s.parallax);
  const selected = useViewer((s) => s.selected === layer.id);
  const pointer = useThree((s) => s.pointer);

  const m = layer.material ?? {};
  const paper = m.paperTexture ?? {};
  const grain = useMemo(() => {
    if (paper.enabled === false) return null;
    const t = paperGrainTexture().clone();
    const s = paper.scale ?? 8;
    t.repeat.set(s * (scene.frame.width / scene.frame.height), s);
    t.needsUpdate = true;
    return t;
  }, [paper.enabled, paper.scale, scene.frame.width, scene.frame.height]);

  if (layer.geometry.type !== "plane") {
    console.info(`[papercraft] layer ${layer.id}: geometry '${layer.geometry.type}' not implemented yet, using plane`);
  }

  const scale = layer.scale ?? 1;
  const [ox, oy] = layer.offset ?? [0, 0];
  const spacing = scene.interaction.explodedView?.enabled === false ? 0 : (scene.interaction.explodedView?.spacing ?? 1.5);
  const strength = scene.interaction.parallax?.enabled === false ? 0 : (scene.interaction.parallax?.strength ?? 0.15);
  const factor = layer.parallaxFactor ?? 1;

  useFrame((_, dt) => {
    const g = group.current;
    if (!g) return;
    const k = 1 - Math.exp(-dt * 8); // frame-rate independent smoothing
    const px = parallaxOn ? pointer.x * strength * factor : 0;
    const py = parallaxOn ? pointer.y * strength * factor * 0.6 : 0;
    const z = layer.depth + explode * layer.order * spacing;
    g.position.x += (ox + px - g.position.x) * k;
    g.position.y += (oy + py - g.position.y) * k;
    g.position.z += (z - g.position.z) * k;
  });

  return (
    <group ref={group} position={[ox, oy, layer.depth]}>
      <mesh castShadow={layer.castShadow ?? true} receiveShadow={layer.receiveShadow ?? true} scale={scale}>
        <planeGeometry args={[scene.frame.width, scene.frame.height]} />
        <meshStandardMaterial
          map={texture}
          color={m.tint ?? "#ffffff"}
          roughness={m.roughness ?? 0.95}
          metalness={m.metalness ?? 0}
          alphaTest={m.alphaTest ?? 0.5}
          opacity={m.opacity ?? 1}
          transparent={(m.opacity ?? 1) < 1}
          side={m.side === "front" ? THREE.FrontSide : THREE.DoubleSide}
          emissive={selected ? "#3a6cff" : (m.emissive ?? "#000000")}
          emissiveIntensity={selected ? 0.25 : (m.emissiveIntensity ?? 0)}
          bumpMap={grain}
          bumpScale={grain ? (paper.strength ?? 0.08) : 0}
        />
      </mesh>
    </group>
  );
}
