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

DICTIONARY_NAME = "Lavora Clinic Pronunciation"
DICTIONARY_DESCRIPTION = (
    "Custom pronunciations for Lavora Clinic — brand names, medical "
    "terms, doctor names, and Arabic-English loanwords."
)


# Each rule rewrites the first column to the second BEFORE synthesis.
# Phonetic rules (IPA) are also possible but aliases cover 90% of needs
# without requiring IPA knowledge.
RULES = [
    # ---- Clinic brand & contact ----
    {"string_to_replace": "Lavora Clinic", "alias": "Lavora Clinic"},
    {"string_to_replace": "Lavora", "alias": "Lavora"},
    {"string_to_replace": "lavoraclinic.com", "alias": "lavora clinic dot com"},
    {"string_to_replace": "lavoraclinic.om", "alias": "lavora clinic dot O M"},
    {"string_to_replace": "info@lavoraclinic.com", "alias": "info at lavora clinic dot com"},

    # ---- Currency ----
    # Without this, "OMR" gets read as "O M R" letter-by-letter.
    {"string_to_replace": "OMR", "alias": "Omani rials"},
    {"string_to_replace": " omr ", "alias": " Omani rials "},

    # ---- Common English loanwords inside Arabic context ----
    {"string_to_replace": "واتساب", "alias": "واتس آب"},
    {"string_to_replace": "إنستغرام", "alias": "إنستا غرام"},
    {"string_to_replace": "WhatsApp", "alias": "WhatsApp"},
    {"string_to_replace": "Instagram", "alias": "Instagram"},

    # ---- Aesthetic / dermatology brand names ----
    # Lavora's service catalog is full of branded device & treatment names
    # that English-trained voices read decently but Arabic-trained voices
    # mangle. Aliases force the right syllable break.
    {"string_to_replace": "Frax Pro", "alias": "Frax Pro"},
    {"string_to_replace": "Picoway", "alias": "Pico Way"},
    {"string_to_replace": "RedTouch", "alias": "Red Touch"},
    {"string_to_replace": "Profhilo", "alias": "Pro Filo"},
    {"string_to_replace": "Polynucleotides", "alias": "Poly nucleotides"},
    {"string_to_replace": "Endolift", "alias": "Endo lift"},
    {"string_to_replace": "Fotona 4D", "alias": "Fotona four D"},
    {"string_to_replace": "Onda Plus", "alias": "Onda Plus"},
    {"string_to_replace": "Redustim", "alias": "Redu stim"},
    {"string_to_replace": "Aptos", "alias": "Aptos"},
    {"string_to_replace": "Vaginoplasty", "alias": "Vagino plasty"},
    {"string_to_replace": "Labiaplasty", "alias": "Labia plasty"},
    {"string_to_replace": "Mesotherapy", "alias": "Meso therapy"},
    {"string_to_replace": "Exosome", "alias": "Exo some"},
    {"string_to_replace": "PRP", "alias": "P R P"},

    # ---- Medical / aesthetic terms in Arabic ----
    # Diacritics encourage the TTS to use the right vowels.
    {"string_to_replace": "بوتوكس", "alias": "بُوتُوكْس"},
    {"string_to_replace": "فيلر", "alias": "فِيلَر"},
    {"string_to_replace": "ليزر", "alias": "لَيزَر"},
    {"string_to_replace": "أوندا", "alias": "أُوندا"},
    {"string_to_replace": "بروفايلو", "alias": "بُروفَايلو"},
    {"string_to_replace": "ميزوثيرابي", "alias": "ميزو ثيرابي"},
    {"string_to_replace": "إكسوسوم", "alias": "إكسو سوم"},
    {"string_to_replace": "إندوليفت", "alias": "إندو ليفت"},
    {"string_to_replace": "فوتونا", "alias": "فُوتُونا"},

    # ---- Doctor names — ensure correct vowels (Lavora team) ----
    {"string_to_replace": "Dr. Soraya", "alias": "Doctor Soraya"},
    {"string_to_replace": "Dr. Neda", "alias": "Doctor Neda"},
    {"string_to_replace": "Dr. Hussein", "alias": "Doctor Hussein"},
    {"string_to_replace": "Dr. Amani", "alias": "Doctor Amani"},
    {"string_to_replace": "Dr. Leila", "alias": "Doctor Leila"},
    {"string_to_replace": "الدكتورة ثريا", "alias": "الدكتورة ثُرَيَّا"},
    {"string_to_replace": "الدكتورة ندى", "alias": "الدكتورة نَدَى"},
    {"string_to_replace": "الدكتور حسين", "alias": "الدكتور حُسَين"},
    {"string_to_replace": "الدكتورة أماني", "alias": "الدكتورة أَماني"},
    {"string_to_replace": "الدكتورة ليلى", "alias": "الدكتورة لَيلى"},

    # ---- Location ----
    {"string_to_replace": "الغبرة", "alias": "الغُبرَة"},
    {"string_to_replace": "Al Ghubra", "alias": "Al Ghubra"},
    {"string_to_replace": "Al Ghubrah", "alias": "Al Ghubra"},
    {"string_to_replace": "Al Marafah", "alias": "Al Marafa"},
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
