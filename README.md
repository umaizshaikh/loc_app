# 📄 Localization App

AI-powered localization pipeline with multi-agent architecture for translating English UI strings to Hindi.

## 🎯 Purpose

This application demonstrates:
- **Agent-based translation pipeline** with generation, evaluation, improvement, and validation
- **Multi-file support** for multiple `en*.json` source files
- **Persistent translation cache** for deterministic reuse across CI runs
- **Confidence and quality gating** with retry logic
- **Pure Hindi policy enforcement** to avoid transliterations
- **CI/CD integration** via GitHub Actions
- **Runtime language switching** in PySide6 desktop app

---

## 🏗 Architecture

### Agent-Based Pipeline

The translation pipeline uses a lightweight multi-agent architecture:

1. **ChangeDetectorAgent** - Detects new/changed strings in `en*.json` files
2. **TranslationAgent** - Translates with retry-once logic and confidence normalization
3. **ReflectionAgent** - Evaluates translation quality via `/evaluate` endpoint
4. **ImprovementAgent** - Self-improves translations that pass confidence but fail quality
5. **ValidationAgent** - Enforces confidence (≥0.95) and quality (≥0.90) thresholds
6. **ReportAgent** - Generates QA reports and metrics

### Pipeline Flow

```
en*.json files
    ↓
ChangeDetectorAgent (detect changes)
    ↓
TranslationAgent (translate with cache lookup)
    ↓
ReflectionAgent (evaluate quality)
    ↓
ImprovementAgent (improve if needed, enforce pure Hindi)
    ↓
ValidationAgent (gate by thresholds)
    ↓
ReportAgent (generate QA report)
    ↓
hi*.json files + qa_report.json
```

---

## 📁 Project Structure

```
loc_app/
│
├── main.py                          # Application entry point
│
├── ui/
│   ├── main_window.py               # PySide6 main window
│   └── localization/
│       ├── en.json                  # English source strings
│       ├── en1.json                 # Additional English sources
│       ├── hi.json                  # Hindi translations
│       ├── hi1.json                 # Additional Hindi translations
│       ├── qa_report.json           # QA metrics report
│       └── string_loader.py         # Runtime string loader
│
├── scripts/
│   └── generate_hi.py              # Agent-based translation pipeline
│
├── localization/
│   └── translation_cache.json      # Persistent translation cache
│
├── .github/
│   └── workflows/
│       └── localization.yml        # CI/CD pipeline
│
├── requirements.txt
├── .gitignore
└── README.md
```

---

## 🚀 Setup

### 1️⃣ Create Virtual Environment

```bash
python -m venv venv
venv\Scripts\activate    # Windows
source venv/bin/activate # Linux/Mac
```

### 2️⃣ Install Dependencies

```bash
pip install -r requirements.txt
```

Current dependencies:
- `PySide6` - Qt for Python UI framework
- `requests` - HTTP client for API calls

### 3️⃣ Environment Variables (for CI/localization API)

```bash
# Localization API endpoint
LOCALIZATION_API_URL=http://127.0.0.1:8000/translate

# Confidence threshold (default: 0.95)
CONFIDENCE_THRESHOLD=0.95

# Quality threshold (default: 0.90)
QUALITY_THRESHOLD=0.90

# Gemini API key (for reflection/improvement)
GEMINI_API_KEY=your_api_key_here

# Gemini model (default: gemini-1.5-flash)
GEMINI_REFLECTION_MODEL=gemini-1.5-flash
```

### 4️⃣ Run Application

```bash
python main.py
```

---

## 🔄 Translation Pipeline

### Running the Pipeline

```bash
python scripts/generate_hi.py
```

### Features

- **Multi-file support**: Automatically processes all `en*.json` files
- **Incremental detection**: Only translates new or changed strings
- **Translation cache**: Reuses validated translations across files and runs
- **Confidence gating**: Requires ≥95% confidence (normalized from 0-100 scale)
- **Quality gating**: Requires ≥90% quality score from reflection
- **Retry logic**: One retry attempt if confidence below threshold
- **Pure Hindi policy**: Rejects transliteration-based improvements
- **QA reporting**: Generates `qa_report.json` with metrics

### Cache Behavior

- Cache is bootstrapped from existing `hi*.json` files at startup
- Validated translations (passing both thresholds) are stored in cache
- Cache persists across CI runs via `localization/translation_cache.json`
- Same source text across multiple files reuses cached translation

### Example Output

```
[ORCHESTRATOR] Processing file: en.json
[CACHE] Reusing cached translation for key: btn_submit
[TRANSLATOR] Translating key: btn_new
[TRANSLATOR] Raw confidence: 98
[TRANSLATOR] Normalized confidence: 0.98
[REFLECTION] Quality score: 0.94
[VALIDATOR] Key btn_new passed.
[ORCHESTRATOR] Saved file: ui/localization/hi.json
```

---

## 🌍 Runtime Language Switching

From the application menu:

```
Settings → Switch to English
Settings → Switch to Hindi
```

The application:
1. Loads the selected JSON file
2. Reloads all visible UI text
3. Updates the interface immediately
4. Continues running without restart

---

## 🔁 CI/CD Integration

### GitHub Actions Workflow

The `.github/workflows/localization.yml` workflow:

1. **Triggers** on changes to:
   - `ui/localization/en.json`
   - `scripts/**`
   - `.github/workflows/localization.yml`

2. **Steps**:
   - Clones localization API repository
   - Starts localization API server
   - Runs translation pipeline
   - Generates QA report
   - Commits updated `hi*.json` files and `qa_report.json`

3. **Artifacts**:
   - Uploads `qa_report.json` as artifact

### QA Report Structure

```json
{
  "threshold": 0.95,
  "quality_threshold": 0.90,
  "processed_files": ["en.json", "en1.json"],
  "cache_hits": 5,
  "cache_misses": 2,
  "total_api_calls": 2,
  "average_confidence": 0.97,
  "average_quality_score": 0.94,
  "status": "PASSED"
}
```

---

## 🎯 Key Features

### Multi-File Support

- Automatically discovers all `en*.json` files
- Maps each to corresponding `hi*.json` file
- Processes files in deterministic sorted order
- Aggregates metrics across all files

### Translation Cache

- Persistent cache across CI runs
- Bootstrapped from existing `hi*.json` files
- Only stores validated translations (pass both thresholds)
- Shared across all source files

### Quality Assurance

- **Confidence threshold**: 0.95 (normalized from 0-100)
- **Quality threshold**: 0.90 (from reflection evaluation)
- **Retry logic**: One retry if confidence below threshold
- **Pure Hindi policy**: Rejects transliterations like "सबमिट"

### Agent Architecture

- Clean separation of concerns
- Each agent has single responsibility
- Pipeline orchestration via `LocalizationOrchestrator`
- Easy to extend with new agents

---

## 📊 Metrics Tracked

- Total API calls
- Cache hits/misses
- Retries performed
- Reflection calls
- Improvement attempts
- Average confidence (accepted translations only)
- Average quality score
- Low confidence items count

---

## 🔐 CI-Friendly Design

- JSON = Single Source of Truth
- No compiled translation files
- Runtime reload support
- UTF-8 encoding for multilingual support
- Safe fallback if key missing
- Deterministic processing order
- Cache for reproducibility

---

## ➕ Adding New Source Files

Simply create a new `en*.json` file:

```bash
# Create en_settings.json
cp ui/localization/en.json ui/localization/en_settings.json
```

The pipeline will automatically:
1. Detect the new file
2. Process it through the translation pipeline
3. Generate `hi_settings.json`
4. Include it in the QA report

---

## 🧪 Development

### Running Locally

1. Start localization API server (separate repository)
2. Set environment variables
3. Run `python scripts/generate_hi.py`
4. Check generated `hi*.json` files and `qa_report.json`

### Testing

- Modify `en.json` or add `en*.json` files
- Run pipeline
- Verify cache reuse for duplicate strings
- Check QA report for metrics

---

## 📌 Key Concepts

✔ Agent-based architecture  
✔ Multi-file translation support  
✔ Persistent translation cache  
✔ Confidence and quality gating  
✔ Pure Hindi policy enforcement  
✔ Incremental change detection  
✔ Retry logic with single attempt  
✔ QA reporting and metrics  
✔ CI/CD integration  
✔ Runtime language switching  

---

## 🏁 Built For

- AI-assisted localization workflows
- CI/CD-integrated translation pipelines
- Multi-file localization management
- Quality-assured translation generation
- Deterministic translation reuse

---

## 📝 License

[Add your license here]
