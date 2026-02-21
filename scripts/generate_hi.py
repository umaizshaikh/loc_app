import json
import requests
import time
import os

API_URL = "http://127.0.0.1:8000/translate"
EN_PATH = "ui/localization/en.json"
HI_PATH = "ui/localization/hi.json"

def translate(text):
    payload = {
        "source_text": text,
        "target_language": "Hindi",
        "content_type": "ui",
        "product_category": "loc_app"
    }

    response = requests.post(API_URL, json=payload)

    if response.status_code != 200:
        raise Exception(f"API Error: {response.text}")

    return response.json()

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
    api_calls = 0
    reused = 0

    print("\n🔄 Starting incremental localization with change detection...\n")

    for key, en_value in en_strings.items():
        existing_entry = existing_hi.get(key)

        # If key exists and source unchanged → reuse
        if existing_entry and existing_entry.get("source") == en_value:
            new_hi[key] = existing_entry
            reused += 1
            print(f"✓ Reused: {key}")
        else:
            print(f"→ Translating: {key}")
            result = translate(en_value)
            new_hi[key] = {
                "source": en_value,
                "translation": result["translation"]
            }
            api_calls += 1
            print(f"   ✓ {result['translation']} (Confidence: {result['confidence_score']})")

    with open(HI_PATH, "w", encoding="utf-8") as f:
        json.dump(new_hi, f, ensure_ascii=False, indent=2)

    print("\n✅ Localization complete.")
    print(f"API calls made: {api_calls}")
    print(f"Strings reused: {reused}")
    print(f"Total time: {round(time.time() - start_time, 2)}s")

if __name__ == "__main__":
    main()