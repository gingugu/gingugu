#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "anthropic",
#     "python-dotenv",
# ]
# ///

import os
import sys

from dotenv import load_dotenv


def prompt_llm(prompt_text):
    """
    Base Anthropic LLM prompting method using fastest model.

    Args:
        prompt_text (str): The prompt to send to the model

    Returns:
        str: The model's response text, or None if error
    """
    load_dotenv()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        message = client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=100,
            temperature=0.7,
            messages=[
                {"role": "user", "content": prompt_text}
            ],
        )

        return message.content[0].text.strip()

    except Exception:
        return None


def generate_completion_message():
    """
    Generate a completion message using Anthropic LLM.

    Returns:
        str: A natural language completion message, or None
    """
    engineer_name = os.getenv("ENGINEER_NAME", "").strip()

    if engineer_name:
        name_instruction = (
            "Sometimes (about 30% of the time) include "
            f"the engineer's name '{engineer_name}' "
            "in a natural way."
        )
        examples = (
            "Examples of the style:\n"
            '- Standard: "Work complete!", "All done!", '
            '"Task finished!", "Ready for your next move!"\n'
            f'- Personalized: "{engineer_name}, all set!", '
            f'"Ready for you, {engineer_name}!", '
            f'"Complete, {engineer_name}!", '
            f'"{engineer_name}, we\'re done!"'
        )
    else:
        name_instruction = ""
        examples = (
            "Examples of the style: "
            '"Work complete!", "All done!", '
            '"Task finished!", "Ready for your next move!"'
        )

    prompt = (
        "Generate a short, friendly completion message "
        "for when an AI coding assistant finishes a task."
        "\n\nRequirements:\n"
        "- Keep it under 10 words\n"
        "- Make it positive and future focused\n"
        "- Use natural, conversational language\n"
        "- Focus on completion/readiness\n"
        "- Do NOT include quotes, formatting, "
        "or explanations\n"
        "- Return ONLY the completion message text\n"
        f"{name_instruction}\n\n"
        f"{examples}\n\n"
        "Generate ONE completion message:"
    )

    response = prompt_llm(prompt)

    if response:
        response = (
            response.strip().strip('"').strip("'").strip()
        )
        response = response.split("\n")[0].strip()

    return response


def generate_agent_name():
    """
    Generate a one-word agent name using Anthropic.

    Returns:
        str: A single-word agent name, or fallback name
    """
    import random

    example_names = [
        "Phoenix",
        "Sage",
        "Nova",
        "Echo",
        "Atlas",
        "Cipher",
        "Nexus",
        "Oracle",
        "Quantum",
        "Zenith",
        "Aurora",
        "Vortex",
        "Nebula",
        "Catalyst",
        "Prism",
        "Axiom",
        "Helix",
        "Flux",
        "Synth",
        "Vertex",
    ]

    if not os.getenv("ANTHROPIC_API_KEY"):
        return random.choice(example_names)

    examples_str = ", ".join(example_names[:10])

    prompt_text = (
        "Generate exactly ONE unique agent/assistant name."
        "\n\nRequirements:\n"
        "- Single word only (no spaces, hyphens, "
        "or punctuation)\n"
        "- Abstract and memorable\n"
        "- Professional sounding\n"
        "- Easy to pronounce\n"
        f"- Similar style to: {examples_str}\n\n"
        "Generate a NEW name (not from the examples). "
        "Respond with ONLY the name, nothing else.\n\n"
        "Name:"
    )

    try:
        load_dotenv()
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise Exception("No API key")

        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        message = client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=20,
            temperature=0.7,
            messages=[
                {"role": "user", "content": prompt_text}
            ],
        )

        name = message.content[0].text.strip()
        name = name.split()[0] if name else "Agent"
        name = "".join(c for c in name if c.isalnum())
        name = name.capitalize() if name else "Agent"

        if name and 3 <= len(name) <= 20:
            return name
        else:
            raise Exception("Invalid name generated")

    except Exception:
        return random.choice(example_names)


def main():
    """Command line interface for testing."""
    if len(sys.argv) > 1:
        if sys.argv[1] == "--completion":
            message = generate_completion_message()
            if message:
                print(message)
            else:
                print("Error generating completion message")
        elif sys.argv[1] == "--agent-name":
            name = generate_agent_name()
            print(name)
        else:
            prompt_text = " ".join(sys.argv[1:])
            response = prompt_llm(prompt_text)
            if response:
                print(response)
            else:
                print("Error calling Anthropic API")
    else:
        print(
            "Usage: ./anth.py 'your prompt here' "
            "or ./anth.py --completion "
            "or ./anth.py --agent-name"
        )


if __name__ == "__main__":
    main()
