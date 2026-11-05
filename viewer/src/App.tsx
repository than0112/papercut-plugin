import { useEffect, useState } from "react";
import { PaperScene } from "./scene/PaperScene";
import { loadScene, SceneLoadError, type LoadedScene } from "./scene/loadScene";
import { SceneTree } from "./components/SceneTree";
import { ControlPanel } from "./components/ControlPanel";
import { useViewer } from "./store/viewerStore";

/** Which scene to open: ?scene=<url of scene.json>, default = bundled scene/scene.json or the dev fixture. */
function sceneUrl(): string {
  const param = new URLSearchParams(window.location.search).get("scene");
  return param || import.meta.env.VITE_DEFAULT_SCENE || "scenes/fixture/scene.json";
}

type State = { status: "loading" } | { status: "error"; error: SceneLoadError } | { status: "ready"; loaded: LoadedScene };

export default function App() {
  const [state, setState] = useState<State>({ status: "loading" });
  const resetAll = useViewer((s) => s.resetAll);

  useEffect(() => {
    let cancelled = false;
    loadScene(sceneUrl())
      .then((loaded) => {
        if (cancelled) return;
        resetAll({
          shadows: loaded.scene.rendering.shadows !== false,
          parallax: loaded.scene.interaction.parallax?.enabled !== false,
        });
        setState({ status: "ready", loaded });
      })
      .catch((err) => {
        if (cancelled) return;
        setState({ status: "error", error: err instanceof SceneLoadError ? err : new SceneLoadError(String(err)) });
      });
    return () => {
      cancelled = true;
    };
  }, [resetAll]);

  if (state.status === "loading") return <div className="center">Loading scene…</div>;
  if (state.status === "error")
    return (
      <div className="center error" role="alert">
        <h1>Scene could not be loaded</h1>
        <p>{state.error.message}</p>
        {state.error.details.length > 0 && (
          <ul>
            {state.error.details.slice(0, 20).map((d) => (
              <li key={d}>
                <code>{d}</code>
              </li>
            ))}
          </ul>
        )}
      </div>
    );

  const { scene, baseUrl } = state.loaded;
  return (
    <div className="app">
      <header className="topbar">
        <strong>PaperCraft 3D</strong>
        <span className="scene-name">{scene.name}</span>
        <span className="badge">rev {scene.generator?.revision ?? 0}</span>
      </header>
      {scene.mode === "preview" && (
        <div className="banner" role="status">
          示意預覽 Preview mode — the image was not segmented into real layers. This is not a 3D reconstruction.
        </div>
      )}
      <SceneTree scene={scene} />
      <main className="viewport">
        <PaperScene scene={scene} baseUrl={baseUrl} />
      </main>
      <ControlPanel scene={scene} />
    </div>
  );
}
