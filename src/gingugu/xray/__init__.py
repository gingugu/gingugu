"""Codebase X-ray: a repository's import graph, ranked, and what a change reaches.

``extract`` runs where the repository is - the caller's machine, inside the
proxy or the local stdio server - and turns a git working tree into a payload
of files and import edges. The brain stores that payload as a snapshot and does
the math (``rank``, ``blast``). The brain never reads a path.
"""
