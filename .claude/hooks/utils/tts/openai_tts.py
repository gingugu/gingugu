#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "openai",
#     "openai[voice_helpers]",
#     "python-dotenv",
# ]
# ///

import asyncio
import os
import sys

from dotenv import load_dotenv


async def main():
    """
    OpenAI TTS Script

    Uses OpenAI's latest TTS model for high-quality text-to-speech.
    Accepts optional text prompt as command-line argument.

    Usage:
    - ./openai_tts.py                    # Uses default text
    - ./openai_tts.py "Your custom text" # Uses provided text
    """

    load_dotenv()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print(
            "Error: OPENAI_API_KEY not found in "
            "environment variables"
        )
        sys.exit(1)

    try:
        from openai import AsyncOpenAI
        from openai.helpers import LocalAudioPlayer

        openai = AsyncOpenAI(api_key=api_key)

        print("OpenAI TTS")
        print("=" * 20)

        if len(sys.argv) > 1:
            text = " ".join(sys.argv[1:])
        else:
            text = (
                "Today is a wonderful day to build "
                "something people love!"
            )

        print(f"Text: {text}")
        print("Generating and streaming...")

        try:
            async with (
                openai.audio.speech.with_streaming_response.create(
                    model="gpt-4o-mini-tts",
                    voice="nova",
                    input=text,
                    instructions=(
                        "Speak in a cheerful, positive "
                        "yet professional tone."
                    ),
                    response_format="mp3",
                ) as response
            ):
                await LocalAudioPlayer().play(response)

            print("Playback complete!")

        except Exception as e:
            print(f"Error: {e}")

    except ImportError:
        print("Error: Required package not installed")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
