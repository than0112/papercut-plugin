/**
 * TypeScript mirror of schemas/scene.schema.json (schemaVersion 1.0).
 * The JSON Schema is authoritative; scene files are validated against it at
 * load time (see scene/loadScene.ts), these types only describe the result.
 */

export type Vec2 = [number, number];
export type Vec3 = [number, number, number];
export type HexColor = string;

export interface CameraSpec {
  type: "orthographic" | "perspective";
  position: Vec3;
  target: Vec3;
  fov?: number;
  zoom?: number;
  minZoom?: number;
  maxZoom?: number;
  near?: number;
  far?: number;
  maxPolarAngle?: number;
  maxAzimuthAngle?: number;
}

export interface GeometrySpec {
  type: "plane" | "shape" | "extrude";
  thickness?: number;
  outline?: string;
  curveSegments?: number;
  bevel?: number;
}

export interface MaterialSpec {
  roughness?: number;
  metalness?: number;
  opacity?: number;
  alphaTest?: number;
  side?: "front" | "double";
  tint?: HexColor;
  emissive?: HexColor;
  emissiveIntensity?: number;
  paperTexture?: { enabled?: boolean; strength?: number; scale?: number };
}

export interface Provenance {
  source: "auto" | "manual" | "preview";
  method?: string;
  confidence?: number;
  inpainted?: boolean;
  editedAt?: string;
}

export interface LayerSpec {
  id: string;
  name: string;
  role: "sky" | "background" | "midground" | "foreground" | "detail";
  order: number;
  asset: string;
  mask?: string;
  depth: number;
  offset?: Vec2;
  scale?: number;
  geometry: GeometrySpec;
  material?: MaterialSpec;
  parallaxFactor?: number;
  castShadow?: boolean;
  receiveShadow?: boolean;
  visible: boolean;
  provenance: Provenance;
}

export interface LightingSpec {
  preset?: string;
  ambient: { type?: "ambient" | "hemisphere"; color: HexColor; groundColor?: HexColor; intensity: number };
  key: {
    color: HexColor;
    colorTemperatureK?: number;
    intensity: number;
    position: Vec3;
    target?: Vec3;
    castShadow?: boolean;
    shadow?: { mapSize?: 512 | 1024 | 2048 | 4096; bias?: number; normalBias?: number; radius?: number };
  };
  rim?: { color: HexColor; intensity: number; position: Vec3 };
  background?: HexColor;
}

export interface RenderingSpec {
  toneMapping?: "none" | "aces" | "agx" | "neutral";
  exposure?: number;
  outputColorSpace?: "srgb";
  antialias?: boolean;
  shadows?: boolean;
  maxPixelRatio?: number;
  contactShadows?: { enabled?: boolean; opacity?: number; blur?: number };
}

export interface InteractionSpec {
  orbit?: boolean;
  zoom?: boolean;
  parallax?: { enabled?: boolean; strength?: number };
  explodedView?: { enabled?: boolean; spacing?: number };
}

export interface Scene {
  schemaVersion: "1.0";
  sceneId: string;
  name: string;
  mode: "layered" | "preview";
  generator?: { name?: string; version?: string; createdAt?: string; updatedAt?: string; revision?: number };
  source: { image: string; width: number; height: number; sha256?: string };
  units: { pixelsPerUnit: number };
  frame: { width: number; height: number };
  camera: CameraSpec;
  layers: LayerSpec[];
  lighting: LightingSpec;
  lightingPresets?: Record<string, LightingSpec>;
  rendering: RenderingSpec;
  interaction: InteractionSpec;
}
