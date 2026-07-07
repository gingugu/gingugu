#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "python-dotenv",
# ]
# ///

"""
Stop Hook

Announces task completion via TTS when the agent stops.
Supports LLM-generated completion messages and transcript logging.

With --check-memory-saves, also enforces Gingugu save discipline: if the
session shows substantial tool activity but ZERO gingugu memory writes, the
stop is blocked once (per session) with a reminder to save before context is
lost. Guards the "unsaved session vanishes" failure mode.
"""

import argparse
import json
import os
import random
import subprocess
import sys
from pathlib import Path

# Gingugu write tools — any of these counts as "the session saved something".
MEMORY_WRITE_TOOLS = {
    "mcp__gingugu__memory_store",
    "mcp__gingugu__memory_update",
    "mcp__gingugu__memory_relate",
    "mcp__gingugu__memory_consolidate",
    "mcp__gingugu__memory_forget",
}

# Below this many total tool calls the session is treated as conversational —
# no nudge (a quick Q&A turn shouldn't trip the check).
DEFAULT_MIN_TOOL_CALLS = 15

SAVE_REMINDER = (
    "Gingugu save-discipline check: this session has substantial tool activity "
    "({total} tool calls) but ZERO gingugu memory writes. Unsaved sessions "
    "vanish - save what this session learned NOW: memory_store the decisions, "
    "bugs, patterns, and outcomes (project namespace; crow for cross-project "
    "lessons), then memory_relate the new memories to their cluster. If there "
    "is genuinely nothing worth saving, you may stop again and this check will "
    "not re-fire this session."
)

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def get_completion_messages():
    """Return list of friendly completion messages."""
    return [
        "Work complete!",
        "All done!",
        "Task finished!",
        "Job complete!",
        "Ready for next task!",
    ]


def get_tts_script_path():
    """
    Determine which TTS script to use based on available API keys.
    Priority order: ElevenLabs > OpenAI > pyttsx3
    """
    script_dir = Path(__file__).parent
    tts_dir = script_dir / "utils" / "tts"

    if os.getenv("ELEVENLABS_API_KEY"):
        elevenlabs_script = tts_dir / "elevenlabs_tts.py"
        if elevenlabs_script.exists():
            return str(elevenlabs_script)

    if os.getenv("OPENAI_API_KEY"):
        openai_script = tts_dir / "openai_tts.py"
        if openai_script.exists():
            return str(openai_script)

    pyttsx3_script = tts_dir / "pyttsx3_tts.py"
    if pyttsx3_script.exists():
        return str(pyttsx3_script)

    return None


def get_llm_completion_message():
    """
    Generate completion message using available LLM services.
    Priority order: Anthropic > fallback to random message
    """
    script_dir = Path(__file__).parent
    llm_dir = script_dir / "utils" / "llm"

    # Try Anthropic
    if os.getenv("ANTHROPIC_API_KEY"):
        anth_script = llm_dir / "anth.py"
        if anth_script.exists():
            try:
                result = subprocess.run(
                    ["uv", "run", str(anth_script), "--completion"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip()
            except (
                subprocess.TimeoutExpired,
                subprocess.SubprocessError,
            ):
                pass

    return random.choice(get_completion_messages())


def announce_completion():
    """Announce completion using the best available TTS."""
    try:
        tts_script = get_tts_script_path()
        if not tts_script:
            return

        completion_message = get_llm_completion_message()

        subprocess.run(
            ["uv", "run", tts_script, completion_message],
            capture_output=True,
            timeout=10,
        )

    except (
        subprocess.TimeoutExpired,
        subprocess.SubprocessError,
        FileNotFoundError,
    ):
        pass
    except Exception:
        pass


def _collect_tool_names(node, out):
    """Recursively collect tool_use block names from a transcript entry."""
    if isinstance(node, dict):
        if node.get("type") == "tool_use" and isinstance(node.get("name"), str):
            out.append(node["name"])
        for value in node.values():
            _collect_tool_names(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_tool_names(value, out)


def check_memory_saves(input_data, min_tool_calls):
    """Return a block decision when real work happened but nothing was saved.

    Blocks at most once per session (state flag under logs/save_check/), so a
    session with genuinely nothing to save can stop on the second attempt.
    Fail-soft: any parsing problem means no block.
    """
    try:
        if input_data.get("stop_hook_active"):
            return None  # already continuing from a blocked stop
        transcript_path = input_data.get("transcript_path")
        if not transcript_path or not os.path.exists(transcript_path):
            return None

        session_id = str(input_data.get("session_id") or "unknown")
        state_file = Path(os.getcwd()) / "logs" / "save_check" / f"{session_id}.flag"
        if state_file.exists():
            return None  # one nudge per session, ever

        tool_names = []
        with open(transcript_path) as fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue
                try:
                    _collect_tool_names(json.loads(line), tool_names)
                except json.JSONDecodeError:
                    continue

        total = len(tool_names)
        saves = sum(1 for name in tool_names if name in MEMORY_WRITE_TOOLS)
        if total < min_tool_calls or saves > 0:
            return None

        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("nudged")
        return {"decision": "block", "reason": SAVE_REMINDER.format(total=total)}
    except Exception:
        return None


def main():
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--chat",
            action="store_true",
            help="Copy transcript to chat.json",
        )
        parser.add_argument(
            "--notify",
            action="store_true",
            help="Enable TTS completion announcement",
        )
        parser.add_argument(
            "--check-memory-saves",
            action="store_true",
            help="Block the stop once if the session did work but never saved to gingugu",
        )
        parser.add_argument(
            "--min-tool-calls",
            type=int,
            default=DEFAULT_MIN_TOOL_CALLS,
            help="Tool-call threshold below which the save check stays quiet",
        )
        args = parser.parse_args()

        input_data = json.load(sys.stdin)

        # Ensure log directory exists
        log_dir = os.path.join(os.getcwd(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "stop.json")

        if os.path.exists(log_path):
            with open(log_path) as f:
                try:
                    log_data = json.load(f)
                except (json.JSONDecodeError, ValueError):
                    log_data = []
        else:
            log_data = []

        log_data.append(input_data)

        with open(log_path, "w") as f:
            json.dump(log_data, f, indent=2)

        # Handle --chat switch: save transcript as JSON
        if args.chat and "transcript_path" in input_data:
            transcript_path = input_data["transcript_path"]
            if os.path.exists(transcript_path):
                chat_data = []
                try:
                    with open(transcript_path) as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    chat_data.append(
                                        json.loads(line)
                                    )
                                except json.JSONDecodeError:
                                    pass

                    chat_file = os.path.join(
                        log_dir, "chat.json"
                    )
                    with open(chat_file, "w") as f:
                        json.dump(chat_data, f, indent=2)
                except Exception:
                    pass

        if args.check_memory_saves:
            decision = check_memory_saves(input_data, args.min_tool_calls)
            if decision is not None:
                print(json.dumps(decision))
                sys.exit(0)

        if args.notify:
            announce_completion()

        sys.exit(0)

    except json.JSONDecodeError:
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
