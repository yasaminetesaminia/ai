"""Upload (or refresh) the clinic's pronunciation dictionary to ElevenLabs.

Run from the repo root any time you've edited services.pronunciation.RULES:
    python scripts/upload_pronunciation.py

Prints the new dictionary_id + version_id and saves them to
conversations/pronunciation_state.json so subsequent TTS calls pick them
up automatically.

Pass --reset to forget the cached state before re-uploading (forces a
fresh upload even if a state file already exists).
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services import pronunciation  # noqa: E402


def main() -> None:
    if "--reset" in sys.argv:
        pronunciation.reset_state()
        print("Cached state cleared.")

    print(f"Uploading {len(pronunciation.RULES)} pronunciation rules...")
    state = pronunciation.upload_dictionary()
    print()
    print(f"  dictionary_id : {state['dictionary_id']}")
    print(f"  version_id    : {state['version_id']}")
    print(f"  rules_count   : {state['rules_count']}")
    print()
    print("Saved to conversations/pronunciation_state.json")
    print("Next TTS call will use this dictionary automatically.")


if __name__ == "__main__":
    main()
