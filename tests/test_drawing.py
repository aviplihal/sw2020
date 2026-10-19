"""Integration tests for make_drawing and check_drawing: shop drawings.

Marked `solidworks` -- they need a running SolidWorks (auto-skipped otherwise).
"""

import pytest

from solidworks_mcp.errors import SolidWorksError

pytestmark = pytest.mark.solidworks


def holed_box(part, tmp_path):
    part.add_box(40, 20, 10)
    part.add_hole(6, 30, 10)
    return part.save_part(str(tmp_path / "holed_box.sldprt"))["path"]


def test_a_drawing_shows_every_model_dimension_once(part, tmp_path):
    """Without a spec: front, top and right in third angle, never an isometric."""
    holed_box(part, tmp_path)
    drawing = part.make_drawing(str(tmp_path / "holed_box.pdf"))
    assert drawing["bytes"] > 1000
    assert drawing["model_dimensions"] == part.list_dimensions()["count"] == 6  # width height depth, hole d x y
    assert list(drawing["views"]) == ["front", "top", "right"]


def test_a_drawing_can_be_kept_to_edit(part, tmp_path):
    holed_box(part, tmp_path)
    drawing = part.make_drawing(str(tmp_path / "holed_box.slddrw"))
    assert drawing["path"].lower().endswith(".slddrw") and drawing["bytes"] > 1000
    assert "Pack and Go" in drawing["note"]


@pytest.mark.parametrize("name", ["holed_box.pdf", "holed_box.slddrw"])
def test_a_drawing_closes_again(part, tmp_path, name):
    """The drawing is saved and closed: it would stay open in SolidWorks, and
    keep the part loaded after close_part."""
    holed_box(part, tmp_path)
    part.make_drawing(str(tmp_path / name))
    documents = part.list_documents()["documents"]
    assert [d["title"] for d in documents if d["type"] == "drawing"] == [], "the drawing stayed open"
    assert [d["title"] for d in documents if d["current"]] == ["holed_box.SLDPRT"], "the part should be current again"
    assert not [d for d in documents if d["current"] and d["modified"]], "making a drawing changed the part"


def test_a_drawing_needs_the_part_saved(part, tmp_path):
    part.add_box(40, 20, 10)
    with pytest.raises(SolidWorksError, match="Save the part first"):
        part.make_drawing(str(tmp_path / "unsaved.pdf"))


# --- from a spec --------------------------------------------------------------
#
# A box 1.500 x 1.000 x .500 in (38.1 x 25.4 x 12.7 mm, x y z from the origin)
# with a .250 hole through it at x 28.575, y 12.7: whole inches, so the model
# measures exactly what the spec says. The front view looks at its +z face.


def inch_box(part, tmp_path):
    part.add_box(38.1, 25.4, 12.7)
    part.add_hole(6.35, 28.575, 12.7)
    return part.save_part(str(tmp_path / "inch_box.sldprt"))["path"]


SPEC = {"units": "in", "items": [
    {"id": "D1", "kind": "dimension", "value": "1.500", "decimals": 3,
     "tolerance": {"plus": ".005", "minus": ".005"}, "between": "width", "status": "read"},
    {"id": "D2", "kind": "dimension", "value": ".250", "decimals": 3, "tolerance": None,
     "between": "hole diameter", "status": "read"},
    {"id": "D3", "kind": "dimension", "value": "1", "decimals": 0, "tolerance": None,
     "between": "height", "status": "read"},
    {"id": "A", "kind": "datum", "label": "A", "feature": "left face", "status": "read"},
    {"id": "G1", "kind": "gdt", "characteristic": "position", "tolerance": "Ø.010", "modifier": "MMC",
     "datums": ["A"], "feature": "hole", "status": "read"},
    {"id": "F1", "kind": "finish", "symbol": "machined", "value": "63", "surface": "top", "status": "read"},
    {"id": "N1", "kind": "note", "text": "BREAK ALL SHARP EDGES .010 MAX", "status": "read"},
    {"id": "D9", "kind": "dimension", "value": None, "between": "?", "status": "unreadable"},
]}

VIEWS = [{"kind": "standard", "model_view": "front"}, {"kind": "standard", "direction": "right"}]

PLACEMENTS = {
    "D1": {"view": "front", "measure": "horizontal", "points_mm": [[0, 10, 12.7], [38.1, 10, 12.7]], "side": "below"},
    "D2": {"view": "front", "measure": "diameter", "points_mm": [[31.75, 12.7, 12.7]]},
    "D3": {"view": "right", "measure": "vertical", "points_mm": [[38.1, 0, 5], [38.1, 25.4, 5]]},
    "A": {"view": "front", "point_mm": [0, 10, 12.7]},
    "G1": {"view": "front", "on": "D2"},
    "F1": {"view": "front", "point_mm": [20, 25.4, 12.7]},
}


def test_a_spec_drawing_reads_back_clean(part, tmp_path):
    """Every read item placed, named after its id, and found again in the saved file."""
    inch_box(part, tmp_path)
    path = str(tmp_path / "spec.slddrw")
    made = part.make_drawing(path, SPEC, VIEWS, PLACEMENTS)
    assert sorted(made["placed"]) == ["A", "D1", "D2", "D3", "F1", "G1", "N1"] and made["unreadable"] == ["D9"]
    check = part.check_drawing(path, SPEC)
    status = {line["id"]: line["status"] for line in check["items"]}
    assert status == {"D1": "ok", "D2": "ok", "D3": "ok", "A": "ok", "G1": "ok", "F1": "ok", "N1": "ok",
                      "D9": "unreadable"}
    assert check["clean"] and check["not_in_spec"] == []
    assert not [d for d in part.list_documents()["documents"] if d["current"] and d["modified"]]


def test_the_check_sees_a_wrong_model(part, tmp_path):
    """The drawing shows the model: a spec value the model does not have differs."""
    inch_box(part, tmp_path)
    path = str(tmp_path / "wrong.slddrw")
    part.make_drawing(path, SPEC, VIEWS, PLACEMENTS)
    wrong = {**SPEC, "items": [dict(i, value="1.520") if i["id"] == "D1" else i for i in SPEC["items"]]}
    line = next(i for i in part.check_drawing(path, wrong)["items"] if i["id"] == "D1")
    assert line["status"] == "differs" and line["shown"] == "1.500 ±.005"


def test_an_unreadable_item_is_never_placed(part, tmp_path):
    inch_box(part, tmp_path)
    with pytest.raises(SolidWorksError, match="unreadable"):
        part.make_drawing(str(tmp_path / "x.slddrw"), SPEC, VIEWS,
                          {**PLACEMENTS, "D9": {"view": "front", "measure": "horizontal",
                                                "points_mm": [[0, 10, 12.7], [38.1, 10, 12.7]]}})
