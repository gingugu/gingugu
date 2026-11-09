# X-ray standard

## The law: every language X-ray parses

An X-ray surface covers every language X-ray parses - today Python, TypeScript/
JavaScript and Go - as far as static reading can take each one. Set by the owner
on 2026-11-09, after a review turned up the symbol graph, the read note's "Most
called" line, the after-edit callers note, nearest-test ranking and the X11
ledger all covering Python (or pytest) alone.

What it means in practice:

- **Design across languages from the start.** A new surface is specified, tested
  and measured for each language before it ships, not "Python first, the rest
  later".
- **No extension gates.** A `rel.endswith(".py")` check in a hook or tool is a
  law violation unless the thing it guards truly exists in one language only.
  Test-file rules go through `lang.is_test`; test-name ranking through
  `hook_common._is_test_name`.
- **Gaps are counted, never hidden.** Where a language defeats static reading
  (TS interface-typed parameters, Go interface values, callbacks), the call is
  counted unresolved and the surface says the number is a lower bound. A
  language a surface cannot serve at all is named in its output and its docs.
- **Measure on real repos per language** before review, not just the fixtures.
  The Go symbol linker passed every spec and still crashed on gin's first run.
- **A new language adapter** joins every surface: the import graph, the symbol
  graph, the hook lines, the test rules and the ledger's runner parsing.

## Resolution baselines (X17, measured 2026-11-09)

Share of non-external calls the symbol graph resolves:

| Language | Repo | Resolved |
|---|---|---|
| Python | gingugu | 64.4% |
| TS/JS | vite | 52.9% |
| TS/JS | express example app | 78.4% |
| TS/JS | gingugu `ui/` | 62.5% |
| Go | gin | 75.4% |
| Go | bubbletea | 76.6% |

A change to a linker that moves any of these down needs a reason.
