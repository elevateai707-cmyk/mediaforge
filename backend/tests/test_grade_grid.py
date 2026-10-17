"""Grid layout math, montage selection, labels, and a tiny real ffmpeg render."""
import shutil
import subprocess
import numpy as np
import pytest

from app.grade import grid, cube


def test_quad_size_native_and_cap():
    assert grid.quad_size(1920, 1080) == (1920, 1080)
    assert grid.quad_size(1080, 1920) == (1080, 1920)
    assert grid.quad_size(3840, 2160) == (1920, 1080)                    # 4K capped to 1080-class
    assert grid.quad_size(2160, 3840) == (1080, 1920)                    # vertical 4K likewise
    assert grid.quad_size(1280, 720) == (1280, 720)                      # never upscaled
    assert grid.quad_size(3840, 2160, cap=(3840, 2160)) == (3840, 2160)
    w, h = grid.quad_size(1288, 1610)
    assert w % 2 == 0 and h % 2 == 0 and h <= 1920 and w <= 1080


def test_grid_layout_canvas():
    lay = grid.grid_layout(1920, 1080)
    assert lay["canvas"] == (3840, 2160)
    assert lay["positions"] == [(0, 0), (1920, 0), (0, 1080), (1920, 1080)]
    lay = grid.grid_layout(1080, 1920)
    assert lay["canvas"] == (2160, 3840) and lay["positions"][3] == (1080, 1920)


def test_esc_and_slug():
    assert grid.slug("Stampede Sunset (v2)!") == "Stampede_Sunset_v2"
    e = grid.esc("/a b/it's:x.cube")
    assert e.startswith("'") and "\\:" in e and "'\\''" in e


def _items():
    return [
        {"beat": 1, "path": "/v/a.mov", "start": 1.0, "end": 2.0, "confidence": 0.9, "order": 1, "label": "Beat 01 · take 1", "name": "a.mov"},
        {"beat": 1, "path": "/v/a.mov", "start": 20.0, "end": 21.0, "confidence": 0.95, "order": 2, "label": "Beat 01 · take 2", "name": "a.mov"},
        {"beat": 1, "path": "/v/b.mov", "start": 5.0, "end": 6.0, "confidence": 0.5, "order": 3, "label": "Beat 01 · take 3", "name": "b.mov"},
        {"beat": 2, "path": "/v/c.mov", "start": 0.0, "end": 1.0, "confidence": 0.8, "order": 4, "label": "Beat 02 · take 1", "name": "c.mov"},
        {"beat": 0, "path": "/v/z.mov", "start": 0.0, "end": 1.0, "confidence": 0.0, "order": 5, "label": "Unassigned · take 1", "name": "z.mov"},
    ]


def test_select_montage_one_per_file_and_bounds():
    segs = grid.select_montage(_items(), per_beat=2, seg_len=3.5, durations={"/v/a.mov": 60, "/v/b.mov": 4.0, "/v/c.mov": 1.0})
    assert [s["path"] for s in segs] == ["/v/a.mov", "/v/b.mov", "/v/c.mov"]      # distinct files, unassigned dropped
    assert segs[0]["start"] == 20.0 and segs[0]["dur"] == 3.5                        # highest confidence take of a.mov
    b = segs[1]
    assert b["start"] + b["dur"] <= 4.0 + 1e-6 and b["dur"] >= 2.0 - 1e-6           # shifted back to fit the min length
    assert segs[2]["start"] == 0.0 and segs[2]["dur"] <= 1.0 + 1e-6                   # tiny source: whatever exists
    capped = grid.select_montage(_items(), seg_len=3.5, max_total=4.0, durations={"/v/a.mov": 60, "/v/b.mov": 60, "/v/c.mov": 60})
    assert sum(s["dur"] for s in capped) <= 4.0


def test_label_canvas(tmp_path):
    from PIL import Image
    p = grid.make_label_canvas(tmp_path / "l.png", (480, 640), (240, 320), ["Balanced", "Warm Sunset", "Teal Beach", "Red Night"])
    im = Image.open(p)
    assert im.size == (480, 640) and im.mode == "RGBA"
    a = np.asarray(im)[..., 3]
    assert a[:160].max() == 0                                    # nothing in the top half-row of the top-left quad (labels sit low)
    for x0, y0 in [(0, 0), (240, 0), (0, 320), (240, 320)]:
        assert a[y0 + 160:y0 + 320, x0:x0 + 240].max() > 0      # each quadrant has a label in its lower half


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")
def test_render_grid_tiny(tmp_path):
    src = tmp_path / "src.mov"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x288:d=2:r=30",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", str(src)], check=True)
    ident = cube.write_cube(tmp_path / "id.cube", cube.identity(9))
    warm = cube.from_points(np.clip(cube.grid_points(9) * np.array([1.2, 1.0, 0.8]), 0, 1), 9)
    wc = cube.write_cube(tmp_path / "warm.cube", warm)
    segs = [{"path": str(src), "start": 0.0, "dur": 1.0, "beat": 1, "label": "x", "name": "src.mov"}]
    out = tmp_path / "grid.mp4"
    res = grid.render_grid(tmp_path, segs, {str(src): ident}, [("warm_look", wc)], out, fps=30)
    assert res["canvas"] == (320, 576) and out.stat().st_size > 1000
    from app.grade.sampling import probe
    p = probe(out)
    assert (p["width"], p["height"]) == (320, 576)
    # the warm quadrant (top-right) is warmer than the balanced quadrant (top-left)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(576, 320, 3).astype(float)
    tl, tr = fr[:200, :160], fr[:200, 160:]
    assert tr[..., 0].mean() - tr[..., 2].mean() > tl[..., 0].mean() - tl[..., 2].mean() + 5
