"""The top-level ``gingugu --help`` text, kept apart so ``server`` stays small."""

USAGE = """\
gingugu — persistent long-term memory for AI coding assistants (MCP server)

Usage:
  gingugu                      Run the MCP server over stdio (default transport
                               for local clients like Claude Code / Cursor).
                               With `gingugu remote on` or MEMORY_REMOTE_URL set,
                               it relays to that remote brain instead.
  gingugu serve                Run over streamable HTTP for a remote/central brain.
  gingugu token add|list|revoke  Manage scoped tokens for `gingugu serve`.
  gingugu remote status|on|off|login  Choose which brain this machine talks to.
  gingugu credential list|delete  This machine's credential vault by hand; delete
                               asks you to type the name back, at a terminal.
                               For a client that cannot show the confirmation.
  gingugu promote [options]    Promote local gold memories up to a central brain.
  gingugu init [options]       Bootstrap a repo so an AI assistant uses Gingugu.
  gingugu harness [options]    Install the Claude Code harness on top of init: safety
                               guard, event logs, fenced minions, the creating-pr
                               skill, the .ai/ knowledge base. --migrate moves a repo off
                               an older per-event hook kit; --prune then deletes the
                               stubs and retired files it left.
  gingugu ui [options]         Launch the Memory Explorer web UI in a browser.
  gingugu dream [namespace]    Run the deterministic consolidation pass over the
                               memory graph and stage what it finds for review.
                               Writes only to the proposal queue, never to
                               memories - safe to put on a cron.
  gingugu board watch [opts]   Print a line for every new post in this agent's
                               board inbox (pushed from a remote brain, polled
                               locally). --once exits on the first; --exec runs
                               a command per post. For Monitor / codex queue.
  gingugu xray brief [options]  Handoff brief for a change: the changed files by rank
                               and the code and tests they reach. --range main..HEAD,
                               --files a,b, or the working tree; --json. Local only.
  gingugu embed               Finish embedding the whole store in one run (the
                               server does one small batch per startup).
  gingugu hook prompt          Involuntary recall: reads a UserPromptSubmit
                               event on stdin and surfaces memories the prompt
                               woke. Wired by `gingugu init`; not run by hand.
  gingugu hook tool            Tripwires + minion fence: reads a PreToolUse
                               event on stdin; stops a call that matches a
                               memory's tripwire, or a subagent reaching past
                               its fence. Wired by `gingugu init`.
  gingugu hook subagent        Warm minions: reads a SubagentStart event and
                               injects the memories the subagent's grant can
                               read. Wired by `gingugu init`; not run by hand.
  gingugu hook read            X-ray map on arrival: reads a PostToolUse event
                               (Read/Grep/Glob); the first read in a repo gets a
                               map, each source file its import neighbourhood.
                               Wired by `gingugu init`; not run by hand.

Options:
  -h, --help                   Show this help and exit.
  -V, --version                Show the version and exit.

Run a subcommand with --help for its own options, e.g. `gingugu init --help`.
The active namespace is set via the MEMORY_NAMESPACE environment variable.
"""
