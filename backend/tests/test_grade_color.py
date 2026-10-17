"""Colour maths: .cube round-trip, correction, reference-image look LUTs."""
import numpy as np
import pytest

from app.grade import cube, correct, looks
from app.grade.colorspace import rgb_to_lab, lab_to_rgb, luma, to_linear


def synth(n=6000, seed=1):
    """A plausible 'footage' pixel cloud: smooth gradients + some saturated colours."""
    rng = np.random.default_rng(seed)
    base = rng.beta(2.0, 2.4, size=(n, 1))
    tint = rng.normal(0, 0.08, size=(n, 3))
    return np.clip(base + tint, 0, 1)


def test_cube_roundtrip(tmp_path):
    rng = np.random.default_rng(0)
    lut = np.clip(cube.identity(9) + rng.normal(0, 0.02, (9, 9, 9, 3)), 0, 1)
    p = cube.write_cube(tmp_path / "a" / "x.cube", lut, 'My "Look"')
    back, title = cube.read_cube(p)
    assert back.shape == lut.shape and np.max(np.abs(back - lut)) < 1e-6
    assert "My" in title and '"' not in title
    text = p.read_text().splitlines()
    assert "LUT_3D_SIZE 9" in text
    # red varies fastest in the file (checked on a clean identity LUT)
    q = cube.write_cube(tmp_path / "id.cube", cube.identity(9)).read_text().splitlines()
    first = [float(x) for x in q[5].split()], [float(x) for x in q[6].split()]
    assert first[1][0] > first[0][0] and first[1][1] == first[0][1] == 0.0


def test_cube_reader_rejects_bad(tmp_path):
    p = tmp_path / "bad.cube"
    p.write_text("LUT_3D_SIZE 2\n0 0 0\n")
    with pytest.raises(ValueError):
        cube.read_cube(p)
    p.write_text("LUT_1D_SIZE 4\n")
    with pytest.raises(ValueError):
        cube.read_cube(p)


def test_identity_lut_applies_as_identity():
    lut = cube.identity(33)
    x = np.random.default_rng(3).random((500, 3))
    assert np.max(np.abs(cube.apply_lut(lut, x) - x)) < 1e-9
    assert cube.is_identity(lut)


def test_compose_matches_chain():
    a = correct.correction_lut(correct.Correction((1.1, 1.0, 0.9), 0.2, 0.01, 0.98, 0.1), 33)
    b = cube.from_points(np.clip(cube.grid_points(33) ** 1.2, 0, 1), 33)
    ab = cube.compose(a, b)
    x = np.random.default_rng(4).random((400, 3))
    assert np.max(np.abs(cube.apply_lut(ab, x) - cube.apply_lut(b, cube.apply_lut(a, x)))) < 0.01


def test_lab_roundtrip_and_gamut():
    x = np.random.default_rng(5).random((1000, 3))
    assert np.max(np.abs(lab_to_rgb(rgb_to_lab(x)) - x)) < 1e-4
    wild = np.array([[60.0, 120.0, 100.0]])
    out = lab_to_rgb(wild)
    assert np.all(out >= 0) and np.all(out <= 1)


def test_correction_neutralises_cast_and_normalises_exposure():
    base = synth()
    warm_dark = np.clip(to_linear(base) * np.array([1.0, 0.8, 0.55]) * 0.5, 0, 1) ** (1 / 2.4)
    c = correct.estimate(warm_dark)
    assert c.stats["after"]["cast"] < c.stats["before"]["cast"] * 0.7
    assert abs(c.stats["after"]["median"] - 0.45) < abs(c.stats["before"]["median"] - 0.45)
    assert c.stops > 0                                   # brightened
    # LUT bakes exactly what apply_correction does
    lut = correct.correction_lut(c, 33)
    out_lut = cube.apply_lut(lut, warm_dark[:500])
    assert np.max(np.abs(out_lut - correct.apply_correction(c, warm_dark[:500]))) < 0.02


def test_correction_leaves_balanced_footage_nearly_alone():
    base = synth()
    neutral = np.clip(base.mean(axis=1, keepdims=True).repeat(3, axis=1), 0, 1)
    neutral = 0.15 + 0.7 * neutral
    c = correct.estimate(neutral)
    assert np.allclose(c.gains, 1.0, atol=0.03)
    assert abs(c.stops) < 0.4


def test_scene_matching_pulls_clips_together():
    base = synth()
    a = np.clip(base * 0.55, 0, 1)
    b = np.clip(base * 0.95, 0, 1)
    solo_gap = abs(correct.estimate(a).stats["after"]["median"] - correct.estimate(b).stats["after"]["median"])
    cs = correct.estimate_scene({"a": a, "b": b}, {"a": "s", "b": "s"})
    gap = abs(cs["a"].stats["after"]["median"] - cs["b"].stats["after"]["median"])
    assert gap < solo_gap
    single = correct.estimate_scene({"a": a}, {"a": "x"})
    assert "a" in single


def test_look_identity_in_identity_out():
    """Reference statistically equal to the footage -> (near) identity LUT."""
    src = synth(8000, seed=2)
    for method in ("reinhard", "sot"):
        lut, info = looks.build_look(src, src.copy(), method=method, strength=1.0, skin_protect=0.0)
        err = np.mean(np.abs(lut - cube.identity(33)))
        assert err < (0.004 if method == "reinhard" else 0.02), (method, err)
        assert info["method"] == method


def test_look_recovers_known_shift():
    """Reference = footage with a known colour shift -> the LUT reproduces it."""
    src = synth(8000, seed=3)
    shift = np.array([1.18, 1.0, 0.82])
    ref = np.clip(src * shift, 0, 1)
    for method in ("reinhard", "sot"):
        lut, _ = looks.build_look(src, ref, method=method, strength=1.0, skin_protect=0.0)
        out = cube.apply_lut(lut, src)
        # chromatic part of the shift (a*, b*) must be recovered; L is deliberately damped for reinhard
        got = rgb_to_lab(out)[:, 1:].mean(0)
        want = rgb_to_lab(ref)[:, 1:].mean(0)
        base = rgb_to_lab(src)[:, 1:].mean(0)
        assert np.linalg.norm(got - want) < 0.25 * np.linalg.norm(want - base) + 1.5, (method, got, want, base)


def test_look_strength_zero_is_identity_and_monotone():
    src = synth(4000, seed=4)
    ref = np.clip(src * np.array([0.8, 1.0, 1.25]), 0, 1)
    lut0, _ = looks.build_look(src, ref, strength=0.0)
    assert cube.is_identity(lut0, 2e-3)
    d = [np.mean(np.abs(looks.build_look(src, ref, strength=s)[0] - cube.identity(33))) for s in (0.25, 0.5, 1.0)]
    assert d[0] < d[1] < d[2]


def test_skin_protection_keeps_skin_hue():
    src = synth(4000, seed=5)
    ref = np.clip(src * np.array([0.7, 1.0, 1.3]), 0, 1)          # strong cool look
    skin = np.array([[0.80, 0.60, 0.48]])
    free = cube.apply_lut(looks.build_look(src, ref, skin_protect=0.0)[0], skin)
    prot = cube.apply_lut(looks.build_look(src, ref, skin_protect=1.0)[0], skin)
    d_free = np.linalg.norm(rgb_to_lab(free)[0, 1:] - rgb_to_lab(skin)[0, 1:])
    d_prot = np.linalg.norm(rgb_to_lab(prot)[0, 1:] - rgb_to_lab(skin)[0, 1:])
    assert d_prot < d_free
    assert looks.skin_weight(rgb_to_lab(skin))[0] > 0.3
    sky = np.array([[0.2, 0.45, 0.85]])
    assert looks.skin_weight(rgb_to_lab(sky))[0] < 0.05


def test_bad_method():
    with pytest.raises(ValueError):
        looks.build_look(synth(500), synth(500), method="nope")
