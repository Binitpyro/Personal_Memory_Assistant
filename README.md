# 🧠 Personal Memory Assistant (PMA)

A **local-first AI-powered assistant** that indexes your personal and project files, then answers natural language questions with source-backed precision.

---

## ✨ Product Features

- **🚀 Rust-Accelerated Extraction:** Lightning-fast parallel text scanning and chunking via `rayon`.
- **🔐 Zero-Config AI Access:** Seamless Google OAuth login for your own Gemini quota (no manual API keys needed).
- **🦙 Local AI Auto-Detect:** Instantly discovers running instances of Ollama or LM Studio on your network.
- **🖥️ Standalone Desktop App:** Packaged securely with a one-time local API token, blocking unauthorized network access.
- **🎨 Fast-Path UI Badges:** Live chat UI rendering instantly distinguishes between fast exact-match answers and deep RAG inferences.

---

## 🛠️ Development & Build

### 1. Frontend Build
The frontend is a modular React app located in `/frontend` using TanStack React Query for caching.
```powershell
cd frontend
npm install
npm run build
```

### 2. Run Tests
```powershell
pytest -q
```

### 3. Compile Executable
We use an automated build script to compile the React frontend and bundle the Python/Rust backend into a single `PMA.exe`.
```powershell
python scripts/build_exe.py
```

---

## 🏗️ Architecture
- **Backend:** FastAPI (Python 3.12+)
- **Extraction Core:** Rust (Tier-1 fallback) + Python Extractors (Tier-2)
- **Database:** SQLite + FTS5 (Metadata)
- **Vector Store:** ChromaDB (Migration to LanceDB planned)
- **Frontend:** React + TypeScript + Vite + ECharts + WebGPU
- **AI Models:** SentenceTransformers (Local ONNX) + Gemini / Ollama / LM Studio

---
**Developed as an Open Source Product** · Made with ❤️ by [Binitpyro](https://github.com/Binitpyro)
