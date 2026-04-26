"""ElevenLabs pronunciation dictionary for clinic-specific terms.

Why a dictionary helps: even with a cloned Omani voice, the TTS engine
reads the text first and synthesizes second. Brand names, acronyms,
and English-in-Arabic loanwords often get mangled — "OMR" gets spelled
as letters ("O-M-R") instead of "Omani rials"; "ovvocompany" gets
read as one mumbled blob instead of "ovvo company".

The dictionary is a list of alias rules — `string_to_replace` is what
appears in the prompt's text, `alias` is what the TTS actually
synthesizes. We upload the rules once to ElevenLabs, store the
returned dictionary_id + version_id, then attach those locators to
every TTS call.

Add new rules to RULES then run:
    python scripts/upload_pronunciation.py
to get a fresh version (the old one stays around as a previous version).
"""

import json
import logging
import os
import threading
from pathlib import Path

import requests

import config

logger = logging.getLogger(__name__)

_API_BASE = "https://api.elevenlabs.io/v1"
_STATE_FILE = (
    Path(__file__).resolve().parent.parent
    / "conversations"
    / "pronunciation_state.json"
)
_lock = threading.Lock()

DICTIONARY_NAME = "Noora Clinic Pronunciation"
DICTIONARY_DESCRIPTION = (
    "Custom pronunciations for Noora Clinic — brand names, medical "
    "terms, doctor names, and Arabic-English loanwords."
)


# Each rule rewrites the first column to the second BEFORE synthesis.
# Phonetic rules (IPA) are also possible but aliases cover 90% of needs
# without requiring IPA knowledge.
RULES = [
    # ---- Clinic brand & contact ----
    {"string_to_replace": "Noora Clinic", "alias": "Noora Clinic"},
    {"string_to_replace": "ovvocompany", "alias": "ovvo company"},
    {"string_to_replace": "@ovvocompany", "alias": "at ovvo company"},

    # ---- Currency ----
    # Without this, "OMR" gets read as "O M R" letter-by-letter.
    {"string_to_replace": "OMR", "alias": "Omani rials"},
    {"string_to_replace": " omr ", "alias": " Omani rials "},

    # ---- Common English loanwords inside Arabic context ----
    # The cloned Omani voice handles pure Arabic well, but mid-Arabic
    # English brand names sometimes blur. Splitting helps.
    {"string_to_replace": "واتساب", "alias": "واتس آب"},
    {"string_to_replace": "إنستغرام", "alias": "إنستا غرام"},
    {"string_to_replace": "WhatsApp", "alias": "WhatsApp"},
    {"string_to_replace": "Instagram", "alias": "Instagram"},

    # ---- Medical / aesthetic terms in Arabic ----
    # Diacritics encourage the TTS to use the right vowels. Cloned
    # voices respect the script — adding short vowels (fatḥa/kasra/ḍamma)
    # gives the engine more signal.
    {"string_to_replace": "بوتوكس", "alias": "بُوتُوكْس"},
    {"string_to_replace": "فيلر", "alias": "فِيلَر"},
    {"string_to_replace": "ليزر", "alias": "لَيزَر"},
    {"string_to_replace": "شفارزي", "alias": "شِفارزي"},
    {"string_to_replace": "أوندا", "alias": "أُوندا"},
    {"string_to_replace": "راديو ستيم", "alias": "رَاديو سْتيم"},
    {"string_to_replace": "حشو", "alias": "حَشْو"},
    {"string_to_replace": "تلبيس", "alias": "تَلبيس"},
    {"string_to_replace": "زراعة", "alias": "زِرَاعة"},

    # ---- Doctor names — ensure correct vowels ----
    {"string_to_replace": "الدكتورة سارة", "alias": "الدكتورة سَارة"},
    {"string_to_replace": "الدكتورة أماني", "alias": "الدكتورة أَماني"},
    {"string_to_replace": "الدكتور حسين", "alias": "الدكتور حُسَين"},
    {"string_to_replace": "الدكتورة إيناس", "alias": "الدكتورة إِيناس"},

    # ---- Location ----
    {"string_to_replace": "الغبرة", "alias": "الغُبرَة"},
    {"string_to_replace": "Al Ghubra", "alias": "Al Ghubra"},
]


def _load_state() -> dict:
    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def upload_dictionary(rules: list[dict] | None = None) -> dict:
    """Push the rules to ElevenLabs and return the new dictionary state.

    Always creates a fresh dictionary version — old versions stay live
    until something else references them, so previously-cached locators
    keep working until we update state.
    """
    if not config.ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY not set")
    rules = rules or RULES
    payload_rules = [
        {"type": "alias", "string_to_replace": r["string_to_replace"], "alias": r["alias"]}
        for r in rules
    ]
    body = {
        "name": DICTIONARY_NAME,
        "description": DICTIONARY_DESCRIPTION,
        "rules": payload_rules,
    }
    response = requests.post(
        f"{_API_BASE}/pronunciation-dictionaries/add-from-rules",
        headers={
            "xi-api-key": config.ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"ElevenLabs dictionary upload failed {response.status_code}: "
            f"{response.text[:300]}"
        )
    data = response.json()
    state = {
        "dictionary_id": data.get("id") or data.get("pronunciation_dictionary_id"),
        "version_id": data.get("version_id"),
        "rules_count": len(payload_rules),
    }
    with _lock:
        _save_state(state)
    logger.info(
        "Pronunciation dictionary uploaded: id=%s version=%s rules=%d",
        state["dictionary_id"], state["version_id"], state["rules_count"],
    )
    return state


def get_active_locators() -> list[dict]:
    """Locators to attach to TTS calls. Uploads on first use, caches after.

    Returns [] if upload fails — TTS still works without it.
    """
    state = _load_state()
    if not state.get("dictionary_id") or not state.get("version_id"):
        try:
            state = upload_dictionary()
        except Exception as e:
            logger.warning("Pronunciation dictionary auto-upload failed: %s", e)
            return []
    return [{
        "pronunciation_dictionary_id": state["dictionary_id"],
        "version_id": state["version_id"],
    }]


def reset_state() -> None:
    """Forget the cached dictionary IDs so the next get_active_locators()
    re-uploads. Useful when you've edited RULES and want a fresh version.
    """
    with _lock:
        if _STATE_FILE.exists():
            _STATE_FILE.unlink()
