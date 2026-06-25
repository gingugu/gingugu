#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "python-dotenv",
#     "anthropic",
# ]
# ///

"""
SubagentStop Hook

Announces subagent task completion via TTS with AI-generated summaries.
Uses TTS queue locking to prevent overlapping announcements.
"""

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
    pass


def debug_log(message: str) -> None:
    """Write debug message to logs/subagent_debug.log"""
    try:
        log_dir = os.path.join(os.getcwd(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        debug_path = os.path.join(log_dir, "subagent_debug.log")
        timestamp = datetime.now().isoformat()
        with open(debug_path, "a") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass


# Add hooks directory to path for local imports
sys.path.insert(0, str(Path(__file__).parent))

try:
    from utils.tts.tts_queue import (
        acquire_tts_lock,
        cleanup_stale_locks,
        release_tts_lock,
    )
except ImportError:

    def acquire_tts_lock(
        agent_id: str, timeout: int = 30
    ) -> bool:
        return True

    def release_tts_lock(agent_id: str) -> None:
        pass

    def cleanup_stale_locks(max_age_seconds: int = 60) -> None:
        pass


try:
    from utils.llm.task_summarizer import summarize_subagent_task
except ImportError:

    def summarize_subagent_task(
        task_description: str,
        agent_name: str | None = None,
    ) -> str:
        return "Subagent Complete"


def get_tts_script_path() -> str | None:
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


def extract_task_context(input_data: dict) -> str:
    """
    Extract task context from the subagent input data.
    Reads the initial task/prompt from the JSONL transcript.
    """
    transcript_path = input_data.get("agent_transcript_path")
    if not transcript_path:
        transcript_path = input_data.get("transcript_path")

    if not transcript_path or not os.path.exists(transcript_path):
        return "completed a task"

    try:
        with open(transcript_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    entry_type = entry.get("type", "")

                    if entry_type == "user":
                        message = entry.get("message", {})
                        content = (
                            message.get("content", "")
                            if isinstance(message, dict)
                            else ""
                        )

                        if not content:
                            content = entry.get("content", "")

                        if isinstance(content, str) and content:
                            if len(content) > 200:
                                return content[:200] + "..."
                            return content
                        elif isinstance(content, list):
                            for block in content:
                                if (
                                    isinstance(block, dict)
                                    and block.get("type")
                                    == "text"
                                ):
                                    text = block.get("text", "")
                                    if text:
                                        if len(text) > 200:
                                            return (
                                                text[:200] + "..."
                                            )
                                        return text

                    prompt = entry.get("prompt", "")
                    if prompt:
                        if len(prompt) > 200:
                            return prompt[:200] + "..."
                        return prompt

                except json.JSONDecodeError:
                    continue

    except OSError:
        pass

    return "completed a task"


def announce_subagent_completion(
    message: str = "Subagent Complete",
) -> None:
    """Announce subagent completion via TTS."""
    try:
        tts_script = get_tts_script_path()
        if not tts_script:
            return

        subprocess.run(
            ["uv", "run", tts_script, message],
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


def main() -> None:
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
            "--summarize",
            action="store_true",
            default=True,
            help="Generate AI summary (default: on)",
        )
        parser.add_argument(
            "--no-summarize",
            dest="summarize",
            action="store_false",
            help="Disable AI summary, use generic message",
        )
        args = parser.parse_args()

        input_data = json.load(sys.stdin)

        # Ensure log directory exists
        log_dir = os.path.join(os.getcwd(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "subagent_stop.json")

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

        # Handle --chat switch
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

        # Announce subagent completion via TTS
        if args.notify:
            agent_id = input_data.get("agent_id", "unknown")
            debug_log(
                f"=== SubagentStop for agent: {agent_id} ==="
            )
            tp = input_data.get(
                "agent_transcript_path", "NOT FOUND"
            )
            debug_log(f"agent_transcript_path: {tp}")
            has_key = bool(os.getenv("ANTHROPIC_API_KEY"))
            debug_log(f"ANTHROPIC_API_KEY present: {has_key}")

            cleanup_stale_locks(max_age_seconds=60)

            if args.summarize:
                task_context = extract_task_context(input_data)
                debug_log(
                    f"Extracted task_context: "
                    f"{task_context[:100]}..."
                )
                summary_message = summarize_subagent_task(
                    task_context, agent_name=agent_id
                )
                debug_log(
                    f"Generated summary_message: "
                    f"{summary_message}"
                )
            else:
                summary_message = "Subagent Complete"
                debug_log(
                    "Summarize disabled, using default message"
                )

            if acquire_tts_lock(agent_id, timeout=30):
                try:
                    debug_log(
                        f"Lock acquired, announcing: "
                        f"{summary_message}"
                    )
                    announce_subagent_completion(summary_message)
                finally:
                    release_tts_lock(agent_id)
                    debug_log("Lock released")
            else:
                debug_log(
                    f"Lock timeout, announcing anyway: "
                    f"{summary_message}"
                )
                announce_subagent_completion(summary_message)

        sys.exit(0)

    except json.JSONDecodeError:
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
