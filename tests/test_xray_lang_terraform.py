"""The Terraform adapter: the HCL graph of a repo's ``.tf`` files.

Terraform has no imports, but it has a graph. Every ``.tf`` file in one directory
is one module and shares one namespace, so a reference names the file that
declares it: ``var.region`` leans on the file with ``variable "region"``,
``aws_s3_bucket.logs.arn`` on the file with ``resource "aws_s3_bucket" "logs"``,
``data.aws_iam_policy_document.x`` on its ``data`` block, ``local.tags`` on the
``locals`` that sets ``tags``, ``module.vpc.id`` on the ``module "vpc"`` block.
Those are file edges (``module``, certain).

A directory with ``.tf`` files is a package node, ``terraform:<dir>`` (the root
is ``terraform:.``): a ``module`` block whose ``source`` is a local path points at
the package of that directory (``module``, certain), and the package points at
its files (``member``, certain), as Go's packages do. A registry or git source
is external. A reference to nothing declared in the directory is unresolved.
A file in a directory no local ``module`` block calls is an entry (a root module).

Parsed from bytes with no dependency: comments never declare or reference, and a
reference inside an interpolated string still counts. The adapter never runs
``terraform``.
"""

from __future__ import annotations

import json

import pytest

from gingugu.xray import lang
from gingugu.xray.lang.terraform import ADAPTER
from tests.xray_lang_fixtures import edge, edges, node, run_adapter


def run(sources: dict[str, str]):
    return run_adapter(ADAPTER, sources, {})


VARS = 'variable "region" {\n  type = string\n}\n\nvariable "name" {}\n'
MAIN = (
    'resource "aws_s3_bucket" "logs" {\n'
    "  bucket = var.name\n"
    "  tags   = local.tags\n"
    "}\n"
    'data "aws_iam_policy_document" "read" {\n'
    "  statement {\n"
    '    resources = ["${aws_s3_bucket.logs.arn}/*"]\n'
    "  }\n"
    "}\n"
)
LOCALS = 'locals {\n  tags = { Region = var.region }\n  prefix = "x"\n}\n'
OUTPUTS = (
    'output "policy" {\n  value = data.aws_iam_policy_document.read.json\n}\n'
    'output "vpc" {\n  value = module.vpc.id\n}\n'
)
CALL = 'module "vpc" {\n  source = "./modules/vpc"\n  cidr   = var.region\n}\n'


def test_claims_tf_files_and_is_registered():
    assert ADAPTER.name == "terraform"
    assert ADAPTER.extensions == (".tf",)
    assert ADAPTER.manifests == ()
    assert lang.for_path("infra/main.tf") is ADAPTER
    assert lang.is_test("infra/main.tf") is False


# --- references inside one module -------------------------------------------------------


def test_each_reference_leans_on_the_file_that_declares_it():
    res = run(
        {
            "variables.tf": VARS,
            "main.tf": MAIN,
            "locals.tf": LOCALS,
            "outputs.tf": OUTPUTS,
            "vpc.tf": CALL,
            "modules/vpc/main.tf": 'output "id" {\n  value = "x"\n}\n',
        }
    )
    for src, dst in [
        ("main.tf", "variables.tf"),  # var.name
        ("main.tf", "locals.tf"),  # local.tags
        ("locals.tf", "variables.tf"),  # var.region
        ("outputs.tf", "main.tf"),  # data.aws_iam_policy_document.read
        ("outputs.tf", "vpc.tf"),  # module.vpc
        ("vpc.tf", "variables.tf"),  # var.region
    ]:
        e = edge(res, src, dst)
        assert (e.kind, e.certain, e.implicit) == ("module", True, False), (src, dst)


def test_a_reference_to_a_resource_in_the_same_file_is_no_edge():
    res = run({"main.tf": MAIN, "variables.tf": VARS, "locals.tf": LOCALS})
    assert ("main.tf", "main.tf") not in edges(res)


def test_a_reference_never_crosses_into_another_directory():
    res = run({"a/vars.tf": VARS, "b/main.tf": 'resource "x_y" "z" {\n  n = var.name\n}\n'})
    assert ("b/main.tf", "a/vars.tf") not in edges(res)
    assert res.unresolved == 1


def test_an_undeclared_reference_is_unresolved_and_a_resource_lookalike_is_ignored():
    src = (
        'resource "aws_x" "a" {\n'
        "  n = var.missing\n"  # unresolved
        "  m = each.value\n"  # a meta-argument: never a reference
        "  k = count.index\n"
        "  p = path.module\n"
        "  s = self.id\n"
        "  t = terraform.workspace\n"
        '  u = "plain.text"\n'
        "}\n"
    )
    res = run({"main.tf": src})
    assert res.unresolved == 1


def test_comments_neither_declare_nor_reference():
    res = run(
        {
            "a.tf": '# variable "ghost" {}\n// resource "x_y" "z" {}\n/* variable "v" {} */\n',
            "b.tf": "# var.ghost\nlocals {\n  x = 1 // var.v\n}\n",
        }
    )
    assert ("b.tf", "a.tf") not in edges(res)
    assert res.unresolved == 0


def test_a_heredoc_body_still_references():
    src = 'resource "x_y" "z" {\n  policy = <<EOF\n{"r": "${var.name}"}\nEOF\n}\n'
    res = run({"main.tf": src, "variables.tf": VARS})
    assert edge(res, "main.tf", "variables.tf").kind == "module"


# --- packages, module calls, entries ----------------------------------------------------


def test_each_directory_is_a_package_node_reaching_its_files():
    res = run({"main.tf": MAIN, "modules/vpc/main.tf": "", "modules/vpc/vars.tf": ""})
    root, vpc = node(res, "terraform:."), node(res, "terraform:modules/vpc")
    assert (root.kind, root.lang) == ("package", "terraform")
    assert vpc.kind == "package"
    assert node(res, "main.tf").lang == "terraform"
    for f in ("modules/vpc/main.tf", "modules/vpc/vars.tf"):
        m = edge(res, "terraform:modules/vpc", f)
        assert (m.kind, m.certain) == ("member", True)


def test_a_local_module_source_points_at_its_package():
    res = run({"vpc.tf": CALL, "variables.tf": VARS, "modules/vpc/main.tf": ""})
    e = edge(res, "vpc.tf", "terraform:modules/vpc")
    assert (e.kind, e.certain) == ("module", True)


def test_a_parent_relative_source_resolves():
    res = run(
        {
            "envs/prod/main.tf": 'module "net" {\n  source = "../../modules/net"\n}\n',
            "modules/net/main.tf": "",
        }
    )
    assert edge(res, "envs/prod/main.tf", "terraform:modules/net").kind == "module"


@pytest.mark.parametrize(
    "source",
    [
        "terraform-aws-modules/vpc/aws",
        "git::https://github.com/acme/mods.git//net?ref=v1.2.0",
        "github.com/acme/mods",
        "s3::https://bucket/mod.zip",
    ],
)
def test_a_remote_source_is_external(source):
    res = run({"main.tf": f'module "m" {{\n  source = "{source}"\n}}\n'})
    assert res.external == 1
    assert {d for _, d in edges(res)} == {"main.tf"}  # only the package's member edge


def test_a_local_source_with_no_tf_files_is_unresolved():
    res = run({"main.tf": 'module "m" {\n  source = "./nowhere"\n}\n'})
    assert res.unresolved == 1


def test_a_source_that_escapes_the_repo_is_unresolved():
    res = run({"main.tf": 'module "m" {\n  source = "../../outside"\n}\n'})
    assert res.unresolved == 1


def test_root_modules_are_entries_and_called_modules_are_not():
    res = run({"vpc.tf": CALL, "variables.tf": VARS, "modules/vpc/main.tf": ""})
    assert node(res, "vpc.tf").is_entry and node(res, "variables.tf").is_entry
    assert not node(res, "modules/vpc/main.tf").is_entry
    assert not node(res, "vpc.tf").is_test


# --- parse cache ------------------------------------------------------------------------


def test_parse_round_trips_through_json_and_valid_rejects_junk():
    raw = ADAPTER.parse("main.tf", MAIN.encode())
    assert ADAPTER.valid(json.loads(json.dumps(raw)))
    for junk in (None, [], "x", {"nope": 1}, 3):
        assert not ADAPTER.valid(junk)


def test_parse_depends_only_on_the_bytes():
    assert ADAPTER.parse("a/x.tf", MAIN.encode()) == ADAPTER.parse("b/y.tf", MAIN.encode())


def test_undecodable_bytes_and_unbalanced_braces_never_raise():
    res = ADAPTER.extract({"bad.tf": b'resource "x_y" "z" {\n  a = var.n\n\xff\xfe {{{{\n'}, {})
    assert node(res, "bad.tf").kind == "file"


def test_edges_are_unique():
    res = run({"main.tf": MAIN + MAIN.replace('"logs"', '"logs2"'), "variables.tf": VARS})
    pairs = [(e.src, e.dst) for e in res.edges]
    assert len(pairs) == len(set(pairs))


def test_a_huge_file_parses_in_linear_time():
    body = "".join(f'resource "aws_x" "r{i}" {{\n  n = var.name\n}}\n' for i in range(20_000))
    res = run({"main.tf": body, "variables.tf": VARS})
    assert edge(res, "main.tf", "variables.tf").kind == "module"
