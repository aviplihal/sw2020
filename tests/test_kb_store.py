"""Pure tests: the knowledge base's case lifecycle on Markdown files, INDEX.md and text search (no SolidWorks)."""

import pytest
from kb_helpers import Clock, call, new_kb, open_case

from solidworks_mcp.errors import SolidWorksError
from solidworks_mcp.kb import log, search, store


@pytest.fixture
def kb(tmp_path):
    yield new_kb(tmp_path / "kb")
    log.use(None)


def _finish(kb, dn, status, description, part="bracket", lessons=None, size="60 x 20 x 10"):
    """A case moved past modelling the way K2 and K5 will: header and sections written."""
    case = kb.case(dn)
    sections = {"Description": description}
    if lessons:
        sections["Lessons"] = f"- {lessons}"
    case = kb.update_case(case, sections, status=status, part_type=part, size_mm=size)
    return case


def _make(kb, number, status="approved", description="A bracket", **more):
    open_case(kb, number)
    return _finish(kb, f"DN{number}", status, description, **more)


def test_the_layout_exists_and_ingest_opens_a_case(kb):
    case, warnings = kb.ingest(17, "DN17.pdf", 1)
    assert (case["dn"], case["status"], warnings) == ("DN17", "ingested", [])
    assert (kb.folder / "cases" / "DN17" / "case.md").is_file() and (kb.folder / "INDEX.md").is_file()
    assert {p.name for p in kb.folder.iterdir()} >= {"cases", "tools", "fixes", "INDEX.md"}
    assert kb.case("dn17")["dn"] == kb.case("17")["dn"] == "DN17"


def test_the_open_case_is_found_from_the_headers_so_it_survives_a_restart(kb, tmp_path):
    kb.ingest(17, "DN17.pdf", 1)
    again = store.KnowledgeBase(tmp_path / "kb")
    assert again.open_case()["dn"] == "DN17"
    again.set_status(again.open_case(), "described")
    assert store.KnowledgeBase(tmp_path / "kb").open_case()["status"] == "described"


def test_only_one_case_is_open_at_a_time(kb):
    kb.ingest(17, "DN17.pdf", 1)
    with pytest.raises(SolidWorksError, match="DN17 is still open") as refused:
        kb.ingest(18, "DN18.pdf", 1)
    assert refused.value.code == "KB_CASE_ALREADY_OPEN"
    kb.update_case(kb.case("DN17"), status="finalised")
    assert kb.open_case() is None and kb.ingest(18, "DN18.pdf", 1)[0]["dn"] == "DN18"


def test_two_open_cases_on_disk_are_reported_not_guessed(kb):
    kb.ingest(17, "DN17.pdf", 1)
    kb.write_case({"dn": "DN18", "number": 18, "status": "modelling", "sections": {}})
    with pytest.raises(SolidWorksError, match="DN17 and DN18 are both open") as refused:
        kb.open_case()
    assert refused.value.code == "KB_TWO_OPEN_CASES"


def test_a_duplicate_dn_is_refused_but_an_abandoned_one_does_not_count(kb):
    kb.ingest(17, "DN17.pdf", 1)
    kb.update_case(kb.case("DN17"), status="finalised")
    with pytest.raises(SolidWorksError, match="DN17 already exists") as refused:
        kb.ingest(17, "DN17.pdf", 1)
    assert refused.value.code == "KB_DUPLICATE_DN"


@pytest.mark.parametrize("earlier,new,warning", [([], 5, None), ([15], 16, None), ([15], 17, "DN16 missing"),
                                                 ([15], 19, "DN16 to DN18 missing")])
def test_a_gap_in_the_dn_numbers_is_a_warning(kb, earlier, new, warning):
    for number in earlier:
        _make(kb, number)
    warnings = kb.ingest(new, f"DN{new}.pdf", 1)[1]
    assert (warnings[0].startswith(warning) if warning else warnings == [])


def test_the_lifecycle_moves_only_forward(kb):
    case = kb.ingest(17, "DN17.pdf", 1)[0]
    with pytest.raises(SolidWorksError, match="DN17 is ingested; it cannot become approved"):
        kb.set_status(case, "approved")
    for status in ("described", "described", "modelling", "finalised", "changes_needed", "approved"):
        case = kb.set_status(case, status)
        assert kb.case("DN17")["status"] == status
    with pytest.raises(SolidWorksError, match="cannot become modelling"):
        kb.set_status(case, "modelling")


def test_abandoning_renames_the_case_frees_the_dn_and_leaves_it_out_of_the_index(kb):
    _make(kb, 16)
    kb.ingest(17, "DN17.pdf", 1)
    call("new_part", 0, run=1)
    abandoned = kb.abandon_open_case("wrong scan")
    assert (abandoned["dn"], abandoned["status"], abandoned["reason"]) == ("DN17~1", "abandoned", "wrong scan")
    assert (kb.folder / "cases" / "DN17~1" / "case.md").is_file() and not (kb.folder / "cases" / "DN17").exists()
    assert (kb.folder / "fixes" / "DN17~1" / "timeline.md").is_file(), "its fixes folder is renamed with it"
    assert "DN17" not in kb.index_text() and kb.open_case() is None
    again = kb.ingest(17, "DN17.pdf", 1)[0]
    assert again["dn"] == "DN17" and kb.abandon_open_case("wrong again")["dn"] == "DN17~2"
    assert kb.case("DN17~1")["status"] == "abandoned" and kb.resolve_job("DN17") == "DN17~2"


def test_abandon_needs_a_reason_and_an_open_case(kb):
    with pytest.raises(SolidWorksError, match="No knowledge-base case is open"):
        kb.abandon_open_case("why")
    kb.ingest(17, "DN17.pdf", 1)
    with pytest.raises(SolidWorksError, match="Say why"):
        kb.abandon_open_case("  ")
    assert kb.open_case()["dn"] == "DN17"


def test_the_index_is_rebuilt_on_every_case_change(kb):
    _make(kb, 1, "approved", "Flat plate")
    _make(kb, 2, "changes_needed", "Angle bracket")
    _make(kb, 3, "finalised", "Round flange", part="flange")
    lines = [line for line in kb.index_text().splitlines() if line.startswith("| DN") and line[4].isdigit()]
    assert [line.split("|")[1].strip() for line in lines] == ["DN1", "DN2", "DN3"]
    kb.update_case(kb.case("DN3"), status="approved")
    lines = [line for line in kb.index_text().splitlines() if line.startswith("| DN") and line[4].isdigit()]
    assert [line.split("|")[1].strip() for line in lines] == ["DN3", "DN1", "DN2"], "newest approved first"


def test_sections_are_replaced_whole_and_removed_when_empty(kb):
    case = kb.ingest(17, "DN17.pdf", 1)[0]
    case = kb.update_case(case, {"Description": "one", "Lessons": "- a"})
    case = kb.update_case(case, {"Lessons": ""})
    assert kb.case("DN17")["sections"] == {"Drawing": case["sections"]["Drawing"], "Description": "one"}


# --- text search -------------------------------------------------------------------------------


def test_text_search_is_case_insensitive_and_needs_every_word(kb):
    _make(kb, 1, description="Mounting plate with 1/2-13 UNC tapped holes")
    _make(kb, 2, description="Shaft with a keyway", part="shaft")
    _make(kb, 3, description="Plate, M6 holes", part="plate")
    found = search.search(kb, "1/2-13 unc")
    assert [line.split("|")[1].strip() for line in found["results"]] == ["DN1"] and found["matches"] == 1
    assert len(search.search(kb, "PLATE")["results"]) == 2
    assert search.search(kb, "plate keyway")["results"] == [] and search.search(kb, "plate keyway")["note"]


def test_text_search_reads_the_spec_and_the_review_and_lesson_text(kb):
    case = _make(kb, 1, description="A bracket", lessons="fillet before the holes")
    (kb.case_folder("DN1") / "spec.json").write_text('{"items": [{"callout": "3/8-16 UNC-2B"}]}', encoding="utf-8")
    kb.update_case(case, {"Review": "**Approved**. The bore was Ø.750."})
    for words in ("3/8-16", "fillet before", "ø.750"):
        assert len(search.search(kb, words)["results"]) == 1, words


def test_search_filters_and_ordering_and_the_abandoned_are_left_out(kb):
    _make(kb, 1, "finalised", "flange one", part="flange")
    _make(kb, 2, "changes_needed", "bracket two")
    _make(kb, 3, "approved", "bracket three")
    open_case(kb, 4)
    kb.abandon_open_case("wrong")
    names = lambda **f: [r.split("|")[1].strip() for r in search.search(kb, **f)["results"]]  # noqa: E731
    assert names() == ["DN3", "DN2", "DN1"], "approved, then changes needed, then unreviewed; DN4~1 never shown"
    assert names(part_type="Bracket") == ["DN3", "DN2"]
    assert names(status="changes_needed") == ["DN2"] and names(status="unreviewed") == ["DN1"]
    assert names(limit=1) == ["DN3"] and "3 cases match" in search.search(kb, limit=1)["note"]
    with pytest.raises(SolidWorksError, match="status takes"):
        search.search(kb, status="done")


def test_the_clock_gives_the_dates_in_the_headers(tmp_path):
    clock = Clock()
    store_ = store.KnowledgeBase(tmp_path / "kb", clock=clock)
    assert store_.ingest(1, "DN1.pdf", 1)[0]["ingested"] == "2026-10-12"
