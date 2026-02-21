# 📄 README.md

```markdown
# Mini Localization Demo (AI-Assisted Localization PoC)

## 🎯 Purpose

This is a minimal desktop application built using **PySide6 (Qt for Python)** to demonstrate:

- UI string externalization
- Runtime language switching
- JSON-based localization
- CI/CD-driven translation updates
- AI-assisted localization workflows

This project intentionally avoids Qt `.ts` files and uses a pure JSON-based translation system.

It is designed as a Proof of Concept for demonstrating modern localization pipelines.

---

## 🏗 Architecture Overview

The application follows a strict localization-first architecture:

- ❌ No hardcoded UI strings
- ❌ No embedded English text in widgets
- ❌ No Qt translation files (.ts)
- ✅ All UI text comes from JSON files
- ✅ Language switching at runtime
- ✅ No app restart required

Localization files are the single source of truth.

---

## 📁 Project Structure

```

mini_localization_app/
│
├── main.py
│
├── ui/
│   └── main_window.py
│
├── localization/
│   ├── en.json
│   ├── hi.json
│   └── string_loader.py
│
└── README.md

```

---

## 🚀 How to Run

### 1️⃣ Create Virtual Environment (Optional but Recommended)

```

python -m venv venv
venv\Scripts\activate    # Windows

```

### 2️⃣ Install Dependency

```

pip install PySide6

```

### 3️⃣ Run Application

From the project root:

```

python main.py

```

---

## 🌍 Runtime Language Switching

From the menu:

```

Settings → Switch to English
Settings → Switch to Hindi

````

The application will:

1. Load the selected JSON file
2. Reload all visible UI text
3. Update the interface immediately
4. Continue running without restart

---

## 🔎 How the Localization System Works

### 1. Language Loading

`string_loader.py`:

```python
load_language(lang_code)
````

Loads the corresponding JSON file into memory.

---

### 2. String Access

UI components retrieve strings using:

```python
get_string("key_name")
```

This ensures:

* No string is hardcoded
* JSON remains the only translation source

---

### 3. UI Retranslation

The `MainWindow` contains:

```python
retranslate_ui()
```

When language changes:

* JSON is reloaded
* All widgets are updated
* Dropdown values are refreshed
* Menu items are updated
* Status bar is refreshed

This is critical for runtime switching.

---

## 🧠 Why This Is Ideal for AI-Assisted Localization

Because:

* Translations live entirely in JSON
* No compiled translation files
* No Qt tooling required
* No code modifications needed when translations change

An AI pipeline can:

1. Detect new keys in `en.json`
2. Generate translations
3. Update `hi.json`
4. Commit changes via CI
5. App reflects updates automatically

Zero developer intervention required.

---

## 🔁 CI/CD Integration Model

In a production setup:

1. Developers update `en.json`
2. CI pipeline detects changes
3. AI translation job generates updated `hi.json`
4. PR is created automatically
5. Once merged — app uses new translations instantly

Because the app loads JSON at runtime, rebuilding the application is not required.

---

## ➕ Adding a New Language

To add French:

### 1️⃣ Create new file

```
localization/fr.json
```

Copy structure from `en.json`.

### 2️⃣ Add translations

Translate all values.

### 3️⃣ Add language switch action in UI

Connect:

```python
self.action_switch_fr.triggered.connect(lambda: self.switch_language("fr"))
```

No other changes required.

---

## 🔐 CI-Friendly Design Principles

* JSON = Single Source of Truth
* UI separated from translation logic
* No embedded strings
* Runtime reload support
* UTF-8 encoding for multilingual support
* Safe fallback if key missing

---

## 📌 Key Localization Concepts Demonstrated

✔ String externalization
✔ Runtime language switching
✔ UI retranslation
✔ Separation of concerns
✔ JSON-based localization
✔ CI-driven translation injection
✔ AI-ready translation pipeline

---

## 🧪 What This App Is NOT

* Not a production-grade i18n framework
* Not using Qt Linguist
* Not feature-heavy
* Not using .ts/.qm files
* Not performing real backend operations

This is intentionally minimal for clarity and demonstration.

---

## 🏁 Conclusion

This application serves as a clean demonstration of how modern AI-driven localization can be:

* Automated
* CI-integrated
* JSON-based
* Runtime-switchable
* Cleanly architected

It provides a foundation that can scale into:

* Enterprise localization systems
* Cloud-based translation workflows
* LLM-powered translation pipelines
* Continuous localization environments

---

## 👨‍💻 Built For

AI-assisted localization Proof of Concept
CI/CD-integrated translation workflows
Demonstrating modern i18n architecture patterns

---
