# 🧠 Personal Memory Assistant (PMA)

A **local-first AI-powered assistant** that indexes your personal and project files, then answers natural language questions with source-backed precision.

---

## ✨ Latest Features (v0.0.36)

- **🚀 Full-Height Explorer:** Optimized layout that utilizes 100% of your browser tab height.
- **🗺️ TreeSize Treemap:** Advanced visualization with "Ultrasonic Blue" theme, tiered borders, and 1-second highlight animation on navigation.
- **📁 Robust Path Handling:** Fixed folder nesting issues; clean hierarchical views for Windows and Linux paths.
- **⚡ Performance Insights:** New filtering by file extension in both Explorer and Insights tabs.
- **🛠️ Backend API:** Standardized `/api` prefix for all endpoints with robust Pydantic validation.

---

## 🛠️ Development & Build

### 1. Frontend Build
The frontend is a modular React app located in `/frontend`.
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
To generate a clean, standalone `.exe` in the root directory:
```powershell
python -m PyInstaller --clean --noconfirm --distpath . --workpath .\build .\PMA.spec
```

---

## 🏗️ Architecture
- **Backend:** FastAPI (Python 3.12+)
- **Database:** SQLite + FTS5 (Metadata)
- **Vector Store:** ChromaDB (Embeddings)
- **Frontend:** React + TypeScript + Vite + ECharts
- **AI Models:** SentenceTransformers (Local) + Gemini API (Cloud LLM)

---
**Built for the SanDisk Hackathon** · Made with ❤️ by [Binitpyro](https://github.com/Binitpyro)
