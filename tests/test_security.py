"""Path confinement, identifiers and image validation."""

from __future__ import annotations

import pytest
from PIL import Image

from conftest import plugin  # noqa: F401  (loads the package)
from papercraft_hermes.core.errors import ErrorCode, PaperCraftError
from papercraft_hermes.core.security import require_id, resolve_within, safe_id, validate_image
from papercraft_hermes.core.storage import Workspace


@pytest.mark.parametrize("bad", ["../x", "/etc/passwd", "a/../../b", "assets/../../.."])
def test_resolve_within_blocks_escape(tmp_path, bad):
    with pytest.raises(PaperCraftError) as exc:
        resolve_within(tmp_path, bad)
    assert exc.value.code is ErrorCode.PATH_FORBIDDEN


def test_resolve_within_allows_children(tmp_path):
    assert resolve_within(tmp_path, "assets/a.png") == (tmp_path / "assets/a.png").resolve()


def test_resolve_within_blocks_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    (base / "link").symlink_to(outside)
    with pytest.raises(PaperCraftError):
        resolve_within(base, "link/file.png")


@pytest.mark.parametrize("raw,expected", [
    ("My Scene.png", "my-scene-png"),
    ("; rm -rf / #", "rm-rf"),
    ("紙雕", "scene"),
    ("", "scene"),
])
def test_safe_id(raw, expected):
    assert safe_id(raw) == expected


@pytest.mark.parametrize("bad", ["", "A", "../x", "x" * 65, "a b", None])
def test_require_id_rejects(bad):
    with pytest.raises(PaperCraftError):
        require_id(bad)


def _img(path, fmt="PNG", size=(128, 96), mode="RGB"):
    Image.new(mode, size, "white").save(path, fmt)
    return path


@pytest.mark.parametrize("fmt,ext", [("PNG", "png"), ("JPEG", "jpg"), ("WEBP", "webp")])
def test_validate_image_accepts_supported(tmp_path, fmt, ext):
    info = validate_image(_img(tmp_path / f"a.{ext}", fmt))
    assert info.format == fmt and (info.width, info.height) == (128, 96)
    assert len(info.sha256) == 64


def test_validate_image_rejects_gif(tmp_path):
    with pytest.raises(PaperCraftError) as exc:
        validate_image(_img(tmp_path / "a.gif", "GIF", mode="P"))
    assert exc.value.code is ErrorCode.IMAGE_FORMAT


def test_validate_image_rejects_extension_spoof(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"GIF89a" + b"\0" * 100)
    with pytest.raises(PaperCraftError) as exc:
        validate_image(p)
    assert exc.value.code is ErrorCode.IMAGE_FORMAT


def test_validate_image_rejects_truncated(tmp_path):
    p = _img(tmp_path / "a.png", size=(512, 512))
    p.write_bytes(p.read_bytes()[:200])
    with pytest.raises(PaperCraftError) as exc:
        validate_image(p)
    assert exc.value.code is ErrorCode.IMAGE_FORMAT


def test_validate_image_size_limit(tmp_path):
    p = _img(tmp_path / "a.png")
    with pytest.raises(PaperCraftError) as exc:
        validate_image(p, max_bytes=10)
    assert exc.value.code is ErrorCode.IMAGE_TOO_LARGE


def test_validate_image_too_small(tmp_path):
    with pytest.raises(PaperCraftError):
        validate_image(_img(tmp_path / "a.png", size=(32, 32)))


def test_project_copy_is_read_only_and_confined(tmp_path):
    src = _img(tmp_path / "in.png")
    ws = Workspace(tmp_path / "ws")
    project = ws.create_project(validate_image(src), name="Demo Scene")
    assert project.id.startswith("demo-scene-")
    copied = project.path("source/source.png")
    assert copied.read_bytes() == src.read_bytes()
    assert not (copied.stat().st_mode & 0o222), "source copy must be read-only"
    assert ws.open_project(project.id).id == project.id
    with pytest.raises(PaperCraftError):
        project.path("../../escape.json")
    with pytest.raises(PaperCraftError) as exc:
        ws.open_project("missing")
    assert exc.value.code is ErrorCode.PROJECT_NOT_FOUND
