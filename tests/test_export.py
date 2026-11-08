"""Phase 7: standalone web project export."""

from __future__ import annotations

import json
import zipfile

from conftest import ROOT, call
from papercraft_hermes.core import exporter
from papercraft_hermes.core.schemas import validate_scene
from test_build_scene import SUN, _segmented


def _exported(tmp_path, fixture_dir, monkeypatch, *, spec=True):
    ctx, pid, pdir = _segmented(tmp_path, fixture_dir, monkeypatch, emitters=[SUN])
    assert call(ctx, "papercraft_build_scene", {"project_id": pid})["ok"]
    if spec:
        assert call(ctx, "papercraft_generate_plan", {"project_id": pid})["ok"]
    res = call(ctx, "papercraft_export_project", {"project_id": pid})
    assert res["ok"], res
    return ctx, pid, pdir, res


def test_export_is_standalone(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, pdir, res = _exported(tmp_path, fixture_dir, monkeypatch)
    out = pdir / "export" / "source-web"
    assert res["data"]["export_dir"] == str(out)
    for rel in ("package.json", "package-lock.json", "index.html", "vite.config.ts", "tsconfig.json", ".env",
                "README.md", "src/App.tsx", "src/scene/PaperScene.tsx", "schema/scene.schema.json",
                "public/scene/scene.json", "docs/DESIGN_SPEC.md", "docs/analysis.json", "docs/layers.json",
                "docs/process-log.jsonl", "docs/review/overlay.png"):
        assert (out / rel).is_file(), rel

    viewer_pkg = json.loads((ROOT / "viewer" / "package.json").read_text())
    pkg = json.loads((out / "package.json").read_text())
    assert pkg["dependencies"] == viewer_pkg["dependencies"] and pkg["scripts"]["build"] == viewer_pkg["scripts"]["build"]
    vite = (out / "vite.config.ts").read_text()
    assert "PAPERCRAFT_PROJECT" not in vite and './schema' in vite and 'base: "./"' in vite
    assert json.loads((out / "tsconfig.json").read_text())["compilerOptions"]["paths"] == {"@schemas/*": ["./schema/*"]}
    assert (out / ".env").read_text().strip() == "VITE_DEFAULT_SCENE=scene/scene.json"
    assert not (out / "public" / "scenes").exists(), "the dev fixture is not shipped"
    assert not (out / "node_modules").exists()

    scene = json.loads((out / "public/scene/scene.json").read_text())
    validate_scene(scene, base_dir=out / "public" / "scene")  # every referenced file was copied
    assert exporter.absolute_path_leaks(out) == []
    log = (out / "docs/process-log.jsonl").read_text()
    assert str(tmp_path) not in log and "(path removed)" in log

    with zipfile.ZipFile(res["data"]["zip_path"]) as zf:
        names = {n for n in zf.namelist()}
    files = {f"source-web/{p.relative_to(out).as_posix()}" for p in out.rglob("*") if p.is_file()}
    assert names == files and res["data"]["files"] == len(files)
    assert json.loads((pdir / "status.json").read_text())["stages"]["export"]["state"] == "succeeded"


def test_reexport_replaces_old_files(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, pdir, _ = _exported(tmp_path, fixture_dir, monkeypatch, spec=False)
    stale = pdir / "export" / "source-web" / "stale.txt"
    stale.write_text("old")
    res = call(ctx, "papercraft_export_project", {"project_id": pid, "zip": False})
    assert res["ok"] and res["data"]["zip_path"] is None and not stale.exists()
    assert any("no DESIGN_SPEC.md" in w for w in res["warnings"])


def test_requires_scene(tmp_path, fixture_dir, monkeypatch):
    ctx, pid, _ = _segmented(tmp_path, fixture_dir, monkeypatch)
    assert call(ctx, "papercraft_export_project", {"project_id": pid})["error"]["code"] == "precondition_failed"
    assert call(ctx, "papercraft_export_project", {"project_id": pid, "zip": "yes"})["error"]["code"] == "invalid_argument"


def test_path_scrubbing_and_leak_detection(tmp_path):
    record = {"source": {"path": "C:\\Users\\youho\\Pictures\\fox.webp"}, "note": "saved /home/alice/work/x.png ok",
              "url": "https://example.com/home/page"}
    clean = exporter._strip_abs(record)
    assert clean["source"]["path"] == "fox.webp (path removed)" and "alice" not in clean["note"]
    assert clean["url"] == record["url"], "URLs are not paths"
    (tmp_path / "a.md").write_text("see /Users/bob/secret.png")
    (tmp_path / "b.json").write_text('{"ok": "relative/path.png"}')
    leaks = exporter.absolute_path_leaks(tmp_path)
    assert len(leaks) == 1 and leaks[0].startswith("a.md")
