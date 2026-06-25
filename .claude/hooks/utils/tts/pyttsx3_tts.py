#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "pyttsx3",
# ]
# ///

import random
import sys


def main():
    """
    pyttsx3 TTS Script

    Uses pyttsx3 for offline text-to-speech synthesis.
    Accepts optional text prompt as command-line argument.

    Usage:
    - ./pyttsx3_tts.py                    # Uses default text
    - ./pyttsx3_tts.py "Your custom text" # Uses provided text
    """

    try:
        import pyttsx3

        engine = pyttsx3.init()

        engine.setProperty("rate", 180)
        engine.setProperty("volume", 0.8)

        print("pyttsx3 TTS")
        print("=" * 15)

        if len(sys.argv) > 1:
            text = " ".join(sys.argv[1:])
        else:
            completion_messages = [
                "Work complete!",
                "All done!",
                "Task finished!",
                "Job complete!",
                "Ready for next task!",
            ]
            text = random.choice(completion_messages)

        print(f"Text: {text}")
        print("Speaking...")

        engine.say(text)
        engine.runAndWait()

        print("Playback complete!")

    except ImportError:
        print("Error: pyttsx3 package not installed")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
