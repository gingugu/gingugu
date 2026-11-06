"""Untrusted text made safe to show a person: one sanitizer for the whole package.

A repo directory name, a memory title or a credential service name can carry
terminal escapes (an OSC title set, a screen clear), line breaks or bidi
overrides that make a prompt or a report read as something else. ``clean``
drops every ESC sequence outright and turns each remaining C0, DEL, C1,
zero-width or bidi character into a space, so the text stays on one line and
keeps its length roughly the same. ``confirm.shown`` builds on it for dialogs;
the CLI show runs every name and result line through it before drawing.
"""

from __future__ import annotations

import re
from typing import Any

# CSI, OSC (ended by BEL or ST, or by the end of the text), DCS/SOS/PM/APC,
# then any other two-byte escape. A bare trailing ESC goes too.
_ESC_SEQ = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]"
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)?"
    r"|[PX^_][^\x1b]*(?:\x1b\\)?"
    r"|[ -/]*[0-~])?"
)
UNSHOWABLE = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁦-⁩﻿]")


def clean(text: Any) -> str:
    """``text`` with no escape sequence and no control, zero-width or bidi character."""
    return UNSHOWABLE.sub(" ", _ESC_SEQ.sub("", str(text)))
