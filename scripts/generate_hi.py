"""
Lightweight agent-based localization pipeline.
Preserves: incremental detection, retry logic, confidence gating, QA report.
"""

import json
import os
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
# 3. ValidationAgent
# ---------------------------------------------------------------------------
class ValidationAgent:
    """Evaluates translation results against confidence threshold. Does not re-translate."""

    def __init__(self, threshold=CONFIDENCE_THRESHOLD):
        self.threshold = threshold

    def validate(self, translation_result):
        stats = translation_result["stats"]
        accepted = stats["accepted_confidences"]
        low_confidence_items = stats["low_confidence_items"]

        average_confidence = (sum(accepted) / len(accepted)) if accepted else 0.0
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
                "low_confidence_count": low_confidence_count,
                "low_confidence_items": low_confidence_items,
            },
        }


# ---------------------------------------------------------------------------
# 4. ReportAgent
# ---------------------------------------------------------------------------
class ReportAgent:
    """Generates localization/qa_report.json and prints CI summary block."""

    def __init__(self, report_dir=QA_REPORT_DIR, report_path=QA_REPORT_PATH):
        self.report_dir = report_dir
        self.report_path = report_path

    def generate(self, detector_result, translation_result, validation_result, execution_time_seconds):
        det_stats = detector_result["stats"]
        trans_stats = translation_result["stats"]
        metrics = validation_result["metrics"]
        status = validation_result["status"]

        report = {
            "threshold": CONFIDENCE_THRESHOLD,
            "total_strings_in_source": det_stats["total_strings_in_source"],
            "strings_reused": det_stats["strings_reused"],
            "new_or_changed_strings": det_stats["new_or_changed_strings"],
            "total_api_calls": trans_stats["total_api_calls"],
            "retries_performed": trans_stats["retries_performed"],
            "average_confidence": round(metrics["average_confidence"], 2),
            "low_confidence_count": metrics["low_confidence_count"],
            "low_confidence_items": [
                {"key": i["key"], "source": i["source"], "translation": i["translation"], "confidence": float(i["confidence"])}
                for i in metrics["low_confidence_items"]
            ],
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
        print(f"Average Confidence: {metrics['average_confidence']:.2f}")
        print(f"API Calls: {trans_stats['total_api_calls']}")
        print(f"Retries: {trans_stats['retries_performed']}")
        print(f"Low Confidence Items: {metrics['low_confidence_count']}")
        print(f"Status: {status}")
        print("----------------------------------------")


# ---------------------------------------------------------------------------
# 5. LocalizationOrchestrator
# ---------------------------------------------------------------------------
class LocalizationOrchestrator:
    """Runs the pipeline: detect -> translate -> validate -> report -> merge & write -> exit."""

    def __init__(self):
        self.detector = ChangeDetectorAgent()
        self.translator = TranslationAgent()
        self.validator = ValidationAgent()
        self.reporter = ReportAgent()

    def run(self):
        start_time = time.time()

        print("\n[ORCHESTRATOR] Starting incremental localization with change detection...\n")

        detector_result = self.detector.detect()
        translation_result = self.translator.process(detector_result["changes"])
        validation_result = self.validator.validate(translation_result)

        elapsed = time.time() - start_time
        self.reporter.generate(detector_result, translation_result, validation_result, elapsed)

        # Merge reused + new translations and write hi.json
        final_hi = dict(detector_result["existing_translations"])
        for key, data in translation_result["translations"].items():
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
                print()
            print(f"[ORCHESTRATOR] Build failed due to translations below {CONFIDENCE_THRESHOLD} confidence threshold.")
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
