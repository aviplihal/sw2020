"""Pure tests: the same failure gets the same signature, whatever its names and numbers."""

from solidworks_mcp.kb.signatures import normalise, signature


def test_a_coded_error_is_signed_by_its_code():
    assert signature("FILLET_TOO_LARGE", "Fillet of 5 mm refused on Boss-Extrude2") == "FILLET_TOO_LARGE"


def test_names_numbers_paths_and_hresults_are_placeholders():
    assert normalise("Fillet3 of 5.5 mm failed on Boss-Extrude2 in Sketch12 (D1@Sketch3)") == \
        "<FEATURE> of <N> mm failed on <FEATURE> in <SKETCH> (<DIM>)"
    assert normalise(r"Write failed: C:\SW-Output\DN17.SLDPRT; SaveAs3 returned -2147417848") == \
        "Write failed: <PATH> <FEATURE> returned <HRESULT>"
    assert normalise("Could not open 'C:\\Program Files\\x y.sldprt' (0x80010108)") == \
        "Could not open <Q> (<HRESULT>)"
    assert normalise("Saved /tmp/jobs/a.step at .5 and -1e-3") == "Saved <PATH> at <N> and <N>"


def test_the_same_failure_with_other_values_has_one_signature():
    a = signature(None, "Revolve1 failed: the profile crosses the axis at x = -2.5 mm")
    b = signature(None, "Revolve7 failed: the profile crosses the axis at x = 14 mm")
    c = signature(None, "Revolve1 failed: the profile touches nothing")
    assert a == b != c
    assert a.startswith("T:") and len(a) == 14


def test_whitespace_does_not_change_a_signature():
    assert signature(None, "Shell  failed\n on\tface") == signature(None, "Shell failed on face")
