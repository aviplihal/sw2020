"""Pure tests: a case's DN number comes from the file name (no SolidWorks)."""

import pytest

from solidworks_mcp.errors import SolidWorksError
from solidworks_mcp.kb import dn


@pytest.mark.parametrize("path,number", [("DN17.pdf", 17), (r"C:\Scans\DN17.pdf", 17),
                                         ("/scans/dn5.PDF", 5), ("DN1234.pdf", 1234)])
def test_the_dn_number_comes_from_the_file_name(path, number):
    assert dn.from_filename(path) == number


@pytest.mark.parametrize("path", ["DN17 rev B.pdf", "DN017.pdf", "DN0.pdf", "17.pdf", "DN17.png",
                                  "DN.pdf", "scan.pdf", "DN17.pdf.txt"])
def test_any_other_file_name_is_refused(path):
    with pytest.raises(SolidWorksError, match="not named DN<number>.pdf") as refused:
        dn.from_filename(path)
    assert refused.value.code == "KB_BAD_DN_NAME"


@pytest.mark.parametrize("text,number", [("DN17", 17), ("dn 17", 17), ("DN-017", 17), ("DN#17", 17),
                                         ("seventeen", None), ("", None), ("DN", None)])
def test_a_handwritten_dn_is_read_leniently(text, number):
    assert dn.from_written(text) == number
