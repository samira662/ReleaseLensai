# ReleaseLens AI

AI-powered release intelligence for software teams (SE331 capstone proposal). Pick two versions of a GitHub
repository and get: classified changes, breaking-change findings, a Low/Medium/High risk score, release notes for
end-users / developers / QA, and a Q&A chat over the release (RAG).

## What maps to the proposal

| Proposal item | Where |
|---|---|
| FR1 register/login | `backend/app/routers/auth.py`, `auth.py` |
| FR2/FR3 repo + two versions, fetch commits/PRs/issues/diffs | `services/github_service.py`, `routers/projects.py` |
| FR4 results stored per project | `models.py` (PostgreSQL, or SQLite for dev) |
| FR5 editable categories | `routers/changes.py`, `frontend/src/components/ChangeList.jsx` |
| FR6/FR7 edit + export Markdown/PDF | `routers/notes.py` |
| AR1 change classifier | `services/classifier.py` |
| AR2 breaking-change detector (Python via `ast`, JS/TS extractor) | `services/breaking_detector.py` |
| AR3 three audience notes | `services/notes_generator.py` |
| AR4 risk score + factors | `services/risk_scorer.py` |
| AR5 RAG Q&A with citations | `services/rag_service.py` |
| AR6 AI-marked output, approval before export | `Note.approved`, export returns 409 until approved |
| AR7 80% classifier agreement | Evaluate with your 200 labelled changes (see "Evaluation" below) |
| Security: tokens encrypted, never sent to LLM, passwords hashed | `auth.py`, pipeline in `routers/projects.py` |

## Run it

### Backend
```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # add ANTHROPIC_API_KEY (and optionally GITHUB_TOKEN)
uvicorn app.main:app --reload # http://localhost:8000/docs
pytest                        # 8 tests
```

### Frontend
```bash
cd frontend
npm install
npm run dev                   # http://localhost:5173 (proxies /api to :8000)
```

### Docker (PostgreSQL + backend)
```bash
cp backend/.env.example backend/.env && docker compose up --build
```

## Modes
- **With `ANTHROPIC_API_KEY`**: the LLM classifies, summarises, writes notes, explains risk and answers questions.
- **Without a key**: the app still works end-to-end using rule-based fallbacks (keyword classifier, template notes,
  extractive Q&A). Useful for demos and tests. AI-written text is labelled "AI draft"; fallback text is labelled
  "Template draft".

## Design notes
- Breaking-change detection is deterministic (signature diff of old vs new file versions); the LLM only rewords the reason.
- Risk = transparent weighted score (breaking 30, size 20, files 10, security 10, bug-prone files 10, bug-fix ratio 10,
  missing tests 10) + optional scikit-learn model (`RISK_MODEL_PATH`) + LLM nudge of at most ±10 points.
- Diffs are used in memory only; only file names and summaries are stored. Users can switch off sending diffs to the LLM.
- Analysis runs as a FastAPI background task; the UI polls project status.
- Embeddings: `sentence-transformers` if installed, else a built-in hashing embedder. Vector store: ChromaDB, else in-memory.

## Evaluation (AR7)
Export your 200 hand-labelled changes as `[{title, message, files, label}]` and compare with
`classify_changes()` from `app/services/classifier.py`; agreement = matches / 200.

## Known limits
- JS/TS parsing is regex-based for *exported* functions (tree-sitter is the planned upgrade). Python uses `ast`.
- Behavior changes inside a function body are not detected by the signature comparison.
- Alembic migrations are not included; tables are created on startup (`Base.metadata.create_all`).
- Analysis calls the GitHub API once per commit, so use a token for releases near 100 commits.
