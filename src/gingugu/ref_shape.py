"""The shape of a PR/MR reference, shared by the claims ledger and review hints.

``claim_qualify`` reads a ref to record a claim about it; ``staleness`` reads one
to decide whether a review hint fires. When the two disagreed (#65 fixed
``claims``, ``staleness`` kept the old shape until 0.55.2), a ref the ledger had
settled could still flag, or the reverse. Each module builds its own pattern -
one needs repo qualification, the other a status word nearby - from these
pieces, so the shape is defined once.

The sigil is required and only spaces/tabs may separate it from the kind:
"PR 3" is a plan ordinal, and a "PR" ending one line never binds to a "#5"
opening the next.
"""

from __future__ import annotations

KINDS = r"PR|MR|pull request|merge request"
SIGIL = r"[ \t]*[#!]"
# kind + sigil + number, no groups; callers add \b and captures as they need.
REF = rf"(?:{KINDS}){SIGIL}\d+"
