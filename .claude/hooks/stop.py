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

        if args.notify:
            announce_completion()

        sys.exit(0)

    except json.JSONDecodeError:
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
