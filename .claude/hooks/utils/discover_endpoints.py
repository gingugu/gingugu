#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""
Discover API endpoints from common web framework patterns.
Compares discovered features against existing dataflow documentation.

Usage:
    uv run discover_endpoints.py [--cwd /path/to/project]

Output: JSON report to stdout with discovered, documented, and missing features.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


# Framework detection patterns (file -> framework)
FRAMEWORK_INDICATORS = {
    "package.json": ["express", "fastify", "koa", "hapi", "nestjs", "next", "nuxt", "vue", "@angular/core"],
    "angular.json": ["angular"],
    "pyproject.toml": ["fastapi", "flask", "django", "starlette", "sanic"],
    "requirements.txt": ["fastapi", "flask", "django", "starlette", "sanic"],
    "composer.json": ["laravel", "symfony", "slim", "lumen"],
    "Gemfile": ["rails", "sinatra"],
    "go.mod": ["gin", "chi", "echo", "fiber", "mux"],
    "Cargo.toml": ["actix", "axum", "rocket", "warp"],
    "pom.xml": ["spring-boot", "spring-web", "micronaut", "quarkus", "jakarta"],
    "build.gradle": ["spring-boot", "spring-web", "micronaut", "quarkus"],
    "build.gradle.kts": ["spring-boot", "spring-web", "micronaut", "quarkus"],
}

# Route file patterns to scan per framework family
ROUTE_FILE_PATTERNS = [
    # JavaScript/TypeScript
    "**/routes/**/*.{js,ts,jsx,tsx}",
    "**/controllers/**/*.{js,ts,jsx,tsx}",
    "**/api/**/*.{js,ts,jsx,tsx}",
    "**/app/**/route.{js,ts}",
    "**/pages/api/**/*.{js,ts}",
    "**/server.{js,ts}",
    "**/app.{js,ts}",
    "**/index.{js,ts}",
    # Python
    "**/routes/**/*.py",
    "**/views/**/*.py",
    "**/api/**/*.py",
    "**/endpoints/**/*.py",
    "**/routers/**/*.py",
    "**/urls.py",
    "**/app.py",
    "**/main.py",
    # Ruby
    "**/config/routes.rb",
    "**/controllers/**/*.rb",
    # Go
    "**/handlers/**/*.go",
    "**/routes/**/*.go",
    "**/api/**/*.go",
    "**/main.go",
    "**/router.go",
    # C#
    "**/Controllers/**/*.cs",
    "**/Endpoints/**/*.cs",
    # PHP (Laravel/Symfony)
    "**/routes/**/*.php",
    "**/web.php",
    "**/api.php",
    "**/controllers/**/*.php",
    "**/Controller/**/*.php",
    "**/src/Controller/**/*.php",
    # Java/Kotlin (Spring Boot, Micronaut, Quarkus)
    "**/controllers/**/*.java",
    "**/controller/**/*.java",
    "**/Controller/**/*.java",
    "**/resource/**/*.java",
    "**/resources/**/*.java",
    "**/api/**/*.java",
    "**/rest/**/*.java",
    "**/controllers/**/*.kt",
    "**/controller/**/*.kt",
    "**/resource/**/*.kt",
    # Genero (Four Js)
    "**/*.4gl",
    "**/*.per",
    # Angular
    "**/app-routing.module.ts",
    "**/app.routes.ts",
    "**/*-routing.module.ts",
    "**/*.routes.ts",
    # Vue Router
    "**/router/**/*.{js,ts}",
    "**/router.{js,ts}",
    # OpenAPI specs
    "**/openapi.{yaml,yml,json}",
    "**/swagger.{yaml,yml,json}",
]

# Regex patterns for endpoint definitions by framework
ENDPOINT_PATTERNS = {
    # Express/Fastify/Koa (JS/TS)
    "express": [
        r'(?:app|router|server)\.(get|post|put|patch|delete|options|head)\s*\(\s*[\'"`]([^\'"`]+)[\'"`]',
        r'(?:app|router|server)\.route\s*\(\s*[\'"`]([^\'"`]+)[\'"`]\)',
    ],
    # FastAPI (Python)
    "fastapi": [
        r'@(?:app|router)\.(get|post|put|patch|delete|options|head)\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'@(?:app|router)\.api_route\s*\(\s*[\'"]([^\'"]+)[\'"]',
    ],
    # Flask (Python)
    "flask": [
        r'@(?:app|blueprint|bp)\.(route|get|post|put|patch|delete)\s*\(\s*[\'"]([^\'"]+)[\'"]',
    ],
    # Django (Python)
    "django": [
        r'path\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r're_path\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'url\s*\(\s*r?[\'"]([^\'"]+)[\'"]',
    ],
    # Rails (Ruby)
    "rails": [
        r'(?:get|post|put|patch|delete)\s+[\'"]([^\'"]+)[\'"]',
        r'resources?\s+:(\w+)',
    ],
    # Go (gin/chi/echo)
    "go": [
        r'(?:r|router|e|g|group)\.(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s*\(\s*[\'"`]([^\'"`]+)[\'"`]',
        r'http\.HandleFunc\s*\(\s*[\'"`]([^\'"`]+)[\'"`]',
        r'(?:r|router)\.(?:Handle|Method)\s*\(\s*[\'"`](\w+)[\'"`]\s*,\s*[\'"`]([^\'"`]+)[\'"`]',
    ],
    # ASP.NET (C#)
    "aspnet": [
        r'\[Http(Get|Post|Put|Patch|Delete)\s*(?:\(\s*[\'"]([^\'"]*)[\'"])?\s*\]',
        r'\[Route\s*\(\s*[\'"]([^\'"]+)[\'"]',
    ],
    # Laravel (PHP)
    "laravel": [
        r'Route::(get|post|put|patch|delete|options|any)\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'Route::resource\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'Route::apiResource\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'Route::match\s*\(\s*\[.*?\]\s*,\s*[\'"]([^\'"]+)[\'"]',
    ],
    # Symfony (PHP)
    "symfony": [
        r'#\[Route\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'@Route\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'#\[Route\s*\(\s*[\'"]([^\'"]+)[\'"].*?methods:\s*\[\s*[\'"](\w+)[\'"]',
    ],
    # Java Spring Boot
    "spring": [
        r'@(Get|Post|Put|Patch|Delete)Mapping\s*(?:\(\s*(?:value\s*=\s*)?[\'"]([^\'"]*)[\'"])?',
        r'@RequestMapping\s*\(\s*(?:value\s*=\s*)?[\'"]([^\'"]+)[\'"]',
        r'@RequestMapping\s*\(\s*(?:value\s*=\s*)?[\'"]([^\'"]+)[\'"].*?method\s*=\s*RequestMethod\.(\w+)',
    ],
    # Micronaut (Java/Kotlin)
    "micronaut": [
        r'@(Get|Post|Put|Patch|Delete)\s*\(\s*(?:[\'"]([^\'"]*)[\'"])?\s*\)',
        r'@Controller\s*\(\s*[\'"]([^\'"]+)[\'"]',
    ],
    # Quarkus (Java/Kotlin)
    "quarkus": [
        r'@(GET|POST|PUT|PATCH|DELETE)\b',
        r'@Path\s*\(\s*[\'"]([^\'"]+)[\'"]',
    ],
    # Genero (Four Js 4GL)
    "genero": [
        r'CALL\s+com\.WebServiceEngine\.\w+\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'CALL\s+\w+\.(?:GET|POST|PUT|DELETE)\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'DEFINE\s+\w+\s+com\.HttpServiceRequest',
        r'WS\s+(GET|POST|PUT|DELETE)\s+([^\s]+)',
    ],
    # Vue Router
    "vue": [
        r'(?:path|route)\s*:\s*[\'"]([^\'"]+)[\'"]',
    ],
    # Angular Router
    "angular": [
        r'(?:path|redirectTo)\s*:\s*[\'"]([^\'"]+)[\'"]',
    ],
}

# UI page patterns
UI_PAGE_PATTERNS = [
    "**/pages/**/*.{js,ts,jsx,tsx,vue,svelte}",
    "**/views/**/*.{js,ts,jsx,tsx,vue,svelte}",
    "**/screens/**/*.{js,ts,jsx,tsx}",
    "**/app/**/page.{js,ts,jsx,tsx}",
    "**/src/routes/**/*.{svelte,tsx,jsx}",
    # Angular
    "**/app/**/*.component.ts",
    # Vue
    "**/src/views/**/*.vue",
    "**/src/pages/**/*.vue",
    # PHP (Blade/Twig)
    "**/resources/views/**/*.{blade.php,twig}",
    # Genero (Forms)
    "**/*.per",
]


def detect_frameworks(project_dir: str) -> list[str]:
    """Detect web frameworks used in the project."""
    detected = []
    for indicator_file, frameworks in FRAMEWORK_INDICATORS.items():
        filepath = Path(project_dir) / indicator_file
        if filepath.exists():
            try:
                content = filepath.read_text().lower()
                for fw in frameworks:
                    if fw in content:
                        detected.append(fw)
            except Exception:
                pass
    return list(set(detected))


def find_route_files(project_dir: str) -> list[str]:
    """Find files likely to contain route/endpoint definitions."""
    found = []
    project = Path(project_dir)

    for pattern in ROUTE_FILE_PATTERNS:
        # Convert glob pattern with {a,b} to multiple globs
        if "{" in pattern:
            base = pattern[:pattern.index("{")]
            exts_str = pattern[pattern.index("{") + 1:pattern.index("}")]
            suffix = pattern[pattern.index("}") + 1:]
            for ext in exts_str.split(","):
                for match in project.glob(base + ext + suffix):
                    if _should_include(match, project):
                        found.append(str(match))
        else:
            for match in project.glob(pattern):
                if _should_include(match, project):
                    found.append(str(match))

    return list(set(found))


def find_ui_pages(project_dir: str) -> list[str]:
    """Find files likely to contain UI page/view definitions."""
    found = []
    project = Path(project_dir)

    for pattern in UI_PAGE_PATTERNS:
        if "{" in pattern:
            base = pattern[:pattern.index("{")]
            exts_str = pattern[pattern.index("{") + 1:pattern.index("}")]
            suffix = pattern[pattern.index("}") + 1:]
            for ext in exts_str.split(","):
                for match in project.glob(base + ext + suffix):
                    if _should_include(match, project):
                        found.append(str(match))
        else:
            for match in project.glob(pattern):
                if _should_include(match, project):
                    found.append(str(match))

    return list(set(found))


def _should_include(path: Path, project: Path) -> bool:
    """Filter out node_modules, vendor, test files, etc."""
    rel = str(path.relative_to(project))
    skip_dirs = ["node_modules", ".git", "vendor", "dist", "build", "__pycache__", ".next", ".nuxt", "target", ".gradle", "storage", "cache"]
    return not any(d in rel.split(os.sep) for d in skip_dirs)


def extract_endpoints(file_path: str, frameworks: list[str]) -> list[dict]:
    """Extract API endpoint definitions from a file."""
    endpoints = []
    try:
        content = Path(file_path).read_text()
    except Exception:
        return endpoints

    # Determine which patterns to try based on detected frameworks
    pattern_sets = set()
    framework_map = {
        "express": "express", "fastify": "express", "koa": "express",
        "hapi": "express", "nestjs": "express", "next": "express",
        "fastapi": "fastapi", "starlette": "fastapi",
        "flask": "flask", "sanic": "flask",
        "django": "django",
        "rails": "rails", "sinatra": "rails",
        "gin": "go", "chi": "go", "echo": "go", "fiber": "go", "mux": "go",
        "actix": "go", "axum": "go", "rocket": "go", "warp": "go",
        "laravel": "laravel", "lumen": "laravel", "slim": "laravel",
        "symfony": "symfony",
        "spring-boot": "spring", "spring-web": "spring", "jakarta": "spring",
        "micronaut": "micronaut",
        "quarkus": "quarkus",
        "vue": "vue", "nuxt": "vue",
        "angular": "angular", "@angular/core": "angular",
    }

    for fw in frameworks:
        mapped = framework_map.get(fw)
        if mapped:
            pattern_sets.add(mapped)

    # If no framework detected, try all patterns
    if not pattern_sets:
        pattern_sets = set(ENDPOINT_PATTERNS.keys())

    # Also detect from file extension
    if file_path.endswith((".cs",)):
        pattern_sets.add("aspnet")
    elif file_path.endswith((".php",)):
        pattern_sets.add("laravel")
        pattern_sets.add("symfony")
    elif file_path.endswith((".java", ".kt")):
        pattern_sets.add("spring")
        pattern_sets.add("micronaut")
        pattern_sets.add("quarkus")
    elif file_path.endswith((".4gl",)):
        pattern_sets.add("genero")
    elif file_path.endswith((".vue",)):
        pattern_sets.add("vue")
    elif file_path.endswith((".component.ts",)) or "angular" in file_path.lower():
        pattern_sets.add("angular")

    for pattern_key in pattern_sets:
        for pattern in ENDPOINT_PATTERNS.get(pattern_key, []):
            for match in re.finditer(pattern, content, re.IGNORECASE):
                groups = match.groups()
                if len(groups) == 2:
                    method, path = groups[0].upper(), groups[1]
                elif len(groups) == 1:
                    method, path = "ANY", groups[0]
                else:
                    continue
                endpoints.append({
                    "method": method,
                    "path": path,
                    "file": file_path,
                })

    return endpoints


def group_by_feature(endpoints: list[dict], project_dir: str) -> dict[str, list[dict]]:
    """Group endpoints into features based on route prefix or file name."""
    features = {}
    for ep in endpoints:
        # Try to infer feature from route path
        path = ep["path"].strip("/")
        parts = path.split("/")

        # Use first meaningful path segment as feature name
        feature = "general"
        for part in parts:
            # Skip common prefixes
            if part in ("api", "v1", "v2", "v3", "v4", ""):
                continue
            # Skip path parameters
            if part.startswith(":") or part.startswith("{") or part.startswith("<"):
                continue
            feature = part.lower().replace("_", "-")
            break

        # Fallback: use controller/route file name
        if feature == "general":
            file_stem = Path(ep["file"]).stem.lower()
            for suffix in ("_controller", "_handler", "_router", "_routes", "_views", "_api", "controller", "handler"):
                file_stem = file_stem.replace(suffix, "")
            if file_stem and file_stem not in ("index", "app", "main", "server", "routes", "urls"):
                feature = file_stem.replace("_", "-")

        if feature not in features:
            features[feature] = []
        features[feature].append(ep)

    return features


def get_documented_features(project_dir: str) -> list[str]:
    """Get list of features that already have dataflow documentation."""
    dataflows_dir = Path(project_dir) / ".ai" / "specs" / "dataflows"
    if not dataflows_dir.exists():
        return []
    return [f.stem for f in dataflows_dir.glob("*.md")]


def get_ui_features(ui_files: list[str], project_dir: str) -> list[str]:
    """Extract feature names from UI page files."""
    features = []
    for f in ui_files:
        rel = Path(f).relative_to(project_dir)
        parts = rel.parts
        # Skip common directories
        skip = {"pages", "views", "screens", "app", "src", "routes", "components"}
        meaningful = [p for p in parts if p.lower() not in skip and not p.startswith(".") and not p.startswith("[")]
        if meaningful:
            # Use directory name or file stem
            name = Path(meaningful[-1]).stem.lower()
            if name not in ("index", "page", "layout", "loading", "error", "not-found", "_app", "_document"):
                features.append(name.replace("_", "-"))
    return list(set(features))


def main():
    parser = argparse.ArgumentParser(description="Discover API endpoints and report dataflow gaps")
    parser.add_argument("--cwd", default=os.getcwd(), help="Project directory to scan")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.cwd)

    # Step 1: Detect frameworks
    frameworks = detect_frameworks(project_dir)

    # Step 2: Find and scan route files
    route_files = find_route_files(project_dir)
    all_endpoints = []
    for rf in route_files:
        all_endpoints.extend(extract_endpoints(rf, frameworks))

    # Step 3: Group by feature
    features = group_by_feature(all_endpoints, project_dir)

    # Step 4: Find UI pages
    ui_files = find_ui_pages(project_dir)
    ui_features = get_ui_features(ui_files, project_dir)

    # Merge UI features into feature map
    for uf in ui_features:
        if uf not in features:
            features[uf] = []

    # Step 5: Check documented features
    documented = get_documented_features(project_dir)

    # Step 6: Build report
    discovered_names = sorted(features.keys())
    missing = sorted(set(discovered_names) - set(documented))

    report = {
        "frameworks": frameworks,
        "route_files_scanned": len(route_files),
        "ui_files_scanned": len(ui_files),
        "total_endpoints": len(all_endpoints),
        "discovered_features": {
            name: {
                "endpoints": [
                    {"method": ep["method"], "path": ep["path"]}
                    for ep in features[name]
                ],
                "endpoint_count": len(features[name]),
            }
            for name in discovered_names
        },
        "documented": sorted(documented),
        "missing": missing,
    }

    print(json.dumps(report, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
