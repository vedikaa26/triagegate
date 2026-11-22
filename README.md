# 🛡️ TriageGate — Cost-Aware Log Anomaly Detection & Root-Cause Triage

> *"Never pay for LLM calls on healthy windows."*

TriageGate is a **production-grade, cost-aware observability system** that processes high-volume log streams through a fast Tier-1 ML anomaly detector, and only escalates to a Tier-2 LLM root-cause analysis when a genuine, deduplicated anomaly is confirmed.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         TriageGate System                               │
│                                                                         │
│  Log Stream (synthetic + real)                                          │
│       │                                                                 │
│       ▼  (5-second tumbling window)                                     │
│  ┌─────────────────────────────────────┐                               │
│  │  TIER-1: Fast Cost Gate (< 5ms)     │                               │
│  │  • IsolationForest (sklearn)        │                               │
│  │  • error_rate > 15%  heuristic      │◄── ~95% windows STOP here    │
│  │  • p95_latency > 1200ms heuristic   │    → LLM call AVOIDED        │
│  └──────────────────┬──────────────────┘                               │
│                     │ ANOMALY DETECTED                                  │
│                     ▼                                                   │
│  ┌─────────────────────────────────────┐                               │
│  │  DEDUP GATE (60s cooldown)          │                               │
│  │  SHA-256 hash(service + template)   │◄── Duplicate? SUPPRESSED     │
│  └──────────────────┬──────────────────┘                               │
│                     │ NEW incident                                      │
│                     ▼                                                   │
│  ┌─────────────────────────────────────┐                               │
│  │  TIER-2: LLM Triage                 │                               │
│  │  • Gemini 1.5 Flash (or mock)       │                               │
│  │  • Compressed window summary        │                               │
│  │  • Top 5 error log samples          │                               │
│  │  • Structured JSON output           │                               │
│  └──────────────────┬──────────────────┘                               │
│                     │                                                   │
│                     ▼                                                   │
│  ┌─────────────────────────────────────┐                               │
│  │  INCIDENT RECORD                    │                               │
│  │  severity · root_cause              │                               │
│  │  remediation · confidence           │                               │
│  └─────────────────────────────────────┘                               │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Features

| Feature | Detail |
|---------|--------|
| **Tier-1 ML Gate** | `IsolationForest` (200 trees) + rolling Z-score rules, pre-fitted on 200 synthetic healthy windows at startup |
| **5-second tumbling windows** | 5 features extracted: `log_count`, `error_rate`, `p95_latency_ms`, `status_5xx_count`, `unique_error_templates` |
| **Deduplication** | SHA-256 signature hash with 60-second cooldown prevents LLM re-calls for the same incident pattern |
| **Tier-2 LLM** | Gemini 1.5 Flash with structured JSON output enforced; Mock LLM fallback if no API key |
| **3 Failure Scenarios** | `db_pool_exhaustion`, `auth_502_spike`, `memory_leak_oom` — all injectable on demand |
| **Cost ROI Telemetry** | Real-time tracking of LLM calls avoided, cost saved vs naive approach |
| **Dark-mode Dashboard** | React + Recharts + Lucide observability UI, polling every 2s |

---

## Quick Start

### Prerequisites
- Python 3.11+, Node 18+
- (Optional) Gemini API key — works fully without one via Mock LLM

### 1. Backend

```bash
cd backend
cp ../.env.example .env
# Optional: add your GEMINI_API_KEY to .env

pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

### 3. Run Tests

```bash
cd backend
pytest tests/test_detector.py -v
# 21 tests: feature extraction, IsolationForest, Tier-1 gate, deduplication
```

---

## Docker Compose

```bash
# Copy env file
cp .env.example .env

# Start everything
docker compose up --build

# Frontend → http://localhost:5173
# Backend  → http://localhost:8000
# API Docs → http://localhost:8000/docs
```

---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health + model status |
| `/api/status` | GET | Full system status + metrics |
| `/api/metrics` | GET | Cost/ROI telemetry |
| `/api/windows` | GET | Recent log windows |
| `/api/incidents` | GET | Triaged incidents |
| `/api/recent` | GET | Combined windows + incidents |
| `/api/simulator/start` | POST | Start log stream |
| `/api/simulator/stop` | POST | Stop log stream |
| `/api/inject` | POST | Inject failure scenario |
| `/api/reset` | DELETE | Clear all data |

### Inject a scenario

```bash
curl -X POST http://localhost:8000/api/inject \
  -H "Content-Type: application/json" \
  -d '{"scenario": "db_pool_exhaustion", "duration_seconds": 40}'
```

---

## Project Structure

```
triagegate/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI + lifespan + window processor
│   │   ├── models.py        # SQLAlchemy: LogWindow, Incident, CostMetric
│   │   ├── schemas.py       # Pydantic v2 request/response schemas
│   │   ├── database.py      # Async SQLite engine + session
│   │   ├── state.py         # Shared app state (avoids circular imports)
│   │   ├── ml/
│   │   │   └── detector.py  # IsolationForest + feature extraction
│   │   ├── services/
│   │   │   ├── triage.py    # Tier-2 LLM + dedup cache + mock fallback
│   │   │   └── simulator.py # Synthetic log generator + scenario injector
│   │   └── api/
│   │       └── routes.py    # REST endpoints
│   ├── tests/
│   │   └── test_detector.py # 21 pytest unit tests
│   ├── requirements.txt
│   ├── pytest.ini
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── App.jsx           # Main dashboard
│   │   ├── api.js            # API client
│   │   ├── useTriageData.js  # Data-fetching hook (2s poll)
│   │   ├── KpiCard.jsx       # KPI stat cards
│   │   ├── MetricsChart.jsx  # Recharts dual-axis chart
│   │   └── IncidentCard.jsx  # Expandable triage report card
│   └── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Design Trade-offs

### Why IsolationForest over a simple threshold?
Thresholds require domain knowledge per service. IsolationForest learns the "normal" hyperplane from the data distribution, catching multi-variate anomalies (e.g. moderately elevated latency + moderately elevated error rate together) that simple rules miss.

### Why SHA-256 deduplication over LLM result caching?
Hashing the incident *signature* (service + error template) prevents the LLM from being re-called for the same ongoing incident pattern. This is more accurate than caching the LLM output since input prompts vary slightly per window.

### Why 5-second windows?
Fast enough to detect P2 incidents within one window (~5s detection lag), but coarse enough to aggregate sufficient log volume for meaningful statistical features. Configurable via `WINDOW_SECONDS` env var.

### Why Mock LLM over requiring a real API key?
Zero-barrier-to-demo. The mock produces scenario-aware, structured triage reports that look identical to real LLM output — allowing the full system to work offline and in CI/CD pipelines.

### Cost Model
- Naive approach: 1 LLM call per 5s window = ~720 calls/hour ≈ \$0.043/hour (Gemini 1.5 Flash)
- TriageGate: ~5% anomaly rate + deduplication ≈ ~2–5 calls/hour ≈ \$0.00018/hour
- **~95–98% cost reduction** in steady-state healthy systems
