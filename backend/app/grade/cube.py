"""Adobe/Resolve ``.cube`` 3D LUT reader/writer plus numpy apply/compose.

Array convention: ``lut[ri, gi, bi] -> (r, g, b)`` (shape N,N,N,3).  The file
lists red fastest, which is also what ffmpeg ``lut3d`` and Resolve expect.
"""
from __future__ import annotations
from pathlib import Path
import numpy as np

DEFAULT_SIZE = 33


def identity(size: int = DEFAULT_SIZE) -> np.ndarray:
    g = np.linspace(0.0, 1.0, size)
    r, gg, b = np.meshgrid(g, g, g, indexing="ij")
    return np.stack([r, gg, b], axis=-1)


def grid_points(size: int = DEFAULT_SIZE) -> np.ndarray:
    """All lattice colours flattened in lut order -> (N^3, 3)."""
    return identity(size).reshape(-1, 3)


def from_points(points: np.ndarray, size: int = DEFAULT_SIZE) -> np.ndarray:
    return np.asarray(points, dtype=np.float64).reshape(size, size, size, 3)


def write_cube(path, lut: np.ndarray, title: str = "MediaForge") -> Path:
    path = Path(path)
    size = lut.shape[0]
    if lut.shape != (size, size, size, 3):
        raise ValueError("lut must be (N,N,N,3)")
    path.parent.mkdir(parents=True, exist_ok=True)
    flat = np.clip(lut, 0.0, 1.0).transpose(2, 1, 0, 3).reshape(-1, 3)   # red fastest
    safe = "".join(ch for ch in title if ch not in '"\n\r')[:120]
    lines = [f'TITLE "{safe}"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0", ""]
    lines += ["%.6f %.6f %.6f" % tuple(row) for row in flat]
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(path)
    return path


def read_cube(path) -> tuple[np.ndarray, str]:
    size = None
    title = ""
    dmin, dmax = np.zeros(3), np.ones(3)
    rows = []
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key = line.split(None, 1)[0].upper()
        if key == "TITLE":
            title = line.split(None, 1)[1].strip().strip('"') if " " in line else ""
        elif key == "LUT_3D_SIZE":
            size = int(line.split()[1])
        elif key == "LUT_1D_SIZE":
            raise ValueError("1D LUTs are not supported")
        elif key == "DOMAIN_MIN":
            dmin = np.array([float(x) for x in line.split()[1:4]])
        elif key == "DOMAIN_MAX":
            dmax = np.array([float(x) for x in line.split()[1:4]])
        elif key in ("LUT_3D_INPUT_RANGE", "LUT_1D_INPUT_RANGE"):
            continue
        else:
            rows.append([float(x) for x in line.split()[:3]])
    if size is None or len(rows) != size ** 3:
        raise ValueError("malformed .cube: size/rows mismatch")
    flat = np.array(rows, dtype=np.float64)
    if not (np.allclose(dmin, 0) and np.allclose(dmax, 1)):
        flat = (flat - dmin) / (dmax - dmin)
    lut = flat.reshape(size, size, size, 3).transpose(2, 1, 0, 3).copy()   # back to [r,g,b]
    return lut, title


def apply_lut(lut: np.ndarray, rgb: np.ndarray) -> np.ndarray:
    """Trilinear lookup of encoded RGB (...,3) in [0,1]."""
    n = lut.shape[0]
    x = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0) * (n - 1)
    i0 = np.minimum(np.floor(x).astype(int), n - 2)
    f = x - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    out = 0.0
    for dr in (0, 1):
        for dg in (0, 1):
            for db in (0, 1):
                w = (fr if dr else 1 - fr) * (fg if dg else 1 - fg) * (fb if db else 1 - fb)
                out = out + w * lut[r0 + dr, g0 + dg, b0 + db]
    return out


def compose(first: np.ndarray, second: np.ndarray, size: int | None = None) -> np.ndarray:
    """LUT that applies ``first`` then ``second`` (baked on a size^3 lattice)."""
    size = size or first.shape[0]
    pts = grid_points(size)
    mid = apply_lut(first, pts)
    return from_points(apply_lut(second, mid), size)


def is_identity(lut: np.ndarray, tol: float = 1e-3) -> bool:
    return float(np.max(np.abs(lut - identity(lut.shape[0])))) <= tol
