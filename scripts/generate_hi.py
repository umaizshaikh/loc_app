"""
Lightweight agent-based localization pipeline.
Preserves: incremental detection, retry logic, confidence gating, QA report.
"""

import json
import os
import re
import sys
import time

import requests

# ---------------------------------------------------------------------------
# Configuration (only global state)
# ---------------------------------------------------------------------------
API_URL = os.getenv("LOCALIZATION_API_URL", "http://127.0.0.1:8000/translate")
EN_PATH = "ui/localization/en.json"
HI_PATH = "ui/localization/hi.json"
QA_REPORT_DIR = "localization"
QA_REPORT_PATH = "localization/qa_report.json"
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.95"))
QUALITY_THRESHOLD = float(os.getenv("QUALITY_THRESHOLD", "0.90"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_REFLECTION_MODEL", "gemini-1.5-flash")


def load_json_safe(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


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
    """Call Gemini API for reflection. Returns raw text or raises."""
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
    candidates = data.get("candidates") or []
    if not candidates:
        raise Exception("Gemini API returned no candidates")
    parts = candidates[0].get("content", {}).get("parts") or []
    if not parts:
        raise Exception("Gemini API returned no parts")
    return (parts[0].get("text") or "").strip()


def extract_json_from_text(text: str):
    """
    Extract and parse JSON from LLM response. Handles markdown fences, extra text, trailing commas.
    Returns dict or None on failure.
    """
    if not text or not isinstance(text, str):
        return None
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

    def try_parse(s):
        try:
            return json.loads(s)
        except (json.JSONDecodeError, ValueError):
            return None

    parsed = try_parse(text)
    if parsed is not None:
        return parsed

    # Clean and retry: trailing commas, strip backticks/code markers
    cleaned = text.strip()
    cleaned = re.sub(r",\s*}", "}", cleaned)
    cleaned = re.sub(r",\s*]", "]", cleaned)
    cleaned = cleaned.strip().strip("`").strip()
    if cleaned.lower().startswith("json"):
        cleaned = cleaned[4:].lstrip()
    parsed = try_parse(cleaned)
    if parsed is not None:
        return parsed

    return None


# ---------------------------------------------------------------------------
# 1. ChangeDetectorAgent
# ---------------------------------------------------------------------------
class ChangeDetectorAgent:
    """Loads source/target JSON and detects new or changed keys. Tracks reused strings."""

    def __init__(self, en_path=EN_PATH, hi_path=HI_PATH):
        self.en_path = en_path
        self.hi_path = hi_path

    def detect(self):
        en_strings = load_json_safe(self.en_path)
        existing_hi = load_json_safe(self.hi_path)

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

    def __init__(self, threshold=CONFIDENCE_THRESHOLD):
        self.threshold = threshold

    def _translate_one(self, key, source_text):
        """One key: translate with at most one retry. Returns (entry, confidence, below_threshold, api_calls, retried)."""
        result = _translate_api(source_text)
        api_calls = 1
        first_confidence = result["confidence_score"]

        if first_confidence >= self.threshold:
            print(f"[TRANSLATOR] Translating key: {key}")
            print(f"[TRANSLATOR] Confidence {first_confidence:.2f} – accepted")
            entry = {"source": source_text, "translation": result["translation"]}
            return entry, first_confidence, False, api_calls, False

        print(f"[TRANSLATOR] Translating key: {key}")
        print(f"[TRANSLATOR] Confidence {first_confidence:.2f} below threshold {self.threshold} – retrying...")
        result2 = _translate_api(source_text)
        api_calls = 2
        retry_confidence = result2["confidence_score"]

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

        for key, source_text in changes.items():
            entry, confidence, below_threshold, api_calls, retried = self._translate_one(key, source_text)
            translations[key] = {
                "entry": entry,
                "confidence": confidence,
                "below_threshold": below_threshold,
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

        return {
            "translations": translations,
            "stats": {
                "total_api_calls": total_api_calls,
                "retries_performed": retries_performed,
                "accepted_confidences": accepted_confidences,
                "low_confidence_items": low_confidence_items,
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
        """Call Gemini and parse JSON. On parse failure use quality_score=0.5 to avoid false build failures."""
        prompt = REFLECTION_PROMPT_TEMPLATE.format(
            source_text=source_text.replace('"', '\\"'),
            translated_text=translated_text.replace('"', '\\"'),
        )
        try:
            response_text = _call_gemini(prompt)
        except Exception:
            return {
                "quality_score": 0.5,
                "issues": "Reflection parsing failed",
                "suggested_improvement": "",
            }

        parsed = extract_json_from_text(response_text)
        if parsed is None:
            truncated = (response_text[:300] + "...") if len(response_text) > 300 else response_text
            print(f"[REFLECTION] Failed to parse reflection JSON. Raw response logged.")
            print(f"[REFLECTION] Raw (truncated 300): {truncated}")
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
        """Run reflection on each newly translated key. Enrich and return result + total_reflection_calls."""
        translations = translation_result["translations"]
        trans_stats = translation_result["stats"]
        total_reflection_calls = 0
        enriched = {}

        for key, data in translations.items():
            entry = data["entry"]
            source_text = entry["source"]
            translated_text = entry["translation"]

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

Provide an improved Hindi translation that:
- Preserves original meaning
- Is natural for UI usage
- Is concise and clear

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
            text = _call_gemini(prompt)
            if "```" in text:
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    text = text[start:end]
            data = json.loads(text)
            return (data.get("improved_translation") or "").strip() or None
        except (json.JSONDecodeError, ValueError, Exception):
            return None

    def improve(self, reflection_result):
        """Improve only keys where confidence >= threshold and quality < quality_threshold. Re-run reflection on improved."""
        translations = reflection_result["translations"]
        trans_stats = reflection_result["stats"]
        total_improvement_attempts = 0
        re_reflection_calls = 0
        result_translations = {}

        for key, data in translations.items():
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
                print(f"[IMPROVEMENT] Attempting improvement for key: {key}")
                improved_text = self._request_improvement(source_text, translated_text, issues)
                if improved_text:
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

    def __init__(self, confidence_threshold=CONFIDENCE_THRESHOLD, quality_threshold=QUALITY_THRESHOLD):
        self.confidence_threshold = confidence_threshold
        self.quality_threshold = quality_threshold

    def validate(self, improvement_result):
        translations = improvement_result["translations"]
        stats = improvement_result["stats"]

        low_confidence_items = []
        accepted_confidences = []
        quality_scores = []

        for key, data in translations.items():
            confidence = data["confidence"]
            quality_score = data.get("quality_score", 0.0)
            entry = data["entry"]
            source = entry["source"]
            translation = entry["translation"]

            quality_scores.append(quality_score)

            fails_confidence = confidence < self.confidence_threshold
            fails_quality = quality_score < self.quality_threshold

            if fails_confidence or fails_quality:
                if fails_quality and not fails_confidence:
                    print(f"[VALIDATOR] Key {key} failed quality threshold ({quality_score:.2f} < {self.quality_threshold})")
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
            },
        }


# ---------------------------------------------------------------------------
# 6. ReportAgent
# ---------------------------------------------------------------------------
class ReportAgent:
    """Generates localization/qa_report.json and prints CI summary block."""

    def __init__(self, report_dir=QA_REPORT_DIR, report_path=QA_REPORT_PATH):
        self.report_dir = report_dir
        self.report_path = report_path

    def generate(self, detector_result, improvement_result, validation_result, execution_time_seconds):
        det_stats = detector_result["stats"]
        impr_stats = improvement_result["stats"]
        metrics = validation_result["metrics"]
        status = validation_result["status"]

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
            "total_strings_in_source": det_stats["total_strings_in_source"],
            "strings_reused": det_stats["strings_reused"],
            "new_or_changed_strings": det_stats["new_or_changed_strings"],
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
        self.detector = ChangeDetectorAgent()
        self.translator = TranslationAgent()
        self.reflection = ReflectionAgent()
        self.improvement = ImprovementAgent(reflection_agent=self.reflection)
        self.validator = ValidationAgent()
        self.reporter = ReportAgent()

    def run(self):
        start_time = time.time()

        print("\n[ORCHESTRATOR] Starting incremental localization with change detection...\n")

        detector_result = self.detector.detect()
        translation_result = self.translator.process(detector_result["changes"])
        reflection_result = self.reflection.evaluate(translation_result)
        improvement_result = self.improvement.improve(reflection_result)
        validation_result = self.validator.validate(improvement_result)

        elapsed = time.time() - start_time
        self.reporter.generate(detector_result, improvement_result, validation_result, elapsed)

        # Merge reused + new translations and write hi.json
        final_hi = dict(detector_result["existing_translations"])
        for key, data in improvement_result["translations"].items():
            final_hi[key] = data["entry"]

        with open(HI_PATH, "w", encoding="utf-8") as f:
            json.dump(final_hi, f, ensure_ascii=False, indent=2)

        if validation_result["status"] == "FAILED":
            low_confidence_items = validation_result["metrics"]["low_confidence_items"]
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
