"""X23: what a repo MENTIONS - the names it leans on, read out of its files.

``extract`` takes ``{repo-relative path: text}`` and returns ``Fact``s of the
kinds in ``model.MENTIONED``. ``doc_refs`` is the prose reader shared with the
docs map. Pure: no git, no disk, never raises on bad input.
"""

from __future__ import annotations

from .mentions_ci import scan_ci_yaml, scan_clones, scan_submodules
from .mentions_deps import is_requirements, scan_deps
from .mentions_images import scan_dockerfile, scan_images
from .mentions_net import scan_cites, scan_net
from .mentions_repos import scan_repo_names
from .mentions_sink import Sink, split_lines
from .model import CODE_EXTS, DOC, Fact, is_ci, is_doc, wanted
from .prose import doc_refs, iter_refs

__all__ = ["extract", "doc_refs"]

_SCRIPT_BASES = ("makefile", "jenkinsfile")


def _one(path: str, text: str, sink: Sink) -> None:
    lines = split_lines(text)
    base = path.rpartition("/")[2].lower()
    scan_cites(path, lines, sink)
    doc = is_doc(path) and not is_requirements(path)
    ci = is_ci(path)
    scan_repo_names(path, lines, sink, doc=doc, ci=ci)
    if doc:
        for n, repo, target in iter_refs(lines):
            sink.add(DOC, repo, path, n, "", target)
        return
    if ci:
        scan_ci_yaml(path, lines, sink)
    if ci or base.endswith(".sh") or base.startswith(_SCRIPT_BASES):
        scan_clones(path, lines, sink)
    if base == ".gitmodules":
        scan_submodules(path, lines, sink)
    scan_deps(path, text, sink)
    code = base.endswith(CODE_EXTS)
    if base.startswith("dockerfile") or base.endswith(".dockerfile"):
        scan_dockerfile(path, lines, sink)
    elif not code:
        scan_images(path, lines, sink)
    scan_net(path, lines, sink, code)


def extract(files: dict[str, str]) -> list[Fact]:
    """Every mention in ``files``, deduped on (kind, key, path, target) at the
    first line, sorted by (path, line, kind, key, target)."""
    sink = Sink()
    for path, text in (files or {}).items():
        if not isinstance(path, str) or not isinstance(text, str) or not wanted(path):
            continue
        try:
            _one(path, text, sink)
        except Exception:  # noqa: BLE001 - one bad file never sinks the scan
            continue
    return sink.result()
