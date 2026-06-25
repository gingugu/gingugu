#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "anthropic",
#     "python-dotenv",
# ]
# ///

"""
Task Summarizer LLM Utility

Generates natural language summaries of subagent task completions.
Designed for TTS announcements to provide personalized feedback.

Uses ENGINEER_NAME env var for personalization when available.
"""

import os
import sys
from datetime import datetime

from dotenv import load_dotenv


def debug_log(message: str) -> None:
    """Write debug message to logs/subagent_debug.log"""
    try:
        log_dir = os.path.join(os.getcwd(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        debug_path = os.path.join(log_dir, "subagent_debug.log")
        timestamp = datetime.now().isoformat()
        with open(debug_path, "a") as f:
            f.write(f"[{timestamp}] [SUMMARIZER] {message}\n")
    except Exception:
        pass


def summarize_subagent_task(
    task_description: str,
    agent_name: str | None = None,
) -> str:
    """
    Generate a natural language summary of a completed task.

    Args:
        task_description: Description of the completed task
        agent_name: Optional name of the completing agent

    Returns:
        str: A conversational summary for TTS announcement
    """
    load_dotenv()
    debug_log(
        f"summarize_subagent_task called with: "
        f"{task_description[:50]}..."
    )

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        debug_log("ERROR: ANTHROPIC_API_KEY not found!")
        return "Subagent task completed"

    debug_log(f"API key found (length: {len(api_key)})")

    # Get engineer name from environment for personalization
    engineer_name = os.getenv("ENGINEER_NAME", "").strip()

    # Build name instruction based on ENGINEER_NAME
    if engineer_name:
        name_instruction = (
            f'Address the user as "{engineer_name}" '
            f"directly (but not always at the start)"
        )
        name_examples = (
            f'- "{engineer_name}, authentication is ready '
            f'with secure JWT token support."\n'
            f'- "Your file watcher is now monitoring '
            f'for changes."\n'
            f'- "Builder finished setting up the TTS queue '
            f'with file locks."\n'
            f'- "{engineer_name}, the new API endpoints '
            f'are live and tested."'
        )
    else:
        name_instruction = (
            "Address the user naturally "
            "without a specific name"
        )
        name_examples = (
            '- "Authentication is ready with secure '
            'JWT token support."\n'
            '- "Your file watcher is now monitoring '
            'for changes."\n'
            '- "Builder finished setting up the TTS queue '
            'with file locks."\n'
            '- "The new API endpoints are live and tested."'
        )

    # Build agent context for the prompt
    if agent_name:
        agent_context = (
            f"The agent named '{agent_name}' "
            f"completed this task."
        )
        agent_instruction = (
            f"You can reference the agent by name "
            f"('{agent_name}') naturally."
        )
    else:
        agent_context = "A subagent completed this task."
        agent_instruction = (
            "Refer to it as 'your agent' or similar."
        )

    prompt = (
        "Generate a brief, conversational summary of a "
        "completed task for audio announcement.\n\n"
        f"Task completed: {task_description}\n\n"
        f"Context: {agent_context}\n\n"
        "Requirements:\n"
        f"- {name_instruction}\n"
        "- Keep it under 20 words\n"
        "- Focus on the outcome and value delivered\n"
        "- Be conversational and personalized\n"
        f"- {agent_instruction}\n"
        "- Do NOT include quotes, formatting, "
        "or explanations\n"
        "- Return ONLY the summary text\n\n"
        f"Example styles:\n{name_examples}\n\n"
        "Generate ONE summary:"
    )

    try:
        import anthropic

        debug_log("Anthropic module imported successfully")

        client = anthropic.Anthropic(api_key=api_key)
        debug_log("Anthropic client created")

        debug_log("Calling Haiku API...")
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            temperature=0.7,
            messages=[{"role": "user", "content": prompt}],
        )
        debug_log("API call completed")

        response = message.content[0].text.strip()
        debug_log(f"Raw response: {response}")

        if response:
            response = (
                response.strip().strip('"').strip("'").strip()
            )
            response = response.split("\n")[0].strip()
            debug_log(f"Cleaned response: {response}")
            return response

        debug_log("Response was empty, returning fallback")
        return "Subagent task completed"

    except Exception as e:
        debug_log(
            f"EXCEPTION: {type(e).__name__}: {e!s}"
        )
        return "Subagent task completed"


def main() -> None:
    """Command line interface for testing."""
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Generate natural language summaries "
            "of subagent task completions"
        ),
    )
    parser.add_argument(
        "task_description",
        nargs="?",
        help="Description of the completed task",
    )
    parser.add_argument(
        "--agent-name",
        "-a",
        type=str,
        default=None,
        help="Name of the completing agent",
    )

    args = parser.parse_args()

    if not args.task_description:
        parser.print_help()
        print("\nExamples:")
        print(
            "  uv run task_summarizer.py "
            '"Built authentication system"'
        )
        print(
            "  uv run task_summarizer.py "
            '"Built auth system" --agent-name "builder"'
        )
        sys.exit(1)

    summary = summarize_subagent_task(
        args.task_description, args.agent_name
    )
    print(summary)


if __name__ == "__main__":
    main()
