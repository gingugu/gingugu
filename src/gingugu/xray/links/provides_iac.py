"""X23: names infrastructure-as-code creates - Terraform, CloudFormation, CDK."""

from __future__ import annotations

import re
from collections.abc import Iterator

from gingugu.xray.links.model import HOST, IMAGE, MAX_KEY_CHARS, RESOURCE
from gingugu.xray.links.provides_util import Hit

_RES_TYPES = frozenset(
    {"aws_dynamodb_table", "aws_s3_bucket", "aws_sqs_queue", "aws_sns_topic",
     "aws_kinesis_stream", "aws_secretsmanager_secret", "aws_ssm_parameter",
     "aws_lambda_function", "google_pubsub_topic", "google_pubsub_subscription",
     "google_storage_bucket"}
)  # fmt: skip
_NAME_ATTRS = frozenset({"name", "bucket", "function_name"})
_TF_RES = re.compile(r'^\s*resource\s+"([\w-]+)"\s+"[^"]*"\s*\{')
_TF_ATTR = re.compile(r"^\s*(\w+)\s*=\s*(.*)$")
_TF_DOMAIN = re.compile(r'^\s*domain_name\s*=\s*"([^"]*)"')
_STR = re.compile(r'"([^"\n]*)"')
_CFN = re.compile(
    r'^\s*-?\s*["\']?(?:Table|Bucket|Queue|Topic|Stream|Function)Name["\']?\s*:\s*(.*)$'
)
_CDK_MARK = ("aws-cdk-lib", "aws_cdk", "@aws-cdk")
_CDK = re.compile(
    r"\b(?:table|bucket|queue|topic|stream|function)(?:Name|_name)\b\s*[:=]\s*"
    r"([\"'`])([^\"'`\n]*)\1"
)
_INTERP = re.compile(r"\$\{[^}]*\}")


def norm_resource(raw: str) -> str | None:
    """An IaC-owned name with each interpolation as ``*``; None when it is too
    vague (under four literal characters), spaced, or still templated."""
    s = _INTERP.sub("*", raw.strip())
    if not s or len(s) > MAX_KEY_CHARS or re.search(r"\s", s) or "$" in s or "{{" in s:
        return None
    if sum(1 for c in s if c != "*") < 4:
        return None
    return s


def _tf_attrs(lines: list[str]) -> Iterator[tuple[str, str, str, int]]:
    """(resource type, attribute, value text, line) for each depth-1 attribute of
    a ``resource`` block. Linear: braces are counted per line, never recursed."""
    rtype = None
    depth = 0
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        i += 1
        if rtype is None:
            m = _TF_RES.match(line)
            if m:
                depth = line.count("{") - line.count("}")
                rtype = m.group(1) if depth > 0 else None
            continue
        if depth == 1:
            m = _TF_ATTR.match(line)
            if m:
                val, ln = m.group(2), i
                if val.lstrip().startswith("[") and "]" not in val:
                    for _ in range(200):
                        if i >= n or "]" in val:
                            break
                        val += " " + lines[i]
                        i += 1
                yield rtype, m.group(1), val, ln
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            rtype = None


def terraform(lines: list[str]) -> list[Hit]:
    out: list[Hit] = []
    for n, line in enumerate(lines, 1):
        m = _TF_DOMAIN.match(line)
        if m:
            out.append((HOST, m.group(1), n))
    for rtype, attr, val, n in _tf_attrs(lines):
        first = _STR.match(val.strip())
        if rtype == "aws_ecr_repository" and attr == "name" and first:
            out.append((IMAGE, first.group(1), n))
        elif rtype in _RES_TYPES and attr in _NAME_ATTRS and first:
            name = norm_resource(first.group(1))
            if name:
                out.append((RESOURCE, name, n))
        elif rtype == "aws_route53_record" and attr == "name" and first:
            out.append((HOST, first.group(1), n))
        elif (rtype == "aws_acm_certificate" and attr == "subject_alternative_names") or (
            rtype == "aws_cloudfront_distribution" and attr == "aliases"
        ):
            out.extend((HOST, s, n) for s in _STR.findall(val))
    return out


def cloudformation(lines: list[str]) -> list[Hit]:
    out: list[Hit] = []
    for n, line in enumerate(lines, 1):
        m = _CFN.match(line)
        if not m:
            continue
        v = m.group(1).strip().rstrip(",").strip()
        if v.startswith("!Sub"):
            v = v[4:].strip()
        elif v.startswith(("!", "{", "[")):
            continue
        if v.endswith(","):
            v = v[:-1]
        name = norm_resource(_unq(v))
        if name:
            out.append((RESOURCE, name, n))
    return out


def _unq(v: str) -> str:
    v = v.strip()
    if v[:1] in ('"', "'"):
        end = v.find(v[0], 1)
        return v[1:end] if end > 0 else v[1:]
    return v.split(" #", 1)[0]


def cdk(text: str, lines: list[str]) -> list[Hit]:
    if not any(mark in text for mark in _CDK_MARK):
        return []
    out: list[Hit] = []
    for n, line in enumerate(lines, 1):
        for m in _CDK.finditer(line):
            name = norm_resource(m.group(2))
            if name:
                out.append((RESOURCE, name, n))
    return out
