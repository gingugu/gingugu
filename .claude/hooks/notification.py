#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "python-dotenv",
# ]
# ///

"""
Notification Hook

Announces via TTS when the agent needs user input.
Personalizes with ENGINEER_NAME env var (30% chance to include name).
Skips the generic "Claude is waiting for your input" message.
"""

import argparse
import json
import os
import random
import subprocess
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


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


def announce_notification():
    """Announce that the agent needs user input."""
    try:
        tts_script = get_tts_script_path()
        if not tts_script:
            return

        engineer_name = os.getenv("ENGINEER_NAME", "").strip()

        # 30% chance to include name if available
        if engineer_name and random.random() < 0.3:
            msg = f"{engineer_name}, your agent needs your input"
        else:
            msg = "Your agent needs your input"

        subprocess.run(
            ["uv", "run", tts_script, msg],
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


def main():
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--notify",
            action="store_true",
            help="Enable TTS notifications",
        )
        args = parser.parse_args()

        input_data = json.loads(sys.stdin.read())

        # Ensure log directory exists
        log_dir = os.path.join(os.getcwd(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "notification.json")

        if os.path.exists(log_file):
            with open(log_file) as f:
                try:
                    log_data = json.load(f)
                except (json.JSONDecodeError, ValueError):
                    log_data = []
        else:
            log_data = []

        log_data.append(input_data)

        with open(log_file, "w") as f:
            json.dump(log_data, f, indent=2)

        # Announce via TTS only if --notify flag is set
        # Skip generic "Claude is waiting for your input" message
        waiting_msg = "Claude is waiting for your input"
        if args.notify and input_data.get("message") != waiting_msg:
            announce_notification()

        sys.exit(0)

    except json.JSONDecodeError:
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
