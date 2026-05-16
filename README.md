# RyskNode LogicModel Scrapper

A high-performance FastAPI-based web scraper built with Domain-Driven Design (DDD) principles. It aggregates company and director information from **Falcon Biz** and **Tracxn** concurrently, persisting data to a PostgreSQL database using a SQLAlchemy Unit of Work (UOW) pattern.

## 🚀 Features

- **Concurrent Scraping**: Uses `asyncio` to scrape multiple sources (Falcon Biz & Tracxn) simultaneously for a single query.
- **Synchronous & Asynchronous Flows**:
  - **Single Query**: Returns scraped data immediately in the API response.
  - **Batch Queries**: Acknowledges the request immediately and processes the list in the background.
- **Robust Persistence**: Implements a strict SQLAlchemy schema with the Unit of Work pattern for reliable database transactions.
- **Automated Reporting**: Generates Markdown reports in the `reports/` directory after every batch process, detailing successes and failures.
- **Clean Architecture**: Strictly follows DDD, with separated layers for Adapters, Repositories, Services, and API Schemas.
- **Search Optimized**: Includes normalized searchable fields and `TSVECTOR` support for advanced searching.

## 🛠️ Tech Stack

- **Framework**: FastAPI
- **Dependency Injection**: `inject`
- **Database**: PostgreSQL with SQLAlchemy 2.0 & FastCRUD
- **Parsing**: `lxml`
- **Asynchrony**: `aiohttp`, `asyncio`
- **Package Manager**: `uv`

## 📋 Prerequisites

- Python 3.13+
- PostgreSQL
- `uv` (recommended)

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
   SQLALCHEMY_URI=postgresql+asyncpg://user:password@localhost/dbname
   ```

3. **Install Dependencies**:
   ```bash
   uv sync
   ```

## 🏃 Running the Application

To start the FastAPI server:
```bash
uv run python src
```
The server will start at `http://127.0.0.1:8080`.

## 📖 API Documentation

### 1. Scrape Endpoint
Handles both single and batch requests.

- **URL**: `/api/v1/scrape`
- **Method**: `POST`
- **Payload**:
  - **Single Instance**:
    ```json
    { "queries": "U62010UP2023PTC191535" }
    ```
  - **Batch Instance**:
    ```json
    { "queries": ["U62010UP2023PTC191535", "L01110MH1993PLC072842"] }
    ```

### 2. Response Behavior
- **Single Query**: Returns the consolidated company object.
- **Batch Query**: Returns a status message:
  ```json
  {
    "status": "Processing in background",
    "total_queries": 2
  }
  ```

## 📊 Reporting
When a batch request is processed, a report is generated in `reports/scrape_report_YYYYMMDD_HHMMSS.md` with the following structure:
- Total requests processed.
- Success/Failure count.
- Detailed logs for failed CINs with specific reasons (e.g., Timeout, 404).

## 🏗️ Project Structure
```text
src/
├── api/             # API routes and request/response schemas
├── app/             # Application bootstrap and DI configuration
├── common/          # Shared utilities, base classes, and adapters
│   ├── adapter/     # Website-specific scrapers (Falcon Biz, Tracxn)
│   ├── base/        # Core framework logic (logger, router, etc.)
│   └── schema/      # Shared Pydantic models
├── model/           # SQLAlchemy database models
├── repository/      # Database abstraction layer
└── service/         # Business logic and UOW orchestration
```
