"""Script <-> clip matching, take grouping, chronological fallback."""
import json
from app.grade import assemble as A

SCRIPT = """
1. Vancouver beach - turquoise water, mountains, sailboats and cargo ships
2. Edmonton parade - dancers waving flags down the street in daylight
3. Edmonton night - red lights and neon glowing downtown after dark
4. Regina legislature - the dome of the Legislative Building
5. Calgary Stampede - carnival rides and the midway
"""


def clip(name, city, caption, taken, luma=None, speech="", start=0.0, end=5.0):
    return A.ClipInfo(path=f"/media/{name}.mov", start=start, end=end, name=f"{name}.mov", city=city,
                      caption=caption, speech=speech, taken_at=taken, luma=luma, source="library")


CLIPS = [
    clip("c_stamp2", "Calgary", "A sunny carnival with a swing ride and Ferris wheel on the midway", "2026-07-08T16:00", 0.5),
    clip("c_van2", "Vancouver", "Two people stand on a sandy shore with sailboats and distant mountains", "2026-08-24T21:16", 0.55),
    clip("c_edm_night", "Edmonton", "Nighttime view of a plaza with red vertical lights and neon", "2026-08-08T04:50", 0.12),
    clip("c_reg", "Regina", "A wide shot of the Legislative Building with its dome", "2026-07-27T20:16", 0.5),
    clip("c_edm_parade1", "Edmonton", "A vibrant street parade with dancers wearing flags", "2026-08-08T19:39", 0.5),
    clip("c_van1", "Vancouver", "A calm turquoise sea with three cargo ships and mountains", "2026-08-24T21:17", 0.5),
    clip("c_edm_parade2", "Edmonton", "A crowd marches down a city street, one person wearing a flag cape", "2026-08-08T19:40", 0.48),
    clip("c_stamp1", "Calgary", "A crowd walks toward the Stampede entrance under a red arch", "2026-07-08T15:59", 0.5),
    clip("c_van3", "Vancouver", "A girl and man run into shallow turquoise water at a beach", "2026-08-24T21:18", 0.5),
    clip("c_mystery", "", "", "2026-08-01T10:00", 0.4),
]


def test_parse_script_variants():
    b = A.parse_script(SCRIPT)
    assert [x.index for x in b] == [1, 2, 3, 4, 5] and b[0].title.startswith("Vancouver beach")
    assert len(A.parse_script("Beat 1: open on the lake\nBeat 2: the town")) == 2
    paras = A.parse_script("The opening shot of the lake at dawn.\nStill the lake.\n\nSecond paragraph about the town.")
    assert len(paras) == 2
    assert len(A.parse_script("line one\nline two\nline three")) == 3
    assert A.parse_script("  ") == []


def test_matching_and_take_grouping():
    plan = A.assemble(CLIPS, A.parse_script(SCRIPT))
    beats_order = [it["beat"] for it in plan["items"]]
    assert beats_order == sorted(beats_order, key=lambda b: (b == 0, b))        # script order, unassigned last
    by = lambda n: next(i for i in plan["items"] if i["name"] == f"{n}.mov")
    assert by("c_van1")["beat"] == by("c_van2")["beat"] == by("c_van3")["beat"] == 1
    assert by("c_edm_parade1")["beat"] == by("c_edm_parade2")["beat"] == 2
    assert by("c_edm_night")["beat"] == 3                                     # night cue + words beat the shared city
    assert by("c_reg")["beat"] == 4
    assert by("c_stamp1")["beat"] == by("c_stamp2")["beat"] == 5
    # takes are consecutive, chronological and labelled
    van = [i for i in plan["items"] if i["beat"] == 1]
    assert [v["take"] for v in van] == [1, 2, 3]
    assert [v["name"] for v in van] == ["c_van2.mov", "c_van1.mov", "c_van3.mov"]     # by capture time
    assert [v["label"] for v in van][0] == "Beat 01 · take 1"
    positions = [plan["items"].index(v) for v in van]
    assert positions == list(range(positions[0], positions[0] + 3))
    # no evidence -> unassigned at the end, with 0 confidence, never silently placed
    last = plan["items"][-1]
    assert last["name"] == "c_mystery.mov" and last["beat"] == 0 and last["label"].startswith("Unassigned")
    assert plan["summary"]["unassigned"] == 1
    assert all(0 <= i["confidence"] <= 1 for i in plan["items"])
    assert by("c_van1")["confidence"] > 0.4 and by("c_van1")["matched_terms"]


def test_night_cue_decides_between_same_city_beats():
    c_dark = clip("x", "Edmonton", "People on a street with lights", "2026-08-08T22:00", luma=0.1)
    c_bright = clip("y", "Edmonton", "People on a street with lights", "2026-08-08T14:00", luma=0.6)
    plan = A.assemble([c_dark, c_bright], A.parse_script(SCRIPT))
    by = {i["name"]: i for i in plan["items"]}
    assert by["x.mov"]["beat"] == 3
    assert by["y.mov"]["beat"] in (2, 0)


def test_dialogue_match():
    beats = A.parse_script("1. Welcome everyone to the summer road trip across Canada\n2. We landed in Vancouver")
    talking = clip("t", "", "a man talking to the camera", "2026-08-01T10:00",
                   speech="welcome everyone to our summer road trip across canada")
    plan = A.assemble([talking], beats)
    assert plan["items"][0]["beat"] == 1 and "<dialogue>" in plan["items"][0]["matched_terms"]


def test_chronological_fallback_groups_takes():
    a = clip("a1", "Calgary", "A man talking to camera in front of a ferris wheel", "2026-07-08T10:00")
    b = clip("a2", "Calgary", "A man talking to camera in front of the ferris wheel", "2026-07-08T10:01")
    c = clip("b1", "Regina", "The dome of the legislature at noon", "2026-07-09T10:00")
    plan = A.assemble([c, b, a], None)
    assert plan["mode"] == "chronological"
    assert [i["name"] for i in plan["items"]] == ["a1.mov", "a2.mov", "b1.mov"]
    assert [i["take"] for i in plan["items"]] == [1, 2, 1]
    assert plan["items"][0]["label"] == "Shot 01 · take 1"


def test_collect_clips(tmp_path):
    (tmp_path / "a.mov").write_bytes(b"x"); (tmp_path / "b.txt").write_text("n"); (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.MP4").write_bytes(b"x")
    names = sorted(A.Path(c.path).name for c in A.collect_clips(tmp_path))
    assert names == ["a.mov", "c.MP4"]
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"clips": [{"path": "/x/a.mov", "start": 1, "end": 2}]}))
    refs = A.collect_clips(m)
    assert refs[0].start == 1 and refs[0].end == 2
    try:
        A.collect_clips(tmp_path / "missing")
        assert False
    except ValueError:
        pass


def test_unrelated_chatter_is_not_dialogue():
    beats = A.parse_script("1. Edmonton parade - dancers and flags down the street\n2. Edmonton night - red lights downtown after dark")
    c = clip("m", "Edmonton", "Nighttime plaza with red lights", "2026-08-08T22:00", luma=0.37,
             speech="yeah we are going down there later and then we will see what happens")
    plan = A.assemble([c], beats)
    assert plan["items"][0]["beat"] == 2 and "<dialogue>" not in plan["items"][0]["matched_terms"]


def test_snap_fps():
    from app.grade.job import snap_fps
    assert snap_fps(23.9289) in (23.976, 24.0) and snap_fps(29.9940) == 30.0 and snap_fps(23.976) == 23.976
    assert snap_fps(59.94) == 59.94 and snap_fps(37.0) == 37.0
