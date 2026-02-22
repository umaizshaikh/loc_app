"""
Lightweight agent-based localization pipeline.
Preserves: incremental detection, retry logic, confidence gating, QA report.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------------
# Configuration (only global state)
# ---------------------------------------------------------------------------
API_URL = os.getenv("LOCALIZATION_API_URL", "http://127.0.0.1:8000/translate")
EVALUATE_URL = os.getenv("LOCALIZATION_API_URL", "http://127.0.0.1:8000").rstrip("/") + "/evaluate"
LOCALIZATION_DIR = "ui/localization"
EN_PATH = "ui/localization/en.json"
HI_PATH = "ui/localization/hi.json"
GLOSSARY_PATH = os.path.join("ui", "localization", "glossary.json")
QA_REPORT_DIR = "ui/localization"
QA_REPORT_PATH = "ui/localization/qa_report.json"
METRICS_HISTORY_PATH = "ui/localization/metrics_history.json"
CACHE_PATH = "localization/translation_cache.json"
MAX_METRICS_HISTORY_ENTRIES = 50
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.95"))
QUALITY_THRESHOLD = float(os.getenv("QUALITY_THRESHOLD", "0.90"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_REFLECTION_MODEL", "gemini-1.5-flash")


def load_json_safe(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def discover_source_files():
    """Discover all en*.json files in localization directory. Returns sorted list."""
    if not os.path.exists(LOCALIZATION_DIR):
        return []
    files = []
    for filename in os.listdir(LOCALIZATION_DIR):
        if filename.startswith("en") and filename.endswith(".json"):
            files.append(os.path.join(LOCALIZATION_DIR, filename))
    return sorted(files)


def discover_target_files():
    """Discover all hi*.json files in localization directory. Returns sorted list."""
    if not os.path.exists(LOCALIZATION_DIR):
        return []
    files = []
    for filename in os.listdir(LOCALIZATION_DIR):
        if filename.startswith("hi") and filename.endswith(".json"):
            files.append(os.path.join(LOCALIZATION_DIR, filename))
    return sorted(files)


def map_to_target_file(source_file):
    """Map en*.json to corresponding hi*.json."""
    basename = os.path.basename(source_file)
    target_basename = basename.replace("en", "hi", 1)
    return os.path.join(LOCALIZATION_DIR, target_basename)


def bootstrap_cache_from_existing_files(translation_cache):
    """Populate cache from existing hi*.json files. Returns count of entries added."""
    target_files = discover_target_files()
    entries_added = 0

    for target_file in target_files:
        hi_data = load_json_safe(target_file)
        for key, entry in hi_data.items():
            if not isinstance(entry, dict):
                continue
            source_text = entry.get("source", "").strip()
            translated_text = entry.get("translation", "")
            if source_text and translated_text:
                # Only add if not already in cache (don't overwrite existing entries)
                if source_text not in translation_cache:
                    translation_cache[source_text] = {
                        "translation": translated_text,
                        "confidence": 1.0,
                        "quality_score": 1.0,
                    }
                    entries_added += 1

    return entries_added


def load_translation_cache():
    """Load persistent translation cache. Returns empty dict if file doesn't exist."""
    return load_json_safe(CACHE_PATH)


def load_glossary():
    """Load glossary overrides. Returns empty dict if file doesn't exist."""
    return load_json_safe(GLOSSARY_PATH)


def save_translation_cache(cache):
    """Save translation cache to disk."""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def append_metrics_history(metrics_entry):
    """Append one run to metrics_history.json. Keep max MAX_METRICS_HISTORY_ENTRIES."""
    history = load_json_safe(METRICS_HISTORY_PATH)
    if not isinstance(history, list):
        history = []
    history.append(metrics_entry)
    if len(history) > MAX_METRICS_HISTORY_ENTRIES:
        history = history[-MAX_METRICS_HISTORY_ENTRIES:]
    os.makedirs(QA_REPORT_DIR, exist_ok=True)
    with open(METRICS_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)
    print("[METRICS] Appended run to metrics_history.json")


def contains_transliteration(text: str) -> bool:
    """
    Returns True if text likely contains English transliteration.
    - Contains any ASCII letter → reject.
    - Contains common Devanagari transliteration patterns → reject.
    """
    if not text or not isinstance(text, str):
        return False
    # Any ASCII letter → transliteration
    for ch in text:
        if ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
            return True
    # Common Devanagari transliterations of English words (phonetic borrowings)
    transliteration_patterns = (
        "सबमिट", "डेमो", "फॉर्म", "सब्मिट", "सेव", "एक्सिट", "ओपन",
        "क्लिक", "लॉगिन", "साइन", "सेटिंग", "मेन्यू", "फाइल", "हेल्प",
        "अबाउट", "क्लोज", "कैन्सल", "अड्ड", "एडिट", "डिलीट", "रीसेट",
    )
    text_lower = text.strip()
    for pattern in transliteration_patterns:
        if pattern in text_lower:
            return True
    return False


def _translate_api(text):
    payload = {
        "source_text": text,
        "target_language": "Hindi",
        "content_type": "ui",
        "product_category": "loc_app",
    }
    response = requests.post(API_URL, json=payload)
    if response.status_code != 200:
        raise Exception(f"API Error: {response.text}")
    return response.json()


def _call_gemini(prompt):
    """Call Gemini API for reflection. Returns (response, data) for consistent extraction."""
    if not GEMINI_API_KEY:
        raise Exception("GEMINI_API_KEY is not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    headers = {"Content-Type": "application/json"}
    params = {"key": GEMINI_API_KEY}
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    response = requests.post(url, json=body, headers=headers, params=params, timeout=60)
    if response.status_code != 200:
        raise Exception(f"Gemini API Error: {response.text}")
    data = response.json()
    return response, data


def _gemini_text_from_data(data):
    """Extract LLM text from Gemini API response shape. Returns empty string if missing."""
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts") or []
    if not parts:
        return ""
    return (parts[0].get("text") or "").strip()


def extract_json_from_text(text: str):
    """
    Extract and parse JSON from LLM response. Handles markdown fences, extra text, trailing commas.
    Returns (dict or None, extracted_substring) for debugging.
    """
    if not text or not isinstance(text, str):
        return None, ""
    text = text.strip()

    # If ```json or ``` fenced block present, extract content inside
    if "```" in text:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            text = match.group(1).strip()

    # Otherwise or to normalize: first "{" to last "}"
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    extracted_substring = text

    def try_parse(s):
        try:
            return json.loads(s)
        except (json.JSONDecodeError, ValueError):
            return None

    parsed = try_parse(text)
    if parsed is not None:
        return parsed, extracted_substring

    # Clean and retry: trailing commas, strip backticks/code markers
    cleaned = text.strip()
    cleaned = re.sub(r",\s*}", "}", cleaned)
    cleaned = re.sub(r",\s*]", "]", cleaned)
    cleaned = cleaned.strip().strip("`").strip()
    if cleaned.lower().startswith("json"):
        cleaned = cleaned[4:].lstrip()
    parsed = try_parse(cleaned)
    if parsed is not None:
        return parsed, extracted_substring

    return None, extracted_substring


# ---------------------------------------------------------------------------
# 1. ChangeDetectorAgent
# ---------------------------------------------------------------------------
class ChangeDetectorAgent:
    """Loads source/target JSON and detects new or changed keys. Tracks reused strings."""

    def __init__(self, en_path=None, hi_path=None):
        self.en_path = en_path
        self.hi_path = hi_path

    def detect(self, en_path=None, hi_path=None):
        """Detect changes for a specific file pair. If paths provided, use them; else use instance paths."""
        en_path = en_path or self.en_path
        hi_path = hi_path or self.hi_path
        en_strings = load_json_safe(en_path)
        existing_hi = load_json_safe(hi_path)

        changes = {}
        existing_translations = {}
        strings_reused = 0

        for key, en_value in en_strings.items():
            existing_entry = existing_hi.get(key)
            if existing_entry and existing_entry.get("source") == en_value:
                existing_translations[key] = existing_entry
                strings_reused += 1
                print(f"[DETECTOR] Reused: {key}")
            else:
                changes[key] = en_value

        total_strings_in_source = len(en_strings)
        new_or_changed_strings = len(changes)

        return {
            "changes": changes,
            "existing_translations": existing_translations,
            "stats": {
                "total_strings_in_source": total_strings_in_source,
                "strings_reused": strings_reused,
                "new_or_changed_strings": new_or_changed_strings,
            },
        }


# ---------------------------------------------------------------------------
# 2. TranslationAgent
# ---------------------------------------------------------------------------
class TranslationAgent:
    """Translates changed strings with retry-once logic. Tracks API calls and confidence."""

    def __init__(self, threshold=CONFIDENCE_THRESHOLD, translation_cache=None, glossary=None):
        self.threshold = threshold
        self.translation_cache = translation_cache or {}
        self.glossary = glossary or {}

    def _translate_one(self, key, source_text):
        """One key: translate with at most one retry. Returns (entry, confidence, below_threshold, api_calls, retried)."""
        result = _translate_api(source_text)
        api_calls = 1
        raw_confidence = result["confidence_score"]
        # Normalize confidence from 0-100 to 0-1 if needed
        confidence = float(raw_confidence)
        if confidence > 1:
            confidence = confidence / 100.0
        first_confidence = confidence

        print(f"[TRANSLATOR] Translating key: {key}")
        print(f"[TRANSLATOR] Raw confidence: {raw_confidence}")
        print(f"[TRANSLATOR] Normalized confidence: {first_confidence:.2f}")

        if first_confidence >= self.threshold:
            print(f"[TRANSLATOR] Confidence {first_confidence:.2f} – accepted")
            entry = {"source": source_text, "translation": result["translation"]}
            return entry, first_confidence, False, api_calls, False

        print(f"[TRANSLATOR] Confidence {first_confidence:.2f} below threshold {self.threshold} – retrying...")
        result2 = _translate_api(source_text)
        api_calls = 2
        raw_retry_confidence = result2["confidence_score"]
        retry_confidence = float(raw_retry_confidence)
        if retry_confidence > 1:
            retry_confidence = retry_confidence / 100.0

        if retry_confidence >= self.threshold:
            print(f"[TRANSLATOR] Retry confidence: {retry_confidence:.2f} – accepted")
            entry = {"source": source_text, "translation": result2["translation"]}
            return entry, retry_confidence, False, api_calls, True

        print(f"[TRANSLATOR] Retry confidence {retry_confidence:.2f} – still below threshold.")
        entry = {"source": source_text, "translation": result2["translation"]}
        return entry, retry_confidence, True, api_calls, True

    def process(self, changes):
        translations = {}
        total_api_calls = 0
        retries_performed = 0
        accepted_confidences = []
        low_confidence_items = []
        cache_hits = 0
        cache_misses = 0
        glossary_hits = 0

        for key, source_text in changes.items():
            source_text_clean = source_text.strip()

            # Glossary check FIRST (before cache)
            if source_text_clean in self.glossary:
                glossary_translation = self.glossary[source_text_clean]
                if not isinstance(glossary_translation, str):
                    glossary_translation = str(glossary_translation or "").strip()
                if glossary_translation:
                    print(f"[GLOSSARY] Override applied for key: {key}")
                    entry = {"source": source_text_clean, "translation": glossary_translation}
                    translations[key] = {
                        "entry": entry,
                        "confidence": 1.0,
                        "below_threshold": False,
                        "quality_score": 1.0,
                        "issues": "",
                        "suggested_improvement": "",
                        "from_cache": False,
                        "from_glossary": True,
                    }
                    self.translation_cache[source_text_clean] = {
                        "translation": glossary_translation,
                        "confidence": 1.0,
                        "quality_score": 1.0,
                    }
                    glossary_hits += 1
                    accepted_confidences.append(1.0)
                    continue
                print(f"[GLOSSARY] No override for key: {key} (empty translation)")

            # Check cache (glossary has priority)
            if source_text_clean in self.translation_cache:
                cached = self.translation_cache[source_text_clean]
                print(f"[CACHE] Reusing cached translation for key: {key}")
                entry = {"source": source_text_clean, "translation": cached["translation"]}
                confidence = cached["confidence"]
                quality_score = cached.get("quality_score", 1.0)
                translations[key] = {
                    "entry": entry,
                    "confidence": confidence,
                    "below_threshold": False,
                    "quality_score": quality_score,
                    "issues": "",
                    "suggested_improvement": "",
                    "from_cache": True,
                    "from_glossary": False,
                }
                cache_hits += 1
                if confidence >= self.threshold:
                    accepted_confidences.append(confidence)
            else:
                # Normal translation path
                entry, confidence, below_threshold, api_calls, retried = self._translate_one(key, source_text)
                translations[key] = {
                    "entry": entry,
                    "confidence": confidence,
                    "below_threshold": below_threshold,
                    "from_cache": False,
                    "from_glossary": False,
                }
                total_api_calls += api_calls
                if retried:
                    retries_performed += 1
                if not below_threshold:
                    accepted_confidences.append(confidence)
                if below_threshold:
                    low_confidence_items.append({
                        "key": key,
                        "source": source_text,
                        "translation": entry["translation"],
                        "confidence": confidence,
                    })
                cache_misses += 1

        return {
            "translations": translations,
            "stats": {
                "total_api_calls": total_api_calls,
                "retries_performed": retries_performed,
                "accepted_confidences": accepted_confidences,
                "low_confidence_items": low_confidence_items,
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
                "glossary_hits": glossary_hits,
            },
        }


# ---------------------------------------------------------------------------
# 3. ReflectionAgent
# ---------------------------------------------------------------------------
REFLECTION_PROMPT_TEMPLATE = '''Evaluate the quality of the following UI translation.

Source (English):
"{source_text}"

Translation (Hindi):
"{translated_text}"

Evaluate:
- Does it preserve meaning?
- Is it natural and clear?
- Is it appropriate for UI context?

Return ONLY valid JSON in this format:
{{
  "quality_score": float (0.0–1.0),
  "issues": "brief explanation",
  "suggested_improvement": "if needed, otherwise empty string"
}}'''


class ReflectionAgent:
    """Evaluates newly generated translations via Gemini. Attaches quality_score, issues, suggested_improvement."""

    def __init__(self, api_key=None):
        self.api_key = api_key or GEMINI_API_KEY

    def _evaluate_one(self, key, source_text, translated_text):
        """Call /evaluate endpoint and parse JSON. Extract LLM output from data['response']. On parse failure use quality_score=0.5."""
        prompt = REFLECTION_PROMPT_TEMPLATE.format(
            source_text=source_text.replace('"', '\\"'),
            translated_text=translated_text.replace('"', '\\"'),
        )
        try:
            response = requests.post(EVALUATE_URL, json={"prompt": prompt}, timeout=60)
            if response.status_code != 200:
                raise Exception(f"Evaluate API Error: {response.text}")
            data = response.json()
        except Exception:
            return {
                "quality_score": 0.5,
                "issues": "Reflection parsing failed",
                "suggested_improvement": "",
            }

        # Extract actual LLM string from /evaluate response
        reflection_text = data.get("response") or ""
        reflection_text = (reflection_text or "").strip() if isinstance(reflection_text, str) else ""

        print("[REFLECTION] Raw LLM response (first 300 chars):")
        print(reflection_text[:300])

        parsed, extracted_substring = extract_json_from_text(reflection_text)
        print("[REFLECTION] Extracted JSON substring:")
        print(extracted_substring)

        if parsed is None:
            print("[REFLECTION] JSON parsing failed.")
            return {
                "quality_score": 0.5,
                "issues": "Reflection parsing failed",
                "suggested_improvement": "",
            }

        try:
            quality_score = float(parsed.get("quality_score", 0.0))
            quality_score = max(0.0, min(1.0, quality_score))
        except (TypeError, ValueError):
            quality_score = 0.5
        return {
            "quality_score": quality_score,
            "issues": str(parsed.get("issues", "") or ""),
            "suggested_improvement": str(parsed.get("suggested_improvement", "") or ""),
        }

    def evaluate(self, translation_result):
        """Run reflection on each newly translated key. Skip cached items. Enrich and return result + total_reflection_calls."""
        translations = translation_result["translations"]
        trans_stats = translation_result["stats"]
        total_reflection_calls = 0
        enriched = {}

        for key, data in translations.items():
            entry = data["entry"]
            source_text = entry["source"]
            translated_text = entry["translation"]

            # Skip reflection for cached or glossary items (already have quality_score)
            if data.get("from_cache") or data.get("from_glossary"):
                enriched[key] = {
                    "entry": entry,
                    "confidence": data["confidence"],
                    "below_threshold": data["below_threshold"],
                    "quality_score": data.get("quality_score", 1.0),
                    "issues": data.get("issues", ""),
                    "suggested_improvement": data.get("suggested_improvement", ""),
                    "from_cache": True,
                    "from_glossary": data.get("from_glossary", False),
                }
                continue

            print(f"[REFLECTION] Evaluating key: {key}")
            reflection = self._evaluate_one(key, source_text, translated_text)
            total_reflection_calls += 1

            quality_score = reflection["quality_score"]
            print(f"[REFLECTION] Quality score: {quality_score:.2f}")

            enriched[key] = {
                "entry": entry,
                "confidence": data["confidence"],
                "below_threshold": data["below_threshold"],
                "quality_score": quality_score,
                "issues": reflection["issues"],
                "suggested_improvement": reflection["suggested_improvement"],
                "from_cache": False,
                "from_glossary": False,
            }

        return {
            "translations": enriched,
            "stats": {
                **trans_stats,
                "total_reflection_calls": total_reflection_calls,
            },
        }


# ---------------------------------------------------------------------------
# 4. ImprovementAgent
# ---------------------------------------------------------------------------
IMPROVEMENT_PROMPT_TEMPLATE = '''The following Hindi UI translation needs improvement.

Source (English):
"{source_text}"

Current Translation:
"{translated_text}"

Issues identified:
"{issues_from_reflection}"

Provide an improved Hindi translation.
Use PURE Hindi vocabulary.
Do NOT transliterate English words.
Avoid direct phonetic borrowings like 'सबमिट' or 'डेमो'.
- Preserve original meaning
- Be natural for UI usage
- Be concise and clear

Return ONLY valid JSON:

{{
  "improved_translation": "string"
}}'''


class ImprovementAgent:
    """One self-improvement pass for translations that pass confidence but fail quality."""

    def __init__(self, reflection_agent, confidence_threshold=CONFIDENCE_THRESHOLD, quality_threshold=QUALITY_THRESHOLD):
        self.reflection_agent = reflection_agent
        self.confidence_threshold = confidence_threshold
        self.quality_threshold = quality_threshold

    def _request_improvement(self, source_text, translated_text, issues):
        """Call Gemini for improved translation. Returns improved text or None on parse failure."""
        prompt = IMPROVEMENT_PROMPT_TEMPLATE.format(
            source_text=source_text.replace('"', '\\"'),
            translated_text=translated_text.replace('"', '\\"'),
            issues_from_reflection=(issues or "").replace('"', '\\"'),
        )
        try:
            _resp, data = _call_gemini(prompt)
            text = data.get("translation") or data.get("response") or _gemini_text_from_data(data)
            text = (text or "").strip() if isinstance(text, str) else ""
            if "```" in text:
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    text = text[start:end]
            parsed = json.loads(text)
            return (parsed.get("improved_translation") or "").strip() or None
        except (json.JSONDecodeError, ValueError, Exception):
            return None

    def improve(self, reflection_result):
        """Improve only keys where confidence >= threshold and quality < quality_threshold. Re-run reflection on improved."""
        translations = reflection_result["translations"]
        trans_stats = reflection_result["stats"]
        total_improvement_attempts = 0
        re_reflection_calls = 0
        result_translations = {}
        policy_logged = False

        for key, data in translations.items():
            # Skip cached or glossary items (already validated and passed)
            if data.get("from_cache") or data.get("from_glossary"):
                result_translations[key] = data
                continue

            confidence = data["confidence"]
            quality_score = data.get("quality_score", 0.0)
            entry = data["entry"]
            source_text = entry["source"]
            translated_text = entry["translation"]
            issues = data.get("issues", "")

            eligible = confidence >= self.confidence_threshold and quality_score < self.quality_threshold
            was_improved = False
            out_entry = dict(entry)
            out_quality = quality_score
            out_issues = issues
            out_suggested = data.get("suggested_improvement", "")

            if eligible:
                if not policy_logged:
                    print(f"[POLICY] Pure Hindi enforcement active.")
                    policy_logged = True
                print(f"[IMPROVEMENT] Attempting improvement for key: {key}")
                improved_text = self._request_improvement(source_text, translated_text, issues)
                if improved_text:
                    if contains_transliteration(improved_text):
                        print(f"[IMPROVEMENT] Rejected transliteration-based suggestion.")
                        print(f"[POLICY] Transliteration detected in improvement suggestion.")
                    else:
                        out_entry["translation"] = improved_text
                        was_improved = True
                        total_improvement_attempts += 1
                        print(f"[IMPROVEMENT] Improvement applied.")
                        refl = self.reflection_agent._evaluate_one(key, source_text, improved_text)
                        re_reflection_calls += 1
                        out_quality = refl["quality_score"]
                        out_issues = refl["issues"]
                        out_suggested = refl["suggested_improvement"]
                        print(f"[IMPROVEMENT] New quality score: {out_quality:.2f}")

            result_translations[key] = {
                "entry": out_entry,
                "confidence": confidence,
                "below_threshold": data["below_threshold"],
                "quality_score": out_quality,
                "issues": out_issues,
                "suggested_improvement": out_suggested,
                "was_improved": was_improved,
            }

        total_reflection_calls = trans_stats.get("total_reflection_calls", 0) + re_reflection_calls

        return {
            "translations": result_translations,
            "stats": {
                **trans_stats,
                "total_reflection_calls": total_reflection_calls,
                "total_improvement_attempts": total_improvement_attempts,
            },
        }


# ---------------------------------------------------------------------------
# 5. ValidationAgent
# ---------------------------------------------------------------------------
class ValidationAgent:
    """Evaluates translation results against confidence and quality thresholds. Does not re-translate."""

    def __init__(self, confidence_threshold=CONFIDENCE_THRESHOLD, quality_threshold=QUALITY_THRESHOLD, translation_cache=None):
        self.confidence_threshold = confidence_threshold
        self.quality_threshold = quality_threshold
        self.translation_cache = translation_cache or {}

    def validate(self, improvement_result):
        translations = improvement_result["translations"]
        stats = improvement_result["stats"]

        low_confidence_items = []
        accepted_confidences = []
        quality_scores = []

        for key, data in translations.items():
            confidence = data["confidence"]  # Already normalized (0-1)
            quality_score = data.get("quality_score", 0.0)
            entry = data["entry"]
            source = entry["source"]
            translation = entry["translation"]

            # Track metrics for ALL translated keys (not just accepted)
            quality_scores.append(quality_score)

            fails_confidence = confidence < self.confidence_threshold
            fails_quality = quality_score < self.quality_threshold

            if fails_confidence or fails_quality:
                if fails_quality and not fails_confidence:
                    print(f"[VALIDATOR] Key {key} failed quality threshold ({quality_score:.2f} < {self.quality_threshold})")
                if fails_quality and contains_transliteration(translation):
                    print(f"[POLICY] Transliteration detected in translation.")
                low_confidence_items.append({
                    "key": key,
                    "source": source,
                    "translation": translation,
                    "confidence": confidence,
                    "quality_score": quality_score,
                    "issues": data.get("issues", ""),
                    "suggested_improvement": data.get("suggested_improvement", ""),
                    "was_improved": data.get("was_improved", False),
                })
            else:
                accepted_confidences.append(confidence)
                print(f"[VALIDATOR] Key {key} passed.")
                # Store validated translation in cache (glossary already stored by TranslationAgent)
                if not data.get("from_cache") and not data.get("from_glossary"):
                    self.translation_cache[source] = {
                        "translation": translation,
                        "confidence": confidence,
                        "quality_score": quality_score,
                    }

        # Compute averages AFTER processing all keys
        average_confidence = (sum(accepted_confidences) / len(accepted_confidences)) if accepted_confidences else 0.0
        average_quality_score = (sum(quality_scores) / len(quality_scores)) if quality_scores else 0.0
        low_confidence_count = len(low_confidence_items)
        status = "FAILED" if low_confidence_items else "PASSED"

        if status == "FAILED":
            print(f"[VALIDATOR] Build FAILED – {low_confidence_count} item(s) below threshold.")
        else:
            print(f"[VALIDATOR] Build PASSED – all translations meet threshold.")

        return {
            "status": status,
            "metrics": {
                "average_confidence": average_confidence,
                "average_quality_score": average_quality_score,
                "low_confidence_count": low_confidence_count,
                "low_confidence_items": low_confidence_items,
                "total_reflection_calls": stats.get("total_reflection_calls", 0),
                "total_improvement_attempts": stats.get("total_improvement_attempts", 0),
                "cache_hits": stats.get("cache_hits", 0),
                "cache_misses": stats.get("cache_misses", 0),
                "glossary_hits": stats.get("glossary_hits", 0),
            },
        }


# ---------------------------------------------------------------------------
# 6. ReportAgent
# ---------------------------------------------------------------------------
class ReportAgent:
    """Generates ui/localization/qa_report.json and prints CI summary block."""

    def __init__(self, report_dir=QA_REPORT_DIR, report_path=QA_REPORT_PATH):
        self.report_dir = report_dir
        self.report_path = report_path

    def generate(self, detector_result, improvement_result, validation_result, execution_time_seconds, processed_files=None):
        det_stats = detector_result["stats"]
        impr_stats = improvement_result["stats"]
        metrics = validation_result["metrics"]
        status = validation_result["status"]
        processed_files = processed_files or []

        low_items_payload = []
        for i in metrics["low_confidence_items"]:
            item = {"key": i["key"], "source": i["source"], "translation": i["translation"], "confidence": float(i["confidence"])}
            if "quality_score" in i:
                item["quality_score"] = float(i["quality_score"])
            if i.get("issues"):
                item["issues"] = i["issues"]
            if i.get("suggested_improvement"):
                item["suggested_improvement"] = i["suggested_improvement"]
            if "was_improved" in i:
                item["was_improved"] = bool(i["was_improved"])
            low_items_payload.append(item)

        report = {
            "threshold": CONFIDENCE_THRESHOLD,
            "quality_threshold": QUALITY_THRESHOLD,
            "processed_files": processed_files,
            "total_strings_in_source": det_stats["total_strings_in_source"],
            "strings_reused": det_stats["strings_reused"],
            "new_or_changed_strings": det_stats["new_or_changed_strings"],
            "glossary_hits": metrics.get("glossary_hits", 0),
            "cache_hits": metrics.get("cache_hits", 0),
            "cache_misses": metrics.get("cache_misses", 0),
            "total_api_calls": impr_stats["total_api_calls"],
            "retries_performed": impr_stats["retries_performed"],
            "total_reflection_calls": metrics.get("total_reflection_calls", 0),
            "total_improvement_attempts": metrics.get("total_improvement_attempts", 0),
            "average_confidence": round(metrics["average_confidence"], 2),
            "average_quality_score": round(metrics.get("average_quality_score", 0.0), 2),
            "low_confidence_count": metrics["low_confidence_count"],
            "low_confidence_items": low_items_payload,
            "execution_time_seconds": round(execution_time_seconds, 2),
            "status": status,
        }

        os.makedirs(self.report_dir, exist_ok=True)
        with open(self.report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        print(f"[REPORT] QA report generated at {self.report_path}")

        print("----------------------------------------")
        print("Localization QA Summary")
        print("----------------------------------------")
        print(f"Threshold: {CONFIDENCE_THRESHOLD}")
        print(f"Quality Threshold: {QUALITY_THRESHOLD}")
        print(f"Glossary Hits: {metrics.get('glossary_hits', 0)}")
        print(f"Cache Hits: {metrics.get('cache_hits', 0)}")
        print(f"Cache Misses: {metrics.get('cache_misses', 0)}")
        print(f"Average Confidence: {metrics['average_confidence']:.2f}")
        print(f"Average Quality Score: {metrics.get('average_quality_score', 0):.2f}")
        print(f"API Calls: {impr_stats['total_api_calls']}")
        print(f"Retries: {impr_stats['retries_performed']}")
        print(f"Reflection Calls: {metrics.get('total_reflection_calls', 0)}")
        print(f"Improvement Attempts: {metrics.get('total_improvement_attempts', 0)}")
        print(f"Low Confidence Items: {metrics['low_confidence_count']}")
        print(f"Status: {status}")
        print("----------------------------------------")


# ---------------------------------------------------------------------------
# 7. LocalizationOrchestrator
# ---------------------------------------------------------------------------
class LocalizationOrchestrator:
    """Runs the pipeline: detect -> translate -> reflection -> improvement -> validate -> report -> merge & write -> exit."""

    def __init__(self):
        self.translation_cache = load_translation_cache()
        self.glossary = load_glossary()
        self.detector = ChangeDetectorAgent(en_path=EN_PATH, hi_path=HI_PATH)  # Defaults for backward compat
        self.translator = TranslationAgent(translation_cache=self.translation_cache, glossary=self.glossary)
        self.reflection = ReflectionAgent()
        self.improvement = ImprovementAgent(reflection_agent=self.reflection)
        self.validator = ValidationAgent(translation_cache=self.translation_cache)
        self.reporter = ReportAgent()

    def run(self):
        start_time = time.time()

        print("\n[ORCHESTRATOR] Starting incremental localization with change detection...\n")

        # Log glossary loaded
        print(f"[GLOSSARY] Loaded entries: {len(self.glossary)}")

        # Bootstrap cache from existing hi*.json files
        entries_added = bootstrap_cache_from_existing_files(self.translation_cache)
        if entries_added > 0:
            print(f"[CACHE] Bootstrapped from existing hi files.")
            print(f"[CACHE] Initial entries loaded: {entries_added}")

        # Discover all en*.json files
        source_files = discover_source_files()
        if not source_files:
            print("[ORCHESTRATOR] No en*.json files found in localization directory.")
            return

        # Aggregate metrics across all files
        all_detector_stats = {
            "total_strings_in_source": 0,
            "strings_reused": 0,
            "new_or_changed_strings": 0,
        }
        all_translation_stats = {
            "total_api_calls": 0,
            "retries_performed": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "glossary_hits": 0,
        }
        all_reflection_stats = {"total_reflection_calls": 0}
        all_improvement_stats = {"total_improvement_attempts": 0}
        all_validation_metrics = {
            "average_confidence": 0.0,
            "average_quality_score": 0.0,
            "low_confidence_count": 0,
            "low_confidence_items": [],
            "total_reflection_calls": 0,
            "total_improvement_attempts": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "glossary_hits": 0,
        }
        processed_files = []
        all_confidences = []
        all_quality_scores = []
        overall_status = "PASSED"

        # Process each file
        for source_file in source_files:
            target_file = map_to_target_file(source_file)
            source_basename = os.path.basename(source_file)
            target_basename = os.path.basename(target_file)

            print(f"[ORCHESTRATOR] Processing file: {source_basename}")
            print(f"[ORCHESTRATOR] Target file: {target_basename}")

            # Detect changes for this file
            detector_result = self.detector.detect(en_path=source_file, hi_path=target_file)
            
            # Aggregate detector stats
            all_detector_stats["total_strings_in_source"] += detector_result["stats"]["total_strings_in_source"]
            all_detector_stats["strings_reused"] += detector_result["stats"]["strings_reused"]
            all_detector_stats["new_or_changed_strings"] += detector_result["stats"]["new_or_changed_strings"]

            # Process translation pipeline
            translation_result = self.translator.process(detector_result["changes"])
            reflection_result = self.reflection.evaluate(translation_result)
            improvement_result = self.improvement.improve(reflection_result)
            validation_result = self.validator.validate(improvement_result)

            # Aggregate stats
            all_translation_stats["total_api_calls"] += translation_result["stats"]["total_api_calls"]
            all_translation_stats["retries_performed"] += translation_result["stats"]["retries_performed"]
            all_translation_stats["cache_hits"] += translation_result["stats"].get("cache_hits", 0)
            all_translation_stats["cache_misses"] += translation_result["stats"].get("cache_misses", 0)
            all_translation_stats["glossary_hits"] += translation_result["stats"].get("glossary_hits", 0)
            all_reflection_stats["total_reflection_calls"] += reflection_result["stats"].get("total_reflection_calls", 0)
            all_improvement_stats["total_improvement_attempts"] += improvement_result["stats"].get("total_improvement_attempts", 0)
            
            # Collect confidences and quality scores for averaging
            for key, data in improvement_result["translations"].items():
                all_confidences.append(data["confidence"])
                all_quality_scores.append(data.get("quality_score", 0.0))

            # Aggregate validation metrics
            metrics = validation_result["metrics"]
            all_validation_metrics["low_confidence_count"] += metrics["low_confidence_count"]
            all_validation_metrics["low_confidence_items"].extend(metrics["low_confidence_items"])
            if validation_result["status"] == "FAILED":
                overall_status = "FAILED"

            processed_files.append(source_basename)

            # Merge reused + new translations and write target file
            final_hi = dict(detector_result["existing_translations"])
            for key, data in improvement_result["translations"].items():
                final_hi[key] = data["entry"]

            # Ensure directory exists before writing
            os.makedirs(LOCALIZATION_DIR, exist_ok=True)
            
            # Always save target file (even if all keys were reused)
            with open(target_file, "w", encoding="utf-8") as f:
                json.dump(final_hi, f, ensure_ascii=False, indent=2)
            
            print(f"[ORCHESTRATOR] Saved file: {target_file}")
            print(f"[ORCHESTRATOR] Completed processing {source_basename}\n")

        # Compute final averages
        if all_confidences:
            all_validation_metrics["average_confidence"] = sum(all_confidences) / len(all_confidences)
        if all_quality_scores:
            all_validation_metrics["average_quality_score"] = sum(all_quality_scores) / len(all_quality_scores)

        # Update aggregated metrics
        all_validation_metrics["total_reflection_calls"] = all_reflection_stats["total_reflection_calls"]
        all_validation_metrics["total_improvement_attempts"] = all_improvement_stats["total_improvement_attempts"]
        all_validation_metrics["cache_hits"] = all_translation_stats["cache_hits"]
        all_validation_metrics["cache_misses"] = all_translation_stats["cache_misses"]
        all_validation_metrics["glossary_hits"] = all_translation_stats["glossary_hits"]

        # Final aggregated variables for metrics history
        final_status = overall_status
        total_api_calls = all_translation_stats["total_api_calls"]
        total_cache_hits = all_translation_stats["cache_hits"]
        total_glossary_hits = all_translation_stats["glossary_hits"]
        total_reflection_calls = all_reflection_stats["total_reflection_calls"]
        average_confidence = (
            sum(all_confidences) / len(all_confidences)
            if all_confidences else 0.0
        )
        average_quality = (
            sum(all_quality_scores) / len(all_quality_scores)
            if all_quality_scores else 0.0
        )

        # Create aggregated results for reporting
        aggregated_detector_result = {"stats": all_detector_stats}
        aggregated_improvement_result = {"stats": all_translation_stats}
        aggregated_validation_result = {
            "status": overall_status,
            "metrics": all_validation_metrics,
        }

        elapsed = time.time() - start_time
        self.reporter.generate(
            aggregated_detector_result,
            aggregated_improvement_result,
            aggregated_validation_result,
            elapsed,
            processed_files=processed_files,
        )

        # Append metrics history after report (using final aggregated values)
        metrics_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "status": final_status,
            "average_confidence": round(average_confidence, 2),
            "average_quality": round(average_quality, 2),
            "api_calls": total_api_calls,
            "cache_hits": total_cache_hits,
            "glossary_hits": total_glossary_hits,
            "reflection_calls": total_reflection_calls,
        }
        append_metrics_history(metrics_entry)
        print("[METRICS DEBUG]", metrics_entry)

        if overall_status == "FAILED":
            low_confidence_items = all_validation_metrics["low_confidence_items"]
            print("\n[ORCHESTRATOR] --- Low confidence summary ---")
            for item in low_confidence_items:
                print(f"  Key: {item['key']}")
                print(f"  Source: {item['source']}")
                print(f"  Final Translation: {item['translation']}")
                print(f"  Final Confidence: {item['confidence']:.2f}")
                if "quality_score" in item:
                    print(f"  Quality Score: {item['quality_score']:.2f}")
                print()
            print(f"[ORCHESTRATOR] Build failed due to translations below {CONFIDENCE_THRESHOLD} confidence or {QUALITY_THRESHOLD} quality threshold.")
            sys.exit(1)

        print("\n[ORCHESTRATOR] All translations meet 95% confidence threshold.")
        print("[ORCHESTRATOR] Localization complete.")

        # Save updated cache
        save_translation_cache(self.translation_cache)
        print(f"[ORCHESTRATOR] Translation cache saved to {CACHE_PATH}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    orchestrator = LocalizationOrchestrator()
    orchestrator.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[ORCHESTRATOR] Localization failed: {e}")
        sys.exit(1)
