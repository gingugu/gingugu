"""X7d spec, Python: routes across files - the URL, not just the file's tail.

End to end through ``python.ADAPTER.extract``: the walk adds ``recv`` to every
entry and ``mounts`` records (``surface_records``); the linker resolves each
mount's child and each entry's receiver to a router value (``surface_mounts.Ref``)
and ``Collector.finish`` joins the prefix chain in front of the route.

Value resolution (``value(rel, scope, form, x, y)``), Python:

- ``"n"`` ``x``: a name bound in ``scope`` or a scope enclosing it (any assignment)
  is ``(rel, that scope, x)`` (module level: ``<module>``); else a from-import of
  a module-level name of a repo module (``from .users import router``) is
  ``(that module's path, "<module>", name)`` - a variable, not only a def; else
  None.
- ``"a"`` ``x.y``: ``x`` an imported repo module -> ``(its path, "<module>", y)``.
- ``"m"`` a dotted module (Django ``include("app.urls")``) -> ``(its path,
  "<module>", "*")``: every route registered there with no receiver.

Mount sites: ``P.include_router(child, prefix=...)`` (FastAPI; no ``prefix`` is
"" ), ``P.register_blueprint(child, url_prefix=...)`` (Flask), Django
``path("api/", include("mod"))`` (parent ""). A literal prefix is joined behind
any prefix the same file gives ``P``; a non-literal one is ``surface_route.PREFIX``.
"""

from __future__ import annotations

import json

from gingugu.xray import symbols_walk
from gingugu.xray.lang import python

A = python.ADAPTER


def _routes(files: dict[str, str]) -> dict[str, str]:
    sym = A.extract({k: v.encode() for k, v in files.items()}, {}).symbols
    return {e.handler.rsplit(".", 1)[-1]: e.route for e in sym.entries}


def test_the_walk_records_round_trip() -> None:
    import ast

    src = "from x import r\napp.include_router(r, prefix='/a')\n@r.get('/b')\ndef b(): pass\n"
    raw = symbols_walk.walk(ast.parse(src))
    assert raw["mounts"] == [["<module>", 2, "app", "/a", "n", "r", "", 0]]
    assert raw["entries"][0][10] == "r"
    assert symbols_walk.valid(json.loads(json.dumps(raw)))


FASTAPI = {
    "app/__init__.py": "",
    "app/main.py": (
        "from fastapi import FastAPI\n"
        "from app.api.main import api_router\n"
        "from app.core.config import settings\n"
        "app = FastAPI()\n"
        "app.include_router(api_router, prefix='/api/v1')\n"
        "@app.get('/health')\n"
        "def health(): pass\n"
    ),
    "app/api/__init__.py": "",
    "app/api/main.py": (
        "from fastapi import APIRouter\n"
        "from app.api.routes import items, users\n"
        "from app.api.routes.login import router as login_router\n"
        "api_router = APIRouter()\n"
        "api_router.include_router(users.router)\n"
        "api_router.include_router(items.router, prefix='/things')\n"
        "api_router.include_router(login_router)\n"
    ),
    "app/api/routes/__init__.py": "",
    "app/api/routes/users.py": (
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/users')\n"
        "@router.get('/me')\n"
        "def read_me(): pass\n"
    ),
    "app/api/routes/items.py": (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.post('/')\n"
        "def create_item(): pass\n"
    ),
    "app/api/routes/login.py": (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.post('/login/access-token')\n"
        "def login(): pass\n"
    ),
}


def test_fastapi_routers_chain_through_include_router() -> None:
    assert _routes(FASTAPI) == {
        "health": "/health",
        "read_me": "/api/v1/users/me",
        "create_item": "/api/v1/things/",
        "login": "/api/v1/login/access-token",
    }


def test_a_settings_prefix_is_a_placeholder() -> None:
    files = dict(FASTAPI)
    files["app/main.py"] = files["app/main.py"].replace(
        "prefix='/api/v1'", "prefix=settings.API_V1_STR"
    )
    assert _routes(files)["read_me"] == "{prefix}/users/me"


def test_an_include_on_a_prefixed_router_keeps_the_parent_prefix() -> None:
    files = {
        "a.py": (
            "from fastapi import APIRouter\nimport b\n"
            "outer = APIRouter(prefix='/outer')\n"
            "outer.include_router(b.inner, prefix='/mid')\n"
        ),
        "b.py": (
            "from fastapi import APIRouter\ninner = APIRouter()\n"
            "@inner.get('/leaf')\ndef leaf(): pass\n"
        ),
    }
    assert _routes(files) == {"leaf": "/outer/mid/leaf"}


def test_flask_blueprints() -> None:
    files = {
        "app/__init__.py": (
            "from flask import Flask\nfrom . import auth, blog\n"
            "app = Flask(__name__)\n"
            "app.register_blueprint(auth.bp)\n"
            "app.register_blueprint(blog.bp, url_prefix='/blog')\n"
        ),
        "app/auth.py": (
            "from flask import Blueprint\nbp = Blueprint('auth', __name__, url_prefix='/auth')\n"
            "@bp.route('/login', methods=['POST'])\ndef login(): pass\n"
        ),
        "app/blog.py": (
            "from flask import Blueprint\nbp = Blueprint('blog', __name__)\n"
            "@bp.route('/')\ndef index(): pass\n"
        ),
    }
    assert _routes(files) == {"login": "/auth/login", "index": "/blog/"}


def test_django_include() -> None:
    files = {
        "site/__init__.py": "",
        "site/urls.py": (
            "from django.urls import include, path\n"
            "urlpatterns = [path('api/', include('shop.urls'))]\n"
        ),
        "shop/__init__.py": "",
        "shop/urls.py": (
            "from django.urls import path\nfrom shop import views\n"
            "urlpatterns = [path('items/<int:pk>/', views.item)]\n"
        ),
        "shop/views.py": "def item(request, pk): pass\n",
    }
    assert _routes(files) == {"item": "api/items/<int:pk>/"}


def test_a_router_nobody_mounts_keeps_its_own_route() -> None:
    files = {
        "a.py": "from fastapi import APIRouter\nr = APIRouter()\n@r.get('/x')\ndef x(): pass\n"
    }
    assert _routes(files) == {"x": "/x"}


def test_a_mount_of_an_unresolvable_child_changes_nothing() -> None:
    files = {
        "a.py": (
            "from fastapi import APIRouter\nfrom somewhere_else import r\n"
            "app.include_router(r, prefix='/z')\n"
            "own = APIRouter()\n@own.get('/x')\ndef x(): pass\n"
        )
    }
    assert _routes(files) == {"x": "/x"}


def test_a_function_local_router_is_its_own_scope() -> None:
    files = {
        "a.py": (
            "from fastapi import FastAPI, APIRouter\n"
            "def build():\n"
            "    app = FastAPI()\n"
            "    r = APIRouter()\n"
            "    @r.get('/x')\n"
            "    def x(): pass\n"
            "    app.include_router(r, prefix='/in')\n"
            "    return app\n"
        )
    }
    assert _routes(files) == {"x": "/in/x"}
