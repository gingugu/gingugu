#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "python-dotenv",
# ]
# ///

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional


def log_session_start(input_data):
    """Log session start event to logs directory."""
    # Ensure logs directory exists
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / 'session_start.json'

    # Read existing log data or initialize empty list
    if log_file.exists():
        with open(log_file) as f:
            try:
                log_data = json.load(f)
            except (json.JSONDecodeError, ValueError):
                log_data = []
    else:
        log_data = []

    # Append the entire input data
    log_data.append(input_data)

    # Write back to file with formatting
    with open(log_file, 'w') as f:
        json.dump(log_data, f, indent=2)


def get_git_status():
    """Get current git status information. Returns None values for non-git repos."""
    try:
        # Get current branch
        branch_result = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if branch_result.returncode == 0:
            current_branch = branch_result.stdout.strip()
        else:
            current_branch = None

        if current_branch is None:
            return None, None

        # Get uncommitted changes count
        status_result = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if status_result.returncode == 0:
            output = status_result.stdout.strip()
            changes = output.split('\n') if output else []
            uncommitted_count = len(changes)
        else:
            uncommitted_count = 0

        return current_branch, uncommitted_count
    except Exception:
        return None, None


def check_dependencies():
    """Check if common project dependencies are available."""
    deps_status = {}
    for tool, cmd in [
        ('node', ['node', '--version']),
        ('python', ['python3', '--version']),
        ('uv', ['uv', '--version']),
        ('git', ['git', '--version']),
    ]:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                deps_status[tool] = result.stdout.strip()
            else:
                deps_status[tool] = None
        except Exception:
            deps_status[tool] = None
    return deps_status


def detect_project_type(cwd):
    """Detect project type from common project files."""
    project_files = [
        ('package.json', 'Node.js project'),
        ('pyproject.toml', 'Python project (pyproject.toml)'),
        ('requirements.txt', 'Python project (requirements.txt)'),
        ('Cargo.toml', 'Rust project'),
        ('go.mod', 'Go project'),
        ('Makefile', 'Makefile present'),
    ]
    detected = []
    for filename, description in project_files:
        if Path(cwd, filename).exists():
            detected.append(description)
    return detected


def persist_env_variable(name, value):
    """Persist an environment variable via CLAUDE_ENV_FILE."""
    env_file = os.environ.get('CLAUDE_ENV_FILE')
    if env_file:
        with open(env_file, 'a') as f:
            f.write(f'export {name}="{value}"\n')
        return True
    return False


def get_logs_size(cwd):
    """Calculate the total size of the logs directory in MB."""
    logs_dir = Path(cwd, 'logs')
    if not logs_dir.exists():
        return None
    try:
        total_size = sum(
            f.stat().st_size for f in logs_dir.rglob('*') if f.is_file()
        )
        return total_size / (1024 * 1024)
    except Exception:
        return None


def build_startup_contract(cwd):
    """Imperative Gingugu startup contract, injected fresh every session.

    Lives here (not only in AGENTS.md) on purpose: AGENTS.md is NOT auto-loaded
    into Claude's context, so the memory protocol was never guaranteed present.
    This block is — it rides in via SessionStart additionalContext every time.
    """
    project = Path(cwd).name
    return (
        "=== SESSION STARTUP CONTRACT (Gingugu memory protocol - do this FIRST) ===\n"
        "Before responding to the first user message, run these in parallel as your\n"
        "opening action. Non-negotiable:\n"
        '  - mcp__gingugu__memory_context(namespace="crow")   # identity, always first\n'
        '  - mcp__gingugu__memory_stats(namespace="crow")\n'
        f'  - mcp__gingugu__memory_context(namespace="{project}")\n'
        f'  - mcp__gingugu__memory_stats(namespace="{project}")\n'
        "Before asking for ANY secret/token/credential: mcp__gingugu__credential_list()\n"
        "Rules: do not skip; do not ask which repo (the workspace is the answer);\n"
        'if a namespace is missing, create it with memory_namespaces(action="create", name=...).\n'
        "Check memory_recall before asking anything answered in a prior session.\n"
        "=== END CONTRACT ==="
    )


def load_development_context(source, cwd):
    """Load relevant development context based on session source."""
    engineer_name = os.environ.get('ENGINEER_NAME', 'Engineer')
    context_parts = []

    # Startup contract first, on any fresh context window (not mid-session compact)
    if source in ('startup', 'clear', 'resume'):
        context_parts.append(build_startup_contract(cwd))
        context_parts.append("")

    # Add timestamp and greeting
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    context_parts.append(f"Session started at: {now}")
    context_parts.append(f"Session source: {source}")
    context_parts.append(f"Engineer: {engineer_name}")

    # Add git information if available
    branch, changes = get_git_status()
    if branch:
        context_parts.append(f"Git branch: {branch}")
        if changes and changes > 0:
            context_parts.append(f"Uncommitted changes: {changes} files")

    # Project type detection
    detected = detect_project_type(cwd)
    if detected:
        context_parts.append("\n--- Project Information ---")
        for d in detected:
            context_parts.append(f"Detected: {d}")

    # Available tools
    deps = check_dependencies()
    available = [f"{k}: {v}" for k, v in deps.items() if v]
    if available:
        context_parts.append("\n--- Available Tools ---")
        context_parts.extend(available)

    # Initialization actions on first startup
    if source == 'startup':
        persist_env_variable('PROJECT_ROOT', cwd)

    # Log directory size (useful on resume/maintenance)
    if source == 'resume':
        size_mb = get_logs_size(cwd)
        if size_mb is not None:
            context_parts.append(f"\nLogs directory size: {size_mb:.2f}MB")

    # Load project memory if it exists
    memory_file = Path(cwd, ".ai", "memory.md")
    if memory_file.exists():
        try:
            with open(memory_file) as f:
                content = f.read().strip()
                if content:
                    context_parts.append("\n--- Project Memory (.ai/memory.md) ---")
                    context_parts.append(content[:2000])
        except Exception:
            pass

    return "\n".join(context_parts)


def main():
    try:
        # Parse command line arguments
        parser = argparse.ArgumentParser()
        parser.add_argument('--no-context', action='store_true',
                          help='Skip loading project memory context')
        args = parser.parse_args()

        # Read JSON input from stdin
        input_data = json.loads(sys.stdin.read())

        # Extract fields
        source = input_data.get('source', 'unknown')  # "startup", "resume", or "clear"
        cwd = input_data.get('cwd', os.getcwd())

        # Enrich with git status
        branch, changes = get_git_status()
        if branch:
            input_data['git_branch'] = branch
            input_data['uncommitted_changes'] = changes

        input_data['logged_at'] = datetime.now().isoformat()

        # Log the session start event
        log_session_start(input_data)

        # Load and output project memory context (on by default)
        if not args.no_context:
            context = load_development_context(source, cwd)
            if context:
                output = {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": context
                    }
                }
                print(json.dumps(output))

        sys.exit(0)

    except json.JSONDecodeError:
        # Handle JSON decode errors gracefully
        sys.exit(0)
    except Exception:
        # Handle any other errors gracefully
        sys.exit(0)


if __name__ == '__main__':
    main()
