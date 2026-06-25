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
import sys
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional


def log_post_compact(input_data):
    """Log post-compact event to logs directory."""
    log_dir = os.path.join(os.getcwd(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "post_compact.json")

    if os.path.exists(log_path):
        with open(log_path) as f:
            try:
                log_data = json.load(f)
            except (json.JSONDecodeError, ValueError):
                log_data = []
    else:
        log_data = []

    log_data.append(input_data)

    with open(log_path, 'w') as f:
        json.dump(log_data, f, indent=2)


def main():
    try:
        # Parse command line arguments
        parser = argparse.ArgumentParser()
        parser.add_argument('--no-reinject', action='store_true',
                          help='Skip re-injecting .ai/memory.md after compaction')
        args = parser.parse_args()

        # Read JSON input from stdin
        input_data = json.load(sys.stdin)

        # Add timestamp to input data for logging
        input_data["logged_at"] = datetime.now().isoformat()

        # Log the event
        log_post_compact(input_data)

        # Re-inject .ai/memory.md after compaction (on by default)
        if not args.no_reinject:
            cwd = input_data.get('cwd', os.getcwd())
            memory_file = Path(cwd, ".ai", "memory.md")

            if memory_file.exists():
                try:
                    content = memory_file.read_text().strip()
                    if content:
                        output = {
                            "hookSpecificOutput": {
                                "hookEventName": "PostCompact",
                                "additionalContext": content[:2000]
                            }
                        }
                        print(json.dumps(output))
                except Exception:
                    pass

        sys.exit(0)

    except json.JSONDecodeError:
        # Handle JSON decode errors gracefully
        sys.exit(0)
    except Exception:
        # Handle any other errors gracefully
        sys.exit(0)


if __name__ == "__main__":
    main()
