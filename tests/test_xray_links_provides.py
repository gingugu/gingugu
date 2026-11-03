"""X23 spec: what a repo PROVIDES - read where the code sets a thing up.

``provides.extract(files)`` takes ``{repo path: text}`` and returns ``Fact``s of
the kinds package, image, host, route and resource. Pure: no git, no disk. A fact
names the file and 1-based line it was read from; the same (kind, key, path) is
reported once, at its first line.
"""

from __future__ import annotations

import time

import pytest

from gingugu.xray.links import model, provides
from gingugu.xray.links.model import HOST, IMAGE, PACKAGE, RESOURCE, ROUTE


def keys(files: dict[str, str], kind: str) -> set[str]:
    return {f.key for f in provides.extract(files) if f.kind == kind}


def fact(files: dict[str, str], kind: str, key: str) -> model.Fact:
    found = [f for f in provides.extract(files) if f.kind == kind and f.key == key]
    assert found, f"no {kind} {key!r} in {provides.extract(files)}"
    return found[0]


def test_only_provided_kinds_come_out():
    files = {"pyproject.toml": '[project]\nname = "x-y"\n', "app.py": '@app.get("/a/b")\n'}
    assert {f.kind for f in provides.extract(files)} <= set(model.PROVIDED)


def test_same_fact_in_one_file_is_reported_once_at_its_first_line():
    files = {"app.py": 'x\n@app.get("/a/b")\ndef f(): ...\n@app.post("/a/b")\n'}
    found = [f for f in provides.extract(files) if f.kind == ROUTE]
    assert [(f.key, f.line) for f in found] == [("/a/b", 2)]


# --- packages ------------------------------------------------------------------


def test_pyproject_project_name_is_a_python_package():
    files = {
        "pyproject.toml": '[build-system]\nrequires = []\n\n[project]\nname = "Acme_Client.Lib"\n'
    }
    f = fact(files, PACKAGE, "py:acme-client-lib")
    assert (f.path, f.line) == ("pyproject.toml", 5)


def test_poetry_name_and_setup_cfg_name():
    assert keys({"pyproject.toml": '[tool.poetry]\nname = "widget"\n'}, PACKAGE) == {"py:widget"}
    assert keys({"setup.cfg": "[metadata]\nname = gadget\n"}, PACKAGE) == {"py:gadget"}


def test_package_json_name_including_a_scope():
    files = {"web/package.json": '{\n  "name": "@acme/Widget-SDK",\n  "version": "1.0.0"\n}\n'}
    f = fact(files, PACKAGE, "npm:@acme/widget-sdk")
    assert (f.path, f.line) == ("web/package.json", 2)


def test_a_dependency_named_name_is_not_the_package_name():
    files = {"package.json": '{"dependencies": {"name": "1.0"}}'}
    assert keys(files, PACKAGE) == set()


def test_go_mod_module_path():
    files = {"go.mod": "// comment\nmodule github.com/acme/svc-b\n\ngo 1.22\n"}
    f = fact(files, PACKAGE, "go:github.com/acme/svc-b")
    assert f.line == 2


def test_broken_manifests_are_skipped_not_fatal():
    files = {"pyproject.toml": "[project\nname=", "package.json": "{nope", "go.mod": ""}
    assert keys(files, PACKAGE) == set()


# --- images --------------------------------------------------------------------


def test_build_push_action_tags_are_built_images():
    wf = (
        "jobs:\n  b:\n    steps:\n      - uses: docker/build-push-action@v6\n        with:\n"
        "          push: true\n          tags: |\n"
        "            123456789012.dkr.ecr.us-east-1.amazonaws.com/team/svc-a:${{ github.sha }}\n"
        "            ghcr.io/acme/svc-a:latest\n"
    )
    assert keys({".github/workflows/build.yml": wf}, IMAGE) == {"team/svc-a", "acme/svc-a"}


def test_docker_build_and_push_commands_anywhere_scripted():
    files = {
        "Makefile": (
            "push:\n\tdocker build -t registry.acme.io/svc-a:1.2 .\n"
            "\tdocker push registry.acme.io/svc-a:1.2\n"
        ),
        "scripts/build.sh": "docker buildx build --tag=acme/worker:dev --push .\n",
    }
    assert keys(files, IMAGE) == {"svc-a", "acme/worker"}


def test_image_env_keys_in_a_workflow():
    wf = "env:\n  ECR_REPOSITORY: team/svc-a\n  IMAGE_NAME: acme/other\n"
    assert keys({".github/workflows/ci.yml": wf}, IMAGE) == {"team/svc-a", "acme/other"}


def test_terraform_ecr_repository_name():
    tf = 'resource "aws_ecr_repository" "this" {\n  name = "team/svc-a"\n}\n'
    f = fact({"infra/ecr.tf": tf}, IMAGE, "team/svc-a")
    assert f.line == 2


def test_templated_image_names_are_not_images():
    wf = (
        "      - uses: docker/build-push-action@v6\n        with:\n"
        "          tags: ${{ env.IMAGE }}:latest\n"
    )
    assert keys({".github/workflows/b.yml": wf}, IMAGE) == set()


# --- hosts -----------------------------------------------------------------------


def test_ingress_hosts_and_tls_hosts():
    ing = (
        "apiVersion: networking.k8s.io/v1\nkind: Ingress\nmetadata:\n  name: a\nspec:\n"
        "  tls:\n    - hosts:\n        - svc-a.acme.internal\n  rules:\n"
        "    - host: svc-a.acme.internal\n    - host: api.acme.io\n"
    )
    assert keys({"k8s/ingress.yaml": ing}, HOST) == {"svc-a.acme.internal", "api.acme.io"}


def test_helm_values_ingress_block_hosts():
    values = (
        "image:\n  repository: team/svc-a\ningress:\n  enabled: true\n  hosts:\n"
        "    - host: svc-a.acme.io\n      paths: [/]\nclient:\n  host: other.acme.io\n"
    )
    # other.acme.io is a host this chart CALLS, outside the ingress block: not provided.
    assert keys({"charts/svc-a/values.yaml": values}, HOST) == {"svc-a.acme.io"}


def test_a_plain_host_key_in_app_config_is_not_a_provided_host():
    assert keys({"config/app.yaml": "upstream:\n  host: svc-a.acme.io\n"}, HOST) == set()


def test_gateway_api_and_istio_hostnames():
    route = 'kind: HTTPRoute\nspec:\n  hostnames:\n    - "b.acme.io"\n'
    vs = "kind: VirtualService\nspec:\n  hosts:\n  - c.acme.io\n"
    assert keys({"r.yaml": route, "vs.yaml": vs}, HOST) == {"b.acme.io", "c.acme.io"}


def test_terraform_dns_cert_and_custom_domains():
    tf = (
        'resource "aws_route53_record" "a" {\n  name = "svc-a.acme.io"\n  type = "A"\n}\n'
        'resource "aws_route53_record" "b" {\n  name = "short"\n}\n'
        'resource "aws_acm_certificate" "c" {\n  domain_name = "api.acme.io"\n'
        '  subject_alternative_names = ["alt.acme.io", "*.edge.acme.io"]\n}\n'
        'resource "aws_apigatewayv2_domain_name" "d" {\n  domain_name = "gw.acme.io"\n}\n'
    )
    assert keys({"infra/dns.tf": tf}, HOST) == {
        "svc-a.acme.io", "api.acme.io", "alt.acme.io", "*.edge.acme.io", "gw.acme.io",
    }  # fmt: skip


def test_openapi_servers_nginx_server_name_and_cname_file():
    spec = "openapi: 3.0.0\nservers:\n  - url: https://svc-a.acme.io/v2\npaths: {}\n"
    files = {
        "openapi.yaml": spec,
        "deploy/nginx.conf": "server {\n  server_name web.acme.io www.acme.io;\n}\n",
        "CNAME": "docs.acme.io\n",
    }
    assert keys(files, HOST) == {"svc-a.acme.io", "web.acme.io", "www.acme.io", "docs.acme.io"}


def test_k8s_service_provides_its_cluster_dns_names():
    svc = (
        "apiVersion: v1\nkind: Service\nmetadata:\n  name: resolver\n  namespace: tokens\n"
        "spec: {}\n"
    )
    assert keys({"k8s/svc.yaml": svc}, HOST) == {
        "resolver.tokens.svc.cluster.local", "resolver.tokens.svc",
    }  # fmt: skip


def test_multi_document_yaml_reads_each_document_alone():
    docs = (
        "kind: Deployment\nmetadata:\n  name: x\n---\n"
        "kind: Service\nmetadata:\n  name: y\n  namespace: z\n---\n"
        "kind: ConfigMap\nmetadata:\n  name: q\n  namespace: z\n"
    )
    assert keys({"all.yaml": docs}, HOST) == {"y.z.svc.cluster.local", "y.z.svc"}


def test_reserved_and_templated_hosts_are_dropped():
    ing = (
        "kind: Ingress\nspec:\n  rules:\n    - host: localhost\n    - host: api.example.com\n"
        "    - host: {{ .Values.host }}\n    - host: 10.0.0.1\n"
    )
    assert keys({"i.yaml": ing}, HOST) == set()


# --- routes ------------------------------------------------------------------------


def test_python_decorators_fastapi_flask_starlette():
    src = (
        "from fastapi import APIRouter\n"
        'router = APIRouter(prefix="/internal")\n'
        '@router.post("/resolve")\nasync def r(): ...\n'
        '@app.get("/v2/keys/{key_id}")\ndef k(): ...\n'
        'bp = Blueprint("b", __name__, url_prefix="/admin")\n'
        '@bp.route("/users/<int:uid>", methods=["GET"])\ndef u(): ...\n'
    )
    assert keys({"svc/api.py": src}, ROUTE) == {
        "/internal/resolve", "/v2/keys/*", "/admin/users/*",
    }  # fmt: skip
    assert fact({"svc/api.py": src}, ROUTE, "/internal/resolve").line == 3


def test_python_route_tables():
    src = (
        'routes = [Route("/hooks/github", endpoint=h), Mount("/x", app=y)]\n'
        'app.add_url_rule("/legacy/ping-me", view_func=v)\n'
        'web.get("/aio/thing", handler)\n'
        'urlpatterns = [path("api/orders/<int:pk>/", views.o)]\n'
    )
    assert keys({"routes.py": src}, ROUTE) == {
        "/hooks/github", "/legacy/ping-me", "/aio/thing", "/api/orders/*",
    }  # fmt: skip


def test_js_server_routers_but_not_http_clients():
    src = (
        "app.get('/internal/resolve', handler);\n"
        "router.post(`/v1/keys/:id`, auth, h);\n"
        'fastify.put("/jobs/:jobId/cancel", h)\n'
        "apiRouter.delete('/sessions/:sid', h)\n"
        "axios.get('/not/a/route')\n"
        "api.post('/also/not/a/route', body)\n"
        "client.get('/nor/this/one')\n"
    )
    assert keys({"src/server.ts": src}, ROUTE) == {
        "/internal/resolve", "/v1/keys/*", "/jobs/*/cancel", "/sessions/*",
    }  # fmt: skip


def test_nextjs_app_router_and_pages_api_file_routes():
    files = {
        "app/api/keys/[id]/route.ts": "export async function GET() {}\n",
        "src/app/(admin)/reports/route.js": "export const POST = () => {}\n",
        "pages/api/hooks/stripe.ts": "export default function h() {}\n",
        "pages/api/users/index.ts": "export default function h() {}\n",
        "app/page.tsx": "export default function P() {}\n",
    }
    assert keys(files, ROUTE) == {"/api/keys/*", "/reports", "/api/hooks/stripe", "/api/users"}


def test_go_handlers_and_routers():
    src = (
        'mux.HandleFunc("/internal/resolve", h)\n'
        'mux.Handle("GET /v1/items/{id}", h)\n'
        'r.GET("/teams/:team/members", h)\n'
        'e.Post("/hooks/slack", h)\n'
    )
    assert keys({"cmd/server/main.go": src}, ROUTE) == {
        "/internal/resolve", "/v1/items/*", "/teams/*/members", "/hooks/slack",
    }  # fmt: skip


def test_openapi_paths_yaml_and_json():
    yml = (
        "openapi: 3.1.0\ninfo: {}\npaths:\n  /internal/resolve:\n    post: {}\n"
        "  /v1/keys/{id}:\n    get: {}\ncomponents: {}\n"
    )
    jsn = '{"openapi": "3.0.0", "paths": {"/jobs/{id}": {"get": {}}}}'
    files = {"api/openapi.yaml": yml, "api/spec.json": jsn}
    assert keys(files, ROUTE) == {"/internal/resolve", "/v1/keys/*", "/jobs/*"}
    assert fact(files, ROUTE, "/internal/resolve").line == 4


def test_a_yaml_paths_key_without_openapi_is_not_a_spec():
    assert keys({"conf.yaml": "paths:\n  /var/data:\n    mode: ro\n"}, ROUTE) == set()


def test_docs_never_provide_anything():
    files = {"README.md": '@app.get("/a/b")\nmodule github.com/x/y\n'}
    assert provides.extract(files) == []


# --- resources -------------------------------------------------------------------


def test_terraform_resource_names():
    tf = (
        'resource "aws_dynamodb_table" "usage" {\n  name         = "usage-ledger"\n'
        '  hash_key     = "pk"\n  attribute {\n    name = "pk"\n    type = "S"\n  }\n}\n'
        'resource "aws_s3_bucket" "b" {\n  bucket = "acme-artifacts-prod"\n}\n'
        'resource "aws_sqs_queue" "q" {\n  name = "${var.env}-jobs-queue"\n}\n'
        'resource "aws_lambda_function" "f" {\n  function_name = "key-rotator"\n}\n'
    )
    # the nested attribute block's name = "pk" is not the table's name
    assert keys({"infra/data.tf": tf}, RESOURCE) == {
        "usage-ledger", "acme-artifacts-prod", "*-jobs-queue", "key-rotator",
    }  # fmt: skip
    assert fact({"infra/data.tf": tf}, RESOURCE, "usage-ledger").line == 2


def test_cloudformation_and_serverless_names():
    cfn = (
        "Resources:\n  T:\n    Type: AWS::DynamoDB::Table\n    Properties:\n"
        "      TableName: usage-ledger\n  Q:\n    Properties:\n"
        '      QueueName: !Sub "${Stage}-events"\n'
        "      BucketName: !Ref Param\n"
    )
    assert keys({"template.yaml": cfn}, RESOURCE) == {"usage-ledger", "*-events"}


def test_cdk_names_only_in_cdk_code():
    cdk = (
        "import * as cdk from 'aws-cdk-lib';\n"
        "new Table(this, 'T', { tableName: 'usage-ledger' });\n"
    )
    client = "const tableName = 'usage-ledger';\n"
    assert keys({"infra/stack.ts": cdk}, RESOURCE) == {"usage-ledger"}
    assert keys({"src/db.ts": client}, RESOURCE) == set()


def test_a_glob_needs_four_literal_characters():
    tf = 'resource "aws_sqs_queue" "q" {\n  name = "${var.a}-${var.b}"\n}\n'
    assert keys({"q.tf": tf}, RESOURCE) == set()


# --- cost bounds ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '@app.get("/' + "a/" * 60000 + "\n",
        "server_name " + "a" * 200000 + "\n",
        'resource "aws_s3_bucket" "b" {\n' + "{" * 100000 + "\n",
        "tags: |\n" + "  x:" * 60000 + "\n",
        "kind: Ingress\n" + "    - host: " * 20000 + "\n",
    ],
)
def test_hostile_text_finishes_fast(text):
    start = time.monotonic()
    provides.extract({"a.py": text, "b.tf": text, "c.yaml": text, ".github/workflows/x.yml": text})
    assert time.monotonic() - start < 2.0
