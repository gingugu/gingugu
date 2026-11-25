"""X7a on Terraform: the names an ``environment`` block sets in a ``.tf`` file.

In a Terraform-heavy repo nearly every environment variable is set there - a
Lambda's ``environment { variables = { NAME = ... } }`` and an ECS container
definition's ``environment = [{ name = "NAME", value = ... }]`` - so without
this reader every name the code reads comes out as "never set here". Names
and lines only, never a value, same as every other reader.
"""

from __future__ import annotations

import pytest

from gingugu.xray import env_map, env_scan
from tests.test_xray_edit_hook import _repo

LAMBDA = (
    'resource "aws_lambda_function" "router" {\n'  # 1
    '  function_name = "router"\n'  # 2
    '  handler       = "app.lambda_handler"\n'  # 3
    "  environment {\n"  # 4
    "    variables = {\n"  # 5
    "      QUERY_WORKGROUP        = var.workgroup\n"  # 6
    '      "METRIC_NAMESPACE" = "router"\n'  # 7
    "      LOG_LEVEL = upper(var.level)\n"  # 8
    "    }\n"  # 9
    "  }\n"  # 10
    "  TIMEOUT_NOT_ENV = 3\n"  # 11: outside the block
    "}\n"  # 12
)

ECS = (
    'resource "aws_ecs_task_definition" "api" {\n'  # 1
    "  container_definitions = jsonencode([{\n"  # 2
    '    name  = "api"\n'  # 3: lowercase: the container, not a variable
    "    environment = [\n"  # 4
    '      { name = "DB_HOST", value = var.db_host },\n'  # 5
    "      {\n"  # 6
    '        name  = "DB_PORT"\n'  # 7
    '        value = "5432"\n'  # 8
    "      },\n"  # 9
    "    ]\n"  # 10
    '    secrets = [{ name = "NOT_AN_ENV_SET", valueFrom = var.arn }]\n'  # 11
    "  }])\n"  # 12
    "}\n"  # 13
)


def test_a_tf_file_has_a_reader():
    assert env_scan.kind("infra/lambda.tf") == "hcl"
    assert env_scan.kind("main.tf") == "hcl"
    assert env_scan.kind("terraform.tfvars") is None  # values, never env names
    assert env_scan.kind("x.tf.json") is None


def test_lambda_variables_map_keys_are_sets_and_nothing_outside_the_block():
    reads, sets = env_scan.scan_text("lambda.tf", LAMBDA)
    assert reads == []
    assert sets == [("QUERY_WORKGROUP", 6), ("METRIC_NAMESPACE", 7), ("LOG_LEVEL", 8)]


def test_ecs_environment_list_names_are_sets_and_secrets_are_not():
    reads, sets = env_scan.scan_text("ecs.tf", ECS)
    assert reads == []
    assert sets == [("DB_HOST", 5), ("DB_PORT", 7)]


def test_single_line_blocks():
    text = (
        "environment { variables = { A_ONE = 1, B_TWO = var.b } }\n"
        'environment = [{ name = "C_THREE", value = "x" }]\n'
    )
    assert env_scan.scan_text("x.tf", text)[1] == [("A_ONE", 1), ("B_TWO", 1), ("C_THREE", 2)]


def test_json_container_definitions_in_a_heredoc():
    text = (
        "container_definitions = <<EOF\n"  # 1
        "[{\n"  # 2
        '  "environment": [\n'  # 3
        '    {"name": "JSON_ONE", "value": "1"},\n'  # 4
        '    {"name": "JSON_TWO", "value": "2"}\n'  # 5
        "  ]\n"  # 6
        "}]\n"  # 7
        "EOF\n"  # 8
    )
    assert env_scan.scan_text("x.tf", text)[1] == [("JSON_ONE", 4), ("JSON_TWO", 5)]


def test_a_value_is_never_mistaken_for_a_name():
    text = (
        "environment {\n"
        "  variables = {\n"
        '    REAL_NAME = "FAKE_NAME=1"\n'  # a key inside a quoted value is no key
        "    OTHER = local.UPPER_LOCAL\n"  # nor is an attribute after a dot
        "  }\n"
        "}\n"
    )
    assert env_scan.scan_text("x.tf", text)[1] == [("REAL_NAME", 3), ("OTHER", 4)]


def test_comments_and_braces_in_strings_do_not_open_or_close_a_block():
    text = (
        "# environment {\n"  # 1
        "// environment {\n"  # 2
        "locals {\n"  # 3
        '  NOT_SET = "environment {"\n'  # 4
        "}\n"  # 5
        "environment {\n"  # 6
        "  variables = {\n"  # 7
        '    KEPT = "}"\n'  # 8: a brace inside a string closes nothing
        "    # COMMENTED = 1\n"  # 9
        "    ALSO_KEPT = 2\n"  # 10
        "  }\n"  # 11
        "}\n"  # 12
        "AFTER = 1\n"  # 13
    )
    assert env_scan.scan_text("x.tf", text)[1] == [("KEPT", 8), ("ALSO_KEPT", 10)]


def test_an_unclosed_block_ends_quietly():
    text = "environment {\n  variables = {\n    HALF = 1\n"
    assert env_scan.scan_text("x.tf", text)[1] == [("HALF", 3)]


def test_a_huge_tf_file_scans_in_linear_time():
    body = "environment {\n  variables = {\n" + "    A_B = 1\n" * 50_000 + "  }\n}\n"
    sets = env_scan.scan_text("big.tf", body)[1]
    assert len(sets) == 50_000


# --- end to end: the map --------------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    return _repo(
        tmp_path,
        {
            "app/handler.py": (
                "import os\n"
                'WG = os.environ["QUERY_WORKGROUP"]\n'
                'NS = os.getenv("METRIC_NAMESPACE")\n'
                'GONE = os.getenv("NEVER_SET_ANYWHERE")\n'
            ),
            "infra/lambda.tf": LAMBDA,
        },
    )


def test_the_map_sees_terraform_sets(repo, tmp_path):
    m = env_map.read(repo, tmp_path / "hook-sessions")
    assert m.sets["QUERY_WORKGROUP"] == [("infra/lambda.tf", 6)]
    assert m.sets["METRIC_NAMESPACE"] == [("infra/lambda.tf", 7)]
    assert "NEVER_SET_ANYWHERE" not in m.sets
