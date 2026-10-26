"""Vocabulary for the Explorer's demo brain. Every word here is invented.

The demo ships publicly (the wheel and GitHub Pages), so nothing in it may
come from a real brain: namespaces are made-up hobby projects, the shared layer
uses gingugu's own documented convention (``crow``), and the persona is a
made-up agent. Links point at the reserved documentation domains.
"""

from __future__ import annotations

CORE = "crow"  # gingugu's documented shared-layer namespace (prompt_hook.GLOBAL_NAMESPACE)
PERSONA = "nova"  # an invented agent persona

# name -> (memory count, topic vocabulary)
NAMESPACES: dict[str, tuple[int, list[str]]] = {
    CORE: (
        380,
        [
            "code-review",
            "testing",
            "commits",
            "naming",
            "logging",
            "retries",
            "timeouts",
            "migrations",
            "caching",
            "docs",
            "secrets",
            "backups",
            "ci",
            "linting",
            "deps",
            "rollbacks",
            "feature-flags",
            "pairing",
        ],
    ),
    PERSONA: (
        200,
        [
            "reflection",
            "tone",
            "estimates",
            "focus",
            "handoffs",
            "questions",
            "mistakes",
            "habits",
            "checklists",
            "summaries",
        ],
    ),
    "orbit-tracker": (
        420,
        [
            "tle-parsing",
            "ground-station",
            "pass-prediction",
            "doppler",
            "antenna",
            "telemetry",
            "orbit-math",
            "sky-map",
            "alerts",
            "time-sync",
            "elevation-mask",
            "decoder",
        ],
    ),
    "recipe-bot": (
        260,
        [
            "ingredients",
            "unit-conversion",
            "scaling",
            "nutrition",
            "substitutions",
            "meal-plan",
            "shopping-list",
            "fractions",
            "embeds",
            "slash-commands",
        ],
    ),
    "weather-station": (
        300,
        [
            "anemometer",
            "rain-gauge",
            "barometer",
            "humidity",
            "solar-panel",
            "sensor-drift",
            "calibration",
            "dashboard",
            "forecast",
            "battery",
            "firmware",
        ],
    ),
    "chess-engine": (
        240,
        [
            "move-gen",
            "eval",
            "search",
            "transposition",
            "endgame",
            "openings",
            "time-control",
            "uci",
            "perft",
            "pruning",
        ],
    ),
    "garden-planner": (
        140,
        [
            "seedlings",
            "frost-dates",
            "companions",
            "watering",
            "soil",
            "rotation",
            "pests",
            "layout",
        ],
    ),
    "home-lab": (
        220,
        [
            "nas",
            "mesh-router",
            "dns",
            "vpn",
            "certs",
            "monitoring",
            "containers",
            "power",
            "snapshots",
            "firewall",
        ],
    ),
    "budget-app": (
        160,
        [
            "ledger",
            "categories",
            "import",
            "rounding",
            "currencies",
            "recurring",
            "reports",
            "export",
        ],
    ),
    "music-tagger": (
        120,
        ["fingerprints", "metadata", "artwork", "playlists", "duplicates", "encodings", "lyrics"],
    ),
}

HUB_TAGS = ["lesson", "gotcha", "rule", "session-note", "howto"]

THINGS = [
    "parser",
    "scheduler",
    "cache",
    "queue",
    "importer",
    "renderer",
    "exporter",
    "validator",
    "watcher",
    "planner",
    "index",
    "sync job",
    "config loader",
    "retry loop",
    "health check",
    "migration",
    "fixture",
    "webhook",
]
ALTERNATIVES = [
    ("SQLite", "Postgres"),
    ("polling", "webhooks"),
    ("JSON", "CSV"),
    ("cron", "a daemon"),
    ("floats", "integer cents"),
    ("UTC", "local time"),
    ("a queue", "direct calls"),
    ("YAML", "TOML"),
    ("REST", "a CLI"),
    ("one big file", "per-day files"),
]
FAILURES = [
    "drifts",
    "times out",
    "double-counts",
    "drops the last row",
    "rounds the wrong way",
    "chokes on leading whitespace",
    "leaks file handles",
    "races on startup",
    "ignores the timezone",
    "loops forever",
]
CONDITIONS = [
    "the clock jumps",
    "the input is empty",
    "two runs overlap",
    "the network flaps",
    "a value is negative",
    "the cache is cold",
    "the file is huge",
    "unicode shows up",
    "the battery is low",
]
VERBS = [
    "rotate",
    "rebuild",
    "calibrate",
    "back up",
    "migrate",
    "profile",
    "release",
    "debug",
    "re-index",
    "benchmark",
]
REASONS = [
    "it keeps the hot path simple",
    "it fails loudly instead of silently",
    "the numbers are reproducible",
    "it costs nothing at this scale",
    "rollback is one command",
    "the tests can exercise it directly",
    "it matches how the data actually arrives",
    "nobody has to remember a step",
]
TOOLS = ["make", "just", "a shell script", "a notebook", "the CLI"]
QUERIES = [
    "how do we {verb} the {topic} {thing}",
    "why does the {thing} {failure}",
    "{topic} {thing} decision",
    "what broke in {topic}",
    "{topic} rules",
]
ABOUTS = [
    "keeping the {topic} {thing} honest",
    "{topic} without surprises",
    "how we {verb} {topic}",
    "the {topic} {thing}",
]
