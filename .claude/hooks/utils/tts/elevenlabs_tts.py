#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "elevenlabs",
#     "python-dotenv",
# ]
# ///

import os
import sys

from dotenv import load_dotenv


def main():
    """
    ElevenLabs Turbo v2.5 TTS Script

    Uses ElevenLabs' Turbo v2.5 model for fast, high-quality TTS.
    Accepts optional text prompt as command-line argument.

    Usage:
    - ./elevenlabs_tts.py                    # Uses default text
    - ./elevenlabs_tts.py "Your custom text" # Uses provided text
    """

    load_dotenv()

    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        print(
            "Error: ELEVENLABS_API_KEY not found in "
            "environment variables"
        )
        sys.exit(1)

    try:
        from elevenlabs import play
        from elevenlabs.client import ElevenLabs

        elevenlabs = ElevenLabs(api_key=api_key)

        print("ElevenLabs Turbo v2.5 TTS")
        print("=" * 40)

        if len(sys.argv) > 1:
            text = " ".join(sys.argv[1:])
        else:
            text = "The first move is what sets everything in motion."

        print(f"Text: {text}")
        print("Generating and playing...")

        try:
            audio = elevenlabs.text_to_speech.convert(
                text=text,
                voice_id="WejK3H1m7MI9CHnIjW9K",
                model_id="eleven_turbo_v2_5",
                output_format="mp3_44100_128",
            )

            play(audio)
            print("Playback complete!")

        except Exception as e:
            print(f"Error: {e}")

    except ImportError:
        print("Error: elevenlabs package not installed")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
