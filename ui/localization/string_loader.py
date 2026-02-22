import json
import os

# Global variable to store current strings
_current_strings = {}
_current_language = "en"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_language(lang_code: str):
    """
    Load the JSON file for the selected language.
    This allows CI/CD to replace JSON files without code changes.
    """
    global _current_strings, _current_language

    file_path = os.path.join(BASE_DIR, f"{lang_code}.json")

    with open(file_path, "r", encoding="utf-8") as f:
        _current_strings = json.load(f)

    _current_language = lang_code


def get_string(key: str) -> str:
    """
    Fetch string by key.
    Handles both plain strings (en.json) and {source, translation} entries (hi.json).
    Fallback to key name if missing.
    """
    value = _current_strings.get(key, f"[{key}]")
    if isinstance(value, dict):
        # hi.json format: {"source": "...", "translation": "..."}
        return value.get("translation") or value.get("source") or f"[{key}]"
    return str(value)


def current_language():
    return _current_language