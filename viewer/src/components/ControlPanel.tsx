import type { Scene } from "../types/scene";
import { useViewer } from "../store/viewerStore";
import { activeLighting } from "../scene/lighting";

export function ControlPanel({ scene }: { scene: Scene }) {
  const v = useViewer();
  const presets = Object.keys(scene.lightingPresets ?? {});
  const L = activeLighting(scene, v.preset);
  const explodeEnabled = scene.interaction.explodedView?.enabled !== false;
  const parallaxEnabled = scene.interaction.parallax?.enabled !== false;

  return (
    <aside className="panel controls" aria-label="Scene controls">
      <h2>Lighting</h2>
      {presets.length > 0 && (
        <div className="segmented" role="radiogroup" aria-label="Lighting preset">
          <button role="radio" aria-checked={v.preset === null} onClick={() => v.setPreset(null)}>
            Scene
          </button>
          {presets.map((p) => (
            <button key={p} role="radio" aria-checked={v.preset === p} onClick={() => v.setPreset(p)}>
              {p}
            </button>
          ))}
        </div>
      )}
      <label className="row">
        <span>Intensity ×{v.lightScale.toFixed(2)}</span>
        <input type="range" min={0} max={2} step={0.01} value={v.lightScale}
          onChange={(e) => v.setLightScale(Number(e.target.value))} />
      </label>
      <label className="row check">
        <input type="checkbox" checked={v.shadows} disabled={scene.rendering.shadows === false}
          onChange={(e) => v.setShadows(e.target.checked)} />
        <span>Shadows</span>
      </label>
      <dl className="readout">
        <dt>Key</dt>
        <dd><i style={{ background: L.key.color }} /> {L.key.color} · {L.key.intensity}</dd>
        <dt>Ambient</dt>
        <dd><i style={{ background: L.ambient.color }} /> {L.ambient.color} · {L.ambient.intensity}</dd>
        {L.rim && (<><dt>Rim</dt><dd><i style={{ background: L.rim.color }} /> {L.rim.color} · {L.rim.intensity}</dd></>)}
      </dl>

      <h2>Layers</h2>
      <label className="row">
        <span>Exploded view {Math.round(v.explode * 100)}%</span>
        <input type="range" min={0} max={1} step={0.01} value={v.explode} disabled={!explodeEnabled}
          onChange={(e) => v.setExplode(Number(e.target.value))} />
      </label>
      <label className="row check">
        <input type="checkbox" checked={v.parallax} disabled={!parallaxEnabled}
          onChange={(e) => v.setParallax(e.target.checked)} />
        <span>Mouse parallax</span>
      </label>

      <h2>Camera</h2>
      <div className="buttons">
        <button onClick={v.resetCamera}>Reset camera</button>
        <button onClick={() => v.resetAll({ shadows: scene.rendering.shadows !== false, parallax: parallaxEnabled })}>
          Reset all
        </button>
      </div>
      <p className="hint">
        Drag to tilt (limited), scroll / pinch to zoom. These controls preview only; persistent edits go
        through <code>papercraft_update_scene</code>.
      </p>
    </aside>
  );
}
