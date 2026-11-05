import Ajv2020 from "ajv/dist/2020";
import sceneSchema from "@schemas/scene.schema.json";
import type { Scene } from "../types/scene";

const ajv = new Ajv2020({ allErrors: true, strict: false, validateFormats: false });
const validateScene = ajv.compile<Scene>(sceneSchema);

export class SceneLoadError extends Error {
  constructor(
    message: string,
    readonly details: string[] = [],
  ) {
    super(message);
  }
}

export interface LoadedScene {
  scene: Scene;
  /** Absolute URL of the directory containing scene.json; asset paths resolve against it. */
  baseUrl: string;
}

/** Fetch a scene.json, validate it against the shared JSON Schema, and resolve its base URL. */
export async function loadScene(url: string): Promise<LoadedScene> {
  const absolute = new URL(url, window.location.href);
  let response: Response;
  try {
    response = await fetch(absolute, { cache: "no-store" });
  } catch (err) {
    throw new SceneLoadError(`Could not fetch ${absolute.pathname}: ${String(err)}`);
  }
  if (!response.ok) throw new SceneLoadError(`Could not fetch ${absolute.pathname} (HTTP ${response.status})`);

  const type = response.headers.get("content-type") ?? "";
  if (!type.includes("json")) {
    throw new SceneLoadError(`${absolute.pathname} was not found (server returned ${type.split(";")[0] || "no content type"}, not JSON)`);
  }
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new SceneLoadError(`${absolute.pathname} is not valid JSON`);
  }
  if (!validateScene(data)) {
    const details = (validateScene.errors ?? []).map((e) => `${e.instancePath || "(root)"}: ${e.message ?? "invalid"}`);
    throw new SceneLoadError("scene.json failed schema validation", details);
  }
  return { scene: data, baseUrl: new URL(".", absolute).href };
}

export function assetUrl(baseUrl: string, relative: string): string {
  return new URL(relative, baseUrl).href;
}
