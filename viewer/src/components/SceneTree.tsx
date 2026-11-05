import type { Scene } from "../types/scene";
import { useViewer } from "../store/viewerStore";

const SOURCE_LABEL = { auto: "auto", manual: "manual", preview: "preview" } as const;

export function SceneTree({ scene }: { scene: Scene }) {
  const hidden = useViewer((s) => s.hidden);
  const selected = useViewer((s) => s.selected);
  const toggle = useViewer((s) => s.toggleLayer);
  const select = useViewer((s) => s.select);
  const layers = [...scene.layers].sort((a, b) => b.order - a.order); // nearest first

  return (
    <nav className="panel tree" aria-label="Scene layers">
      <h2>Scene</h2>
      <div className="tree-root" title={scene.sceneId}>
        {scene.name}
      </div>
      <ul>
        {layers.map((l) => {
          const visible = l.visible && !hidden[l.id];
          return (
            <li key={l.id} className={selected === l.id ? "selected" : undefined}>
              <button
                className="eye"
                aria-label={visible ? `Hide ${l.name}` : `Show ${l.name}`}
                aria-pressed={visible}
                disabled={!l.visible}
                onClick={() => toggle(l.id)}
              >
                {visible ? "◉" : "○"}
              </button>
              <button className="layer-name" onClick={() => select(selected === l.id ? null : l.id)}>
                <span>{l.name}</span>
                <small>
                  z {l.depth.toFixed(2)} · {l.role} · {SOURCE_LABEL[l.provenance.source]}
                </small>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
