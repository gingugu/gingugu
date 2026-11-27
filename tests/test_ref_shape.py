"""Board item 6: the claims ledger and review hints read one PR/MR ref shape.

The two used to carry their own copies; #65 fixed one and the other kept the
old shape until 0.55.2. Both now build from ``ref_shape``, and this pins that
they agree on what is a ref and what is not.
"""

from __future__ import annotations

import pytest

from gingugu import claim_qualify, staleness

REFS = ["PR #12", "MR !7", "pull request #3", "merge request !40", "PR\t#9"]
NOT_REFS = ["PR 3", "kind pr 140", "PR\n#5", "GDPR #4", "PR#", "pull #2"]


def _ledger_numbers(text: str) -> set[int]:
    return {int(m.group("num")) for m in claim_qualify._REF.finditer(text)}


def _hint_numbers(text: str) -> set[int]:
    return {int(m.group(1)) for m in staleness._REF_NUMBER.finditer(text)}


@pytest.mark.parametrize("text", REFS + NOT_REFS)
def test_ledger_and_hints_agree_on_every_ref(text: str) -> None:
    assert _ledger_numbers(text) == _hint_numbers(text)


def test_the_one_known_difference_is_case() -> None:
    # Pinned, not endorsed: hints match the kind case-insensitively and the
    # ledger does not, so a lowercase "pr #5" can hint but never be settled.
    # Predates the shared shape; changing either side is its own decision.
    assert _hint_numbers("pr #5") == {5}
    assert _ledger_numbers("pr #5") == set()


@pytest.mark.parametrize("text", NOT_REFS)
def test_a_non_ref_is_not_a_ref_anywhere(text: str) -> None:
    assert not _ledger_numbers(text)
    assert not _hint_numbers(text)
