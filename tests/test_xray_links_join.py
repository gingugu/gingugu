"""X23: joining mentions to providers, and walking the result across repos.

``join.join(repos)`` turns every repo's provided and mentioned facts into
``Link``s: a mention in B of a name A provides is a link B -> A. ``ripple``
walks links backwards from a change: who leans on what changed, then who
leans on them. Pure: facts in, links out.
"""

from __future__ import annotations

from gingugu.xray.links import join, model, ripple
from gingugu.xray.links.join import RepoFacts
from gingugu.xray.links.model import Fact


def F(kind, key, path="x", line=1, detail="", target=""):  # noqa: N802 - a fixture builder
    return Fact(kind, key, path, line, detail, target)


def repo(provided=(), mentioned=(), files=()):
    return RepoFacts(list(provided), list(mentioned), frozenset(files))


def links(repos, **kw):
    return join.join(repos, **kw).links


def only(found, **want):
    hits = [lk for lk in found if all(getattr(lk, k) == v for k, v in want.items())]
    assert len(hits) == 1, f"want one {want}, got {hits} from {found}"
    return hits[0]


# --- repo-level links ----------------------------------------------------------


def test_ci_checkout_links_to_the_repo_with_its_ref_and_pinning():
    repos = {
        "acme/a": repo(files=["rig/run.py"]),
        "acme/b": repo(mentioned=[F(model.CI, "acme/a", ".github/workflows/t.yml", 7, "main")]),
    }
    lk = only(links(repos), kind="ci")
    assert (lk.src, lk.src_path, lk.src_line, lk.dst, lk.dst_path) == (
        "acme/b", ".github/workflows/t.yml", 7, "acme/a", None,
    )  # fmt: skip
    assert lk.detail == "main" and "unpinned" in lk.flags and lk.certain


def test_a_sha_or_tag_ref_is_pinned():
    repos = {
        "acme/a": repo(),
        "acme/b": repo(mentioned=[F(model.CI, "acme/a", detail="v1.2.0")]),
        "acme/c": repo(mentioned=[F(model.REPO_DEP, "acme/a", detail="a" * 40)]),
    }
    assert all("unpinned" not in lk.flags for lk in links(repos))


def test_a_bare_name_matches_by_name_and_is_uncertain_when_two_share_it():
    repos = {
        "acme/rig": repo(),
        "other/rig": repo(),
        "acme/b": repo(mentioned=[F(model.CITE, "rig", "n.md", 1, "abc1234")]),
        "acme/c": repo(mentioned=[F(model.CI, "acme/rig")]),
    }
    found = links(repos)
    cites = [lk for lk in found if lk.kind == "cite"]
    assert {lk.dst for lk in cites} == {"acme/rig", "other/rig"}
    assert not any(lk.certain for lk in cites) and all("ambiguous" in lk.flags for lk in cites)
    assert only(found, kind="ci").certain


def test_a_repo_known_by_folder_name_matches_an_owner_qualified_mention_uncertainly():
    repos = {"rig": repo(), "acme/b": repo(mentioned=[F(model.CI, "acme/rig")])}
    lk = only(links(repos), kind="ci")
    assert lk.dst == "rig" and not lk.certain


def test_mentions_of_yourself_and_of_unknown_repos_are_not_links():
    repos = {
        "acme/a": repo(mentioned=[F(model.CI, "acme/a"), F(model.CI, "acme/nowhere")]),
    }
    assert links(repos) == []


def test_submodule_is_a_source_link_and_needs_no_ref():
    repos = {"acme/rig": repo(), "acme/b": repo(mentioned=[F(model.SUBMODULE, "acme/rig")])}
    lk = only(links(repos), kind="source")
    assert lk.flags == ()


# --- named things ------------------------------------------------------------------


def test_dependency_on_a_published_package_points_at_its_manifest():
    repos = {
        "acme/b": repo(provided=[F(model.PACKAGE, "py:acme-b", "pyproject.toml", 3)]),
        "acme/c": repo(mentioned=[F(model.DEP, "py:acme-b", "pyproject.toml", 9, ">=1.2")]),
    }
    lk = only(links(repos), kind="pin")
    assert (lk.src, lk.dst, lk.dst_path, lk.detail) == (
        "acme/c",
        "acme/b",
        "pyproject.toml",
        ">=1.2",
    )


def test_image_exact_is_certain_and_last_segment_is_partial():
    repos = {
        "acme/a": repo(provided=[F(model.IMAGE, "team/svc-a", ".github/workflows/b.yml")]),
        "acme/d": repo(
            mentioned=[F(model.IMAGE, "team/svc-a", "k8s/a.yaml"), F(model.IMAGE, "svc-a", "c.yml")]
        ),
    }
    found = links(repos)
    assert only(found, src_path="k8s/a.yaml").certain
    partial = only(found, src_path="c.yml")
    assert partial.kind == "deploy" and not partial.certain and "partial" in partial.flags


def test_host_exact_and_wildcard():
    repos = {
        "acme/a": repo(
            provided=[
                F(model.HOST, "svc-a.acme.io", "k8s/ing.yaml"),
                F(model.HOST, "*.edge.acme.io"),
            ]
        ),
        "acme/b": repo(
            mentioned=[
                F(model.HOST, "svc-a.acme.io", "src/c.py"),
                F(model.HOST, "x.edge.acme.io", "e"),
            ]
        ),
    }
    found = links(repos)
    exact = only(found, src_path="src/c.py")
    assert exact.kind == "runtime" and exact.dst_path == "k8s/ing.yaml" and exact.certain
    wild = only(found, src_path="e")
    assert not wild.certain and "wildcard" in wild.flags


def test_routes_exact_suffix_generic_and_standard():
    provided = [
        F(model.ROUTE, "/internal/resolve", "svc/api.py", 12),
        F(model.ROUTE, "/keys/*/rotate", "svc/keys.py", 3),
        F(model.ROUTE, "/health", "svc/api.py", 2),
        F(model.ROUTE, "/v1/chat/completions", "svc/proxy.py", 5),
        F(model.HOST, "gw.acme.io", "k8s/i.yaml"),
    ]
    mentioned = [
        F(model.ROUTE, "/internal/resolve", "src/client.ts", 4),
        F(model.ROUTE, "/api/v2/keys/*/rotate", "src/keys.ts", 8),
        F(model.ROUTE, "/health", "src/probe.ts"),
        F(model.ROUTE, "/v1/chat/completions", "src/llm.ts"),
        F(model.ROUTE, "/v1/chat/completions", "src/gw.ts"),
        F(model.HOST, "gw.acme.io", "src/gw.ts"),
        F(model.ROUTE, "/internal", "src/short.ts"),
    ]
    found = links({"acme/a": repo(provided=provided), "acme/b": repo(mentioned=mentioned)})
    routes = {(lk.src_path, lk.dst_path, lk.certain) for lk in found if lk.key.startswith("/")}
    assert routes == {
        ("src/client.ts", "svc/api.py", True),  # exact
        ("src/keys.ts", "svc/keys.py", False),  # the provider misses a mount prefix
        ("src/gw.ts", "svc/proxy.py", True),  # standard path, and the file names the host
    }  # fmt: skip


def test_a_one_segment_route_never_suffix_matches():
    found = links(
        {
            "acme/a": repo(provided=[F(model.ROUTE, "/resolve", "a.py")]),
            "acme/b": repo(mentioned=[F(model.ROUTE, "/internal/resolve", "b.ts")]),
        }
    )
    assert found == []


def test_resource_names_exact_and_glob():
    repos = {
        "acme/infra": repo(
            provided=[
                F(model.RESOURCE, "usage-ledger", "tf/d.tf", 2),
                F(model.RESOURCE, "*-jobs-queue"),
            ]
        ),
        "acme/a": repo(
            mentioned=[
                F(model.NAME, "usage-ledger", "src/db.py"),
                F(model.NAME, "prod-jobs-queue", "q"),
            ]
        ),
    }
    found = links(repos)
    assert only(found, src_path="src/db.py").certain
    glob = only(found, src_path="q")
    assert glob.kind == "data" and not glob.certain and "pattern" in glob.flags


def test_two_providers_make_both_links_ambiguous():
    repos = {
        "acme/a": repo(provided=[F(model.HOST, "x.acme.io")]),
        "acme/b": repo(provided=[F(model.HOST, "x.acme.io")]),
        "acme/c": repo(mentioned=[F(model.HOST, "x.acme.io")]),
    }
    found = links(repos)
    assert {lk.dst for lk in found} == {"acme/a", "acme/b"}
    assert all(not lk.certain and "ambiguous" in lk.flags for lk in found)


def test_a_name_the_mentioner_provides_too_is_uncertain():
    repos = {
        "acme/a": repo(provided=[F(model.ROUTE, "/internal/resolve", "api.py")]),
        "acme/b": repo(
            provided=[F(model.ROUTE, "/internal/resolve", "vendor-spec.yaml")],
            mentioned=[F(model.ROUTE, "/internal/resolve", "client.ts")],
        ),
    }
    lk = only(links(repos), kind="runtime")
    assert lk.dst == "acme/a" and not lk.certain and "also-own" in lk.flags


# --- citations and docs -------------------------------------------------------------


def test_cite_points_at_the_cited_file_or_flags_it_missing():
    repos = {
        "acme/b": repo(files=["src/proxy.ts"]),
        "acme/a": repo(
            mentioned=[
                F(model.CITE, "acme/b", "c.json", 1, "4f2a9c1", "src/proxy.ts"),
                F(model.CITE, "acme/b", "d.json", 1, "4f2a9c1", "src/gone.ts"),
                F(model.CITE, "acme/b", "e.json", 1, "4f2a9c1", ""),
            ]
        ),
    }
    found = links(repos)
    assert only(found, src_path="c.json").dst_path == "src/proxy.ts"
    missing = only(found, src_path="d.json")
    assert missing.dst_path is None and "missing" in missing.flags
    assert only(found, src_path="e.json").dst_path is None


def test_doc_refs_resolve_to_one_file_or_are_dropped():
    repos = {
        "acme/gateway": repo(files=["guardrails/resolver.py", "a/util.py", "b/util.py"]),
        "acme/tt": repo(
            mentioned=[
                F(model.DOC, "acme/gateway", "docs/R.md", 3, target="guardrails/resolver.py"),
                F(model.DOC, "acme/gateway", "docs/R.md", 4, target="resolver.py"),
                F(model.DOC, "acme/gateway", "docs/R.md", 5, target="util.py"),
                F(model.DOC, "acme/gateway", "docs/R.md", 6, target="nope/missing.py"),
            ]
        ),
    }
    found = links(repos)
    assert {(lk.kind, lk.dst_path, lk.src_line) for lk in found} == {
        ("docs", "guardrails/resolver.py", 3)
    }


def test_the_same_link_from_one_file_is_reported_once_at_its_first_line():
    repos = {
        "acme/a": repo(provided=[F(model.HOST, "a.acme.io", "i.yaml")]),
        "acme/b": repo(
            mentioned=[F(model.HOST, "a.acme.io", "c.py", 9), F(model.HOST, "a.acme.io", "c.py", 2)]
        ),
    }
    assert [lk.src_line for lk in links(repos)] == [2]


# --- shared private hosts nobody here serves -------------------------------------------


def test_private_hosts_two_repos_call_but_none_provides_are_shared():
    repos = {
        "acme/a": repo(mentioned=[F(model.HOST, "ledger.acme.internal", "a.py", 3)]),
        "acme/b": repo(mentioned=[F(model.HOST, "ledger.acme.internal", "b.ts", 5)]),
        "acme/c": repo(mentioned=[F(model.HOST, "api.openai.com", "c.py")]),
        "acme/d": repo(mentioned=[F(model.HOST, "api.openai.com", "d.py")]),
    }
    result = join.join(repos)
    assert result.links == []
    assert [(s.key, s.repos) for s in result.shared] == [
        ("ledger.acme.internal", ("acme/a", "acme/b"))
    ]


# --- ripple: who leans on a change ----------------------------------------------------------


def _eco():
    return [
        # b's client calls a's resolver route; c's CI checks out a; d pins b's package
        model.Link(
            "acme/b", "src/client.ts", 4, "acme/a", "svc/api.py", "runtime", "/internal/resolve"
        ),
        model.Link("acme/c", ".github/workflows/t.yml", 7, "acme/a", None, "ci", "acme/a", "main"),
        model.Link("acme/d", "pyproject.toml", 2, "acme/b", "package.json", "pin", "npm:b", "^1"),
        model.Link(
            "acme/e", "src/x.ts", 1, "acme/b", "src/client.ts", "runtime", "/k", certain=False
        ),
        model.Link("acme/f", "f.py", 1, "acme/c", None, "ci", "acme/c"),
    ]


def test_ripple_from_changed_files_follows_file_links_and_repo_links_into_the_start():
    reach = ripple.ripple(_eco(), "acme/a", ["svc/api.py"])
    got = {(r.repo, r.hop) for r in reach}
    # f leans on c as a whole, and c did not change: repo-level links count only into the start
    assert got == {("acme/b", 1), ("acme/c", 1), ("acme/e", 2)}
    e = next(r for r in reach if r.repo == "acme/e")
    assert not e.certain and [lk.src_path for lk in e.via] == ["src/x.ts"]


def test_ripple_needs_the_changed_file_for_a_file_link():
    reach = ripple.ripple(_eco(), "acme/a", ["svc/other.py"])
    assert {r.repo for r in reach} == {"acme/c"}


def test_ripple_closure_widens_each_repo_by_its_own_blast():
    def closure(repo_id, files):
        # in b, client.ts is imported by package.json's entry (stand-in for an import blast)
        return set(files) | ({"package.json"} if repo_id == "acme/b" else set())

    reach = ripple.ripple(_eco(), "acme/a", ["svc/api.py"], closure=closure)
    assert ("acme/d", 2) in {(r.repo, r.hop) for r in reach}


def test_ripple_without_files_starts_from_every_link_into_the_repo():
    reach = ripple.ripple(_eco(), "acme/b", None)
    assert {(r.repo, r.hop) for r in reach} == {("acme/d", 1), ("acme/e", 1)}


def test_ripple_stops_at_its_hop_limit_and_never_returns_to_the_start():
    chain = [model.Link(f"r/{i + 1}", "f", 1, f"r/{i}", "f", "runtime", "k") for i in range(6)] + [
        model.Link("r/0", "f", 1, "r/3", "f", "runtime", "k")
    ]
    reach = ripple.ripple(chain, "r/0", ["f"], hops=3)
    assert [(r.repo, r.hop) for r in reach] == [("r/1", 1), ("r/2", 2), ("r/3", 3)]


# --- route precision: a route alone is a weak name ----------------------------------


def _route_case(provided, mentioned):
    return links({"acme/a": repo(provided=provided), "acme/b": repo(mentioned=mentioned)})


def test_a_mention_parameter_never_matches_a_provider_name():
    found = _route_case(
        [F(model.ROUTE, "/*/message", "a.py"), F(model.HOST, "a.acme.io")],
        [F(model.ROUTE, "/messages/*", "b.ts"), F(model.HOST, "a.acme.io", "cfg.yaml")],
    )
    assert [lk for lk in found if lk.key.startswith("/")] == []


def test_without_a_host_only_a_distinctive_whole_route_survives_flagged():
    found = _route_case(
        [
            F(model.ROUTE, "/internal/resolve", "a.py"),
            F(model.ROUTE, "/upload", "u.py"),
            F(model.ROUTE, "/v2/keys/*", "k.py", detail=model.UNMOUNTED),
        ],
        [
            F(model.ROUTE, "/internal/resolve", "b.ts"),
            F(model.ROUTE, "/upload", "c.ts"),
            F(model.ROUTE, "/v2/keys/*", "d.ts"),
            F(model.ROUTE, "/api/internal/resolve", "e.ts"),
        ],
    )
    assert [(lk.src_path, lk.certain, lk.flags) for lk in found] == [
        ("b.ts", False, ("route-only",))
    ]


def test_a_host_anywhere_in_the_repo_corroborates_its_routes():
    found = _route_case(
        [
            F(model.HOST, "a.acme.io", "k8s/i.yaml"),
            F(model.ROUTE, "/internal/resolve", "a.py"),
            F(model.ROUTE, "/v2/keys/*", "k.py", detail=model.UNMOUNTED),
        ],
        [
            F(model.HOST, "a.acme.io", "values.yaml"),
            F(model.ROUTE, "/internal/resolve", "b.ts"),
            F(model.ROUTE, "/v2/keys/*", "d.ts"),
            F(model.ROUTE, "/api/internal/resolve", "e.ts"),
        ],
    )
    got = {(lk.src_path, lk.certain, lk.flags) for lk in found if lk.key.startswith("/")}
    assert got == {
        ("b.ts", True, ()), ("d.ts", False, ("unmounted",)), ("e.ts", False, ("suffix",)),
    }  # fmt: skip
