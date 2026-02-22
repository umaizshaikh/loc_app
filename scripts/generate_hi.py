import json
import os
import sys
import time

import requests

API_URL = os.getenv("LOCALIZATION_API_URL", "http://127.0.0.1:8000/translate")
EN_PATH = "ui/localization/en.json"
HI_PATH = "ui/localization/hi.json"
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.95"))


def translate(text):
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


def translate_with_retry(key, source_text):
    """
    Translate one key with at most one retry if confidence is below threshold.
    Returns (entry, confidence, below_threshold, api_calls, retried).
    """
    result = translate(source_text)
    api_calls = 1
    first_confidence = result["confidence_score"]

    if first_confidence >= CONFIDENCE_THRESHOLD:
        print(f"[INFO] Translating key: {key}")
        print(f"[INFO] Confidence {first_confidence:.2f} – accepted")
        entry = {"source": source_text, "translation": result["translation"]}
        return entry, first_confidence, False, api_calls, False

    print(f"[INFO] Translating key: {key}")
    print(f"[WARN] Confidence {first_confidence:.2f} below threshold {CONFIDENCE_THRESHOLD} – retrying...")
    result2 = translate(source_text)
    api_calls = 2
    retry_confidence = result2["confidence_score"]

    if retry_confidence >= CONFIDENCE_THRESHOLD:
        print(f"[INFO] Retry confidence: {retry_confidence:.2f} – accepted")
        entry = {"source": source_text, "translation": result2["translation"]}
        return entry, retry_confidence, False, api_calls, True

    print(f"[ERROR] Retry confidence {retry_confidence:.2f} – still below threshold.")
    entry = {"source": source_text, "translation": result2["translation"]}
    return entry, retry_confidence, True, api_calls, True


def load_json_safe(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def main():
    start_time = time.time()

    en_strings = load_json_safe(EN_PATH)
    existing_hi = load_json_safe(HI_PATH)

    new_hi = {}
    low_confidence_items = []
    total_api_calls = 0
    retry_count = 0
    reused_count = 0
    accepted_confidences = []

    print("\n[INFO] Starting incremental localization with change detection...\n")

    for key, en_value in en_strings.items():
        existing_entry = existing_hi.get(key)

        if existing_entry and existing_entry.get("source") == en_value:
            new_hi[key] = existing_entry
            reused_count += 1
            print(f"[INFO] Reused: {key}")
        else:
            entry, confidence, below_threshold, api_calls, retried = translate_with_retry(key, en_value)
            new_hi[key] = entry
            total_api_calls += api_calls
            if retried:
                retry_count += 1
            if not below_threshold:
                accepted_confidences.append(confidence)
            if below_threshold:
                low_confidence_items.append({
                    "key": key,
                    "source": en_value,
                    "translation": entry["translation"],
                    "confidence": confidence,
                })

    with open(HI_PATH, "w", encoding="utf-8") as f:
        json.dump(new_hi, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - start_time

    # Metrics
    print("\n[INFO] --- Metrics ---")
    print(f"[INFO] Total API calls: {total_api_calls}")
    print(f"[INFO] Number of retries performed: {retry_count}")
    print(f"[INFO] Strings reused from existing hi.json: {reused_count}")
    if accepted_confidences:
        avg_conf = sum(accepted_confidences) / len(accepted_confidences)
        print(f"[INFO] Average confidence (accepted translations): {avg_conf:.2f}")
    else:
        print("[INFO] Average confidence (accepted translations): N/A (no new translations or all below threshold)")
    print(f"[INFO] Total execution time: {round(elapsed, 2)}s")

    if low_confidence_items:
        print("\n[INFO] --- Low confidence summary ---")
        for item in low_confidence_items:
            print(f"  Key: {item['key']}")
            print(f"  Source: {item['source']}")
            print(f"  Final Translation: {item['translation']}")
            print(f"  Final Confidence: {item['confidence']:.2f}")
            print()
        print(f"[ERROR] Build failed due to translations below {CONFIDENCE_THRESHOLD} confidence threshold.")
        sys.exit(1)

    print("\n[INFO] All translations meet 95% confidence threshold.")
    print("[INFO] Localization complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[ERROR] Localization failed: {e}")
        sys.exit(1)
