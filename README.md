# RyskNode LogicModel Scrapper

A high-performance FastAPI and Celery-based financial intelligence and scraping engine built with Domain-Driven Design (DDD) principles. It provides synchronous REST APIs and asynchronous Redis queue processing with real-time Flower dashboard observability and Redis Pub/Sub event broadcasting.

## 🚀 Features

- **Dual Execution Architecture**:
  - **Synchronous REST API**: Direct, fast in-process JSON responses for Buyer Risk Assessment (S1), Credit Limit (S2), and Single Company Scrape.
  - **Asynchronous Redis Worker**: Offload heavy jobs (Batch Scraping, Data.gov.in Sync, Background Risk Scoring) to dedicated Celery queues.
- **Dynamic Event Pub/Sub**: Automatically publishes task completion/failure events to Redis channels (`rysknode-events:<task_name>.<status>`).
- **Real-Time Observability**: Integrated **Flower UI** dashboard with basic authentication for live queue analytics, worker health, and retry monitoring.
- **Modular Queue Routing**: Isolated queues (`default`, `scrape`, `sync`, `assess`) preventing high-volume background jobs from blocking fast tasks.
- **Robust Persistence**: SQLAlchemy 2.0 with isolated Unit of Work (UOW) per worker process to prevent connection pool corruption across forks.
- **Explainable AI (XAI)**: SHAP and LIME credit risk model explainability with Tri-Core conduct scorecards.

---

## 🛠️ Tech Stack

- **API Framework**: FastAPI, Uvicorn
- **Async Task Queue & Workers**: Celery 5.6+, Kombu
- **Message Broker & Event Bus**: Redis 6.0+
- **Monitoring Dashboard**: Flower UI 2.1+
- **Dependency Injection**: `inject`
- **Database**: PostgreSQL with SQLAlchemy 2.0 & FastCRUD
- **ML / Scoring Engines**: LightGBM, XGBoost, SHAP, LIME, OpenLGD
- **Package Manager**: `uv`

---

## 📋 Prerequisites

- Python 3.13+
- PostgreSQL
- Redis Server (`redis-server`)
- `uv` package manager

---

## ⚙️ Setup & Installation

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd RyskNode_LogicModel_Scrapper
   ```

2. **Configure Environment**:
   Create a `.env` file in the root directory (refer to `.env.sample`):
   ```bash
   APP_ENV=LOCAL
   APP_PORT=8080
   SQLALCHEMY_URI=postgresql+asyncpg://user:password@localhost:5432/rysknode_dev
   
   # Celery & Redis
   CELERY_BROKER_URL=redis://:default@localhost:6379/0
   CELERY_RESULT_BACKEND=redis://:default@localhost:6379/1
   CELERY_EVENT_APP_NAME=rysknode
   
   # Flower UI
   CELERY_FLOWER_PORT=5555
   CELERY_FLOWER_USER=admin
   CELERY_FLOWER_PASSWORD=flowerpass
   ```

3. **Install Dependencies**:
   ```bash
   uv sync
   ```

---

## 🏃 Running the Services

To run the complete stack locally, start the following processes in separate terminals:

### 1. Redis Server
Ensure your Redis instance is running:
```bash
redis-server
```

### 2. Unified Service Orchestration
To start the FastAPI web server, the Celery worker, and Flower UI simultaneously in a single command:
```bash
uv run python src
```

When started, `src/__main__.py` launches:
1. **Celery Worker**: Listens on all active queues (`default`, `buyer_risk`, `credit_limit`, `sync`) with `CELERY_WORKER_CONCURRENCY` parallel process slots.
2. **Flower UI Dashboard**: Real-time worker health, tasks execution, and analytics on `http://127.0.0.1:5555`.
3. **FastAPI Application**: REST API on `http://127.0.0.1:8080`.

### 3. Flower UI Dashboard
- **URL**: `http://127.0.0.1:5555`
- **Username**: `admin` (or `CELERY_FLOWER_USER` in `.env`)
- **Password**: `flowerpass` (or `CELERY_FLOWER_PASSWORD` in `.env`)
- In Flower, you will see the active worker with its assigned queues and pool size.



---

## 📡 Redis Pub/Sub Event Channels

All background tasks communicate via Redis Pub/Sub channels. When a worker completes an evaluation, the result payload is automatically broadcast to:

| Task | Queue | Event Channel on Success |
| :--- | :--- | :--- |
| `assess_buyer_task` | `buyer_risk` | `rysknode-events:assess_buyer_task.completed` |
| `assess_credit_limit_task` | `credit_limit` | `rysknode-events:assess_credit_limit_task.completed` |
| `sync_data_gov_state` | `sync` | `rysknode-events:sync_data_gov_state.completed` |

**Example Subscriber (Python):**
```python
import redis, json

r = redis.Redis.from_url("redis://:default@localhost:6379/0", decode_responses=True)
pubsub = r.pubsub()
pubsub.psubscribe("rysknode-events:*")

for message in pubsub.listen():
    if message["type"] == "pmessage":
        channel = message["channel"]  # e.g. rysknode-events:assess_buyer_task.completed
        data = json.loads(message["data"])
        print(f"[{channel}] Task {data['task_id']} Result: {data['result']}")
```


---

---

## 📖 Active API Endpoints

### 1. Buyer Risk Assessment (S1)
- **JSON API**: `POST /api/v1/assess` $\rightarrow$ Computes Tri-Core scores, PD, risk band, and XAI explainability.
- **HTML Report**: `POST /api/v1/assess/report` $\rightarrow$ Renders dynamic S1 Risk Assessment Report.

### 2. Credit Limit Assessment (S2)
- **JSON API**: `POST /api/v1/credit-limit` $\rightarrow$ Computes recommended credit limit, tenor, and stress testing table.
- **HTML Report**: `POST /api/v1/credit-limit/report` $\rightarrow$ Renders dynamic S2 Credit Limit Report.

### 3. Sector Intelligence (S5)
- **Supported NICs**: `GET /api/v1/sector/nics` $\rightarrow$ Lists supported National Industrial Classification codes.
- **Sector Analysis**: `POST /api/v1/sector/{nic_code}` $\rightarrow$ Benchmarks financial metrics against sector peers.
- **Sector Report**: `POST /api/v1/sector/{nic_code}/report` $\rightarrow$ Renders HTML or PDF sector intelligence report.

### 4. MCA / Data.gov.in Synchronization
- `POST /api/v1/sync/data-gov` $\rightarrow$ Starts state/global sync in `sync` queue.
- `POST /api/v1/sync/continue` $\rightarrow$ Resumes interrupted sync from last offset.
- `POST /api/v1/sync/stop?task_id=<id>` $\rightarrow$ Revokes active sync task.
- `GET /api/v1/sync/reports` $\rightarrow$ Lists sync markdown summary reports.

---

## 🏗️ Project Structure

```text
src/
├── api/                  # FastAPI routers
│   ├── assess.py         # S1 Buyer Risk Assessment endpoints
│   ├── credit_limit.py   # S2 Credit Limit Assessment endpoints
│   ├── sync.py           # MCA / Data.gov.in sync endpoints
│   └── sector_intel.py   # Sector benchmark & intelligence endpoints
├── app/                  # Application bootstrap & dependency injection
│   ├── bootstrap.py      # FastAPI app initialization
│   └── dependency.py     # DI container bindings & isolated UOW factory
├── asyncworker/          # Celery Sidecar Module
│   ├── __init__.py       # Celery app instance & lifecycle signals (pub/sub)
│   ├── celery_task_context.py # ContextVar async-sync bridge for task logging
│   ├── events.py         # Redis Pub/Sub event broadcaster
│   ├── tasks.py          # Celery task definitions (buyer_risk, credit_limit, sync)
│   └── settings/
│       └── celeryconfig.py # Celery broker, queues, timeouts & beat scaffold
├── common/               # Shared utilities, base router, logger, and UOW base
├── domain/               # Core scoring models, XAI explainer, stress tester
├── model/                # SQLAlchemy database models
├── repository/           # Data access repository layer
└── service/              # Domain services (PPREService, DataGovSyncService, ArtifactService, ReportService)
```


