import type { LightingSpec, Scene } from "../types/scene";

/** The lighting in effect: a named preset if one is selected, else the scene's own lighting. */
export function activeLighting(scene: Scene, preset: string | null): LightingSpec {
  return (preset && scene.lightingPresets?.[preset]) || scene.lighting;
}
