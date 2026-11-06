"""Every version of each `gingugu init` hook template we have shipped.

A hook on disk whose sha256 is in ``previous`` is a stock copy of an older
template: it holds nothing of the user's, so plain ``init`` refreshes it.
Anything else is the user's and is skipped unless ``--force``.

When you edit a template, ``tests/test_bootstrap_refresh.py`` fails until you
move its old ``current`` hash into ``previous`` and pin the new one. That keeps
the outgoing version recognisable; forget it and ``init`` would skip that
version forever.
"""

from __future__ import annotations

SHIPPED: dict[str, dict] = {
    "session_start.py.tmpl": {
        "current": "8bf3e97e45b0a6ce3c724c8e249589090c672ef43f78eaff68273ac790e43aea",
        "previous": frozenset(
            {
                "4161085ca8a999c79747262a5c0cdb565292576d8690013e4508d304b4190e18",
                "ccd5c343020ee0ef897693b9adfa9889a4d0cac2f1977fd1d934d0f650a57752",
                "f8efd3bb57be3784673e0d7b83b7e393fcde708ef24f3028ca08fe18f567cbad",
                "d778da362f20e338f8b175d6e8bd5f931f9f39d37066c0f3f6f62a5f6c343505",
                "bd5909ccefa58541b8c8422b2d58e3016e242f6dc43bf4db469d2f1e948ecdf6",
                "e00a562001e24409f850bd7f2bbdcadc0563f8f4d5b37cbe988346f20218c656",
                "a2291734ba3b3a18a004137314bd709ab0bd0cca1279c8ba579daabf4bd7ac6a",
                "002c30e991f89a260cb022c4b0d013f9cca785297a45daa0299bed2b9dbf5f56",
                "7e0e1d5db37babc18780ce02cb035a652237d8f3777597571bab8147964fe644",
            }
        ),
    },
    "stop.py.tmpl": {
        "current": "1dbc36b81e625f57283db3ad4c9b134cfed00c05dad259d8d4cdfee787af190a",
        "previous": frozenset(
            {
                "a7c0b1a7d92eea2f6f3743cd98113ae43857b4f77851f9c59a03047a99e80652",
                "19903f450d94452781f75099cb97e2c058124030ef72c4808a835910d0ec080a",
                "bfdd1e882203d2ba12b552d96ff1a2b20aebaf2df1b4f1d0ea21532906c76c45",
            }
        ),
    },
    "user_prompt_recall.py.tmpl": {
        "current": "f137790807af2cc97d79529ca0e6ed7d1ee50734722d145ced93d6874bd73f18",
        "previous": frozenset(
            {
                "fcfccec5ea3505918dad6cfa768c86117d569508abbf57a23f7fccaf7728ab6a",
            }
        ),
    },
    "pre_tool_tripwire.py.tmpl": {
        "current": "a73dd0b302aa49bc62f40a2b96131cdb4a0291ea086c539c3c37c238320fb37c",
        "previous": frozenset(
            {
                "8b02bab59e332ad095dfc8f65f6fa34ccc29046085cc236e09928a6798dd9ee4",
                "c67ffe171420fb246a1769f3f79c1a4503af15b2b6c58f80c1cdb40f79a5b079",
                "d90418042828942128b6fbe8f5e1725ab164d08847d95edbfb0098d077af2ec1",
                "0cba9f6438a2859c0e28cba8b2bf138afb08682323ebaa0d7192fc2ed8a3dfc7",
            }
        ),
    },
    "post_tool_xray.py.tmpl": {
        "current": "0fd7bccc4bec177a6f7695afb518d09b2e0d72ae4c1d3c54e6db98abf2c8df0d",
        "previous": frozenset({}),
    },
    "post_bash_xray.py.tmpl": {
        "current": "e2c08f0dca0209b447dd8c537d468e7de254de24ab9dd02dda59c6867ba7f2c1",
        "previous": frozenset(
            {
                "0fd7bccc4bec177a6f7695afb518d09b2e0d72ae4c1d3c54e6db98abf2c8df0d",
            }
        ),
    },
    "post_edit_xray.py.tmpl": {
        "current": "edbe03b598a40262ef571b3927f78a7e74d413da5c093999e359f70451b0572e",
        "previous": frozenset(
            {
                "e2c08f0dca0209b447dd8c537d468e7de254de24ab9dd02dda59c6867ba7f2c1",
                "0fd7bccc4bec177a6f7695afb518d09b2e0d72ae4c1d3c54e6db98abf2c8df0d",
            }
        ),
    },
    "subagent_warmup.py.tmpl": {
        "current": "fd0731727049609b2dd8cb859ba2b7f9eadeccb4974d3f144e0ab76d52ffdc0e",
        "previous": frozenset({}),
    },
}


def previous_versions(template: str) -> frozenset[str]:
    """sha256 of every older shipped version of ``template`` (empty if unknown)."""
    entry = SHIPPED.get(template)
    return entry["previous"] if entry else frozenset()
