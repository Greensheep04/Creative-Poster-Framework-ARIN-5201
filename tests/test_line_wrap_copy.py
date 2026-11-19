import pytest
from poster_critic.checks import text_matches
from poster_critic.contracts import CopyItem


@pytest.mark.parametrize("expected,observed,match", [
    ("Hope Rising in the Ocean","Hope Rising in the\nOcean",True),
    ("Plastic pollution may be visible, but hope is the tide.","Plastic pollution may be visible, but hope is\r\nthe tide.",True),
    ("保护海洋，减少塑料。","保护海洋，\n减少塑料。",True),
    ("cleaner","clea\nner",True),
    ("Hope in the Ocean","hope in the\nOcean",False),
    ("Save 100 lives.","Save 10\nlives.",False),
    ("Protect the ocean!","Protect the\nocean.",False),
    ("Protect the ocean","Protect ocean",False),
    ("Protect the ocean","Protect  the ocean",False),
    ("Protect the ocean","Protect the oce\u200ban",False),
    ("Line one\nLine two","Line one Line two",False),
])
def test_line_wrap_changes_only_visual_breaks(expected,observed,match):
    item=CopyItem(copy_id="title",text=expected,match_policy="line_wrap")
    assert text_matches(observed,item) is match


def test_exact_still_rejects_newline_and_typographic_remains_opt_in():
    assert not text_matches("Hope\nin the Ocean",CopyItem(copy_id="title",text="Hope in the Ocean"))
    assert not text_matches("HOPE",CopyItem(copy_id="title",text="Hope",match_policy="line_wrap"))
