import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { ContactShadows, OrbitControls } from "@react-three/drei";
import { Suspense, useEffect, useLayoutEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import type { Scene } from "../types/scene";
import { activeLighting } from "./lighting";
import { PaperLayer } from "./PaperLayer";
import { useViewer } from "../store/viewerStore";

const TONE: Record<string, THREE.ToneMapping> = {
  none: THREE.NoToneMapping,
  aces: THREE.ACESFilmicToneMapping,
  agx: THREE.AgXToneMapping,
  neutral: THREE.NeutralToneMapping,
};


function Lights({ scene }: { scene: Scene }) {
  const preset = useViewer((s) => s.preset);
  const scale = useViewer((s) => s.lightScale);
  const shadows = useViewer((s) => s.shadows);
  const L = activeLighting(scene, preset);
  const key = useRef<THREE.DirectionalLight>(null);
  const { width, height } = scene.frame;

  useLayoutEffect(() => {
    const light = key.current;
    if (!light) return;
    const t = L.key.target ?? [0, 0, 0];
    light.target.position.set(t[0], t[1], t[2]);
    light.target.updateMatrixWorld();
    const cam = light.shadow.camera as THREE.OrthographicCamera;
    const half = Math.max(width, height) * 0.8;
    Object.assign(cam, { left: -half, right: half, top: half, bottom: -half, near: 0.1, far: 60 });
    cam.updateProjectionMatrix();
  }, [L, width, height]);

  const sh = L.key.shadow ?? {};
  return (
    <>
      {L.ambient.type === "hemisphere" ? (
        <hemisphereLight args={[L.ambient.color, L.ambient.groundColor ?? "#000000", L.ambient.intensity * scale]} />
      ) : (
        <ambientLight color={L.ambient.color} intensity={L.ambient.intensity * scale} />
      )}
      <directionalLight
        ref={key}
        color={L.key.color}
        intensity={L.key.intensity * scale}
        position={L.key.position}
        castShadow={shadows && (L.key.castShadow ?? true)}
        shadow-mapSize={[sh.mapSize ?? 2048, sh.mapSize ?? 2048]}
        shadow-bias={sh.bias ?? -0.0005}
        shadow-normalBias={sh.normalBias ?? 0.02}
        shadow-radius={sh.radius ?? 4}
      />
      {L.rim && <directionalLight color={L.rim.color} intensity={L.rim.intensity * scale} position={L.rim.position} />}
    </>
  );
}

/** Orthographic camera fitted to the paper frame, with limited orbit and zoom from scene.camera. */
function CameraRig({ scene }: { scene: Scene }) {
  const { camera, size } = useThree();
  const controls = useRef<OrbitControlsImpl>(null);
  const resetToken = useViewer((s) => s.resetToken);
  const c = scene.camera;

  const fitZoom = useMemo(() => {
    const margin = 0.92;
    return Math.min(size.width / scene.frame.width, size.height / scene.frame.height) * margin * (c.zoom ?? 1);
  }, [size.width, size.height, scene.frame.width, scene.frame.height, c.zoom]);

  useEffect(() => {
    camera.position.set(...c.position);
    camera.near = c.near ?? 0.1;
    camera.far = c.far ?? 100;
    if (camera instanceof THREE.OrthographicCamera) camera.zoom = fitZoom;
    camera.updateProjectionMatrix();
    controls.current?.target.set(...c.target);
    controls.current?.update();
  }, [camera, c, fitZoom, resetToken]);

  const polar = c.maxPolarAngle ?? 0.35;
  const azimuth = c.maxAzimuthAngle ?? 0.5;
  return (
    <OrbitControls
      ref={controls}
      makeDefault
      enablePan={false}
      enableRotate={scene.interaction.orbit !== false}
      enableZoom={scene.interaction.zoom !== false}
      enableDamping
      dampingFactor={0.08}
      minPolarAngle={Math.PI / 2 - polar}
      maxPolarAngle={Math.PI / 2 + polar}
      minAzimuthAngle={-azimuth}
      maxAzimuthAngle={azimuth}
      minZoom={fitZoom * (c.minZoom ?? 0.5)}
      maxZoom={fitZoom * (c.maxZoom ?? 3)}
      minDistance={c.type === "perspective" ? c.position[2] / (c.maxZoom ?? 3) : undefined}
      maxDistance={c.type === "perspective" ? c.position[2] / (c.minZoom ?? 0.5) : undefined}
    />
  );
}

/**
 * Clip rendering to the back layer's rectangle as seen by the current camera (screen-space
 * scissor). The camera orbits around the back layer, so this rectangle barely moves, and the
 * back layer is completed everywhere by papercraft_segment: nothing inside can be empty, and
 * nearer layers shifted past the image edge are cut off like by a shadow-box frame.
 */
function FrameClip({ scene }: { scene: Scene }) {
  const corners = useMemo(() => {
    const w = scene.frame.width / 2;
    const h = scene.frame.height / 2;
    const z = Math.min(...scene.layers.map((l) => l.depth));
    return [new THREE.Vector3(-w, -h, z), new THREE.Vector3(w, -h, z), new THREE.Vector3(-w, h, z), new THREE.Vector3(w, h, z)];
  }, [scene]);
  const p = useMemo(() => new THREE.Vector3(), []);
  useFrame(({ gl, scene: root, camera, size }) => {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const c of corners) {
      p.copy(c).project(camera);
      const x = ((p.x + 1) / 2) * size.width;
      const y = ((p.y + 1) / 2) * size.height;
      x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
    }
    gl.setScissorTest(false);
    if (root.background instanceof THREE.Color) gl.setClearColor(root.background);
    gl.clear();
    gl.setScissor(Math.floor(x0), Math.floor(y0), Math.ceil(x1 - x0), Math.ceil(y1 - y0));
    gl.setScissorTest(true);
    gl.render(root, camera);
    gl.setScissorTest(false);
  }, 1);
  return null;
}

export function PaperScene({ scene, baseUrl }: { scene: Scene; baseUrl: string }) {
  const preset = useViewer((s) => s.preset);
  const shadows = useViewer((s) => s.shadows);
  const hidden = useViewer((s) => s.hidden);
  const r = scene.rendering;
  const background = activeLighting(scene, preset).background ?? "#1b1b1f";
  const layers = [...scene.layers].sort((a, b) => a.order - b.order);

  return (
    <Canvas
      orthographic={scene.camera.type === "orthographic"}
      shadows={shadows && r.shadows !== false ? "soft" : false}
      dpr={[1, r.maxPixelRatio ?? 2]}
      gl={{ antialias: r.antialias ?? true }}
      onCreated={({ gl }) => {
        gl.toneMapping = TONE[r.toneMapping ?? "neutral"];
        gl.toneMappingExposure = r.exposure ?? 1;
        gl.outputColorSpace = THREE.SRGBColorSpace;
      }}
      camera={scene.camera.type === "perspective" ? { fov: scene.camera.fov ?? 35, position: scene.camera.position } : undefined}
      style={{ touchAction: "none" }}
    >
      <color attach="background" args={[background]} />
      <Lights scene={scene} />
      {r.clipToFrame !== false && <FrameClip scene={scene} />}
      <CameraRig scene={scene} />
      <Suspense fallback={null}>
        {layers
          .filter((l) => l.visible && !hidden[l.id])
          .map((layer) => (
            <PaperLayer key={layer.id} layer={layer} scene={scene} baseUrl={baseUrl} />
          ))}
      </Suspense>
      {r.contactShadows?.enabled && (
        <ContactShadows
          position={[0, -scene.frame.height / 2, 1]}
          scale={scene.frame.width * 1.2}
          opacity={r.contactShadows.opacity ?? 0.4}
          blur={r.contactShadows.blur ?? 2.5}
        />
      )}
    </Canvas>
  );
}
