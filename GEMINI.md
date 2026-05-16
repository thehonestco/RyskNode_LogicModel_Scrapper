```markdown
# System Context & Project Overview for FastAPI Scraper

You are an expert Python Backend Engineer specializing in Web Scraping, FastAPI, Domain-Driven Design (DDD), and SQLAlchemy. Your task is to update and implement a high-performance REST API scraper based on the provided project structure. 

The application must aggregate company and director information by concurrently scraping **Falcon Biz** and **Tracxn**, handle both single and batch requests, integrate with a SQLAlchemy Unit of Work (UOW) pattern, and strictly adhere to the defined database schema.

---

## 1. Single API Endpoint Design
You must implement a single `POST` endpoint to handle all scraping requests:

**Endpoint:** `POST /api/v1/scrape`

**Request Payload (`src/api/schema/`):**
The API must accept a JSON body where the query can be either a single string or a list of strings (CINs or Company Names).
```json
{
  "queries": "U62010UP2023PTC191535" // Can be a string OR a list of strings: ["CIN1", "Company2"]
}
```

---

## 2. Execution Flows & Business Logic (`src/service/`)

### A. Single Instance Request (Synchronous Response)
If the payload contains a **single** CIN or Company Name:
1. The service layer triggers the Falcon Biz and Tracxn adapters concurrently.
2. The extracted data is merged based on the source mapping.
3. The consolidated data is immediately saved to the database using the existing **SQLAlchemy Unit of Work (UOW)** implementation (`src/service/unit_of_work.py`).
4. The API returns the scraped data immediately to the client in the response body.

### B. Multiple Instance Request (Background Batch Processing)
If the payload contains a **list** of CINs or Company Names:
1. The API immediately returns an acknowledgment response (e.g., `{"status": "Processing in background", "total_queries": N}`).
2. A FastAPI `BackgroundTask` (or background worker) is triggered.
3. The scraper processes the list in **batches** (e.g., using `asyncio.gather` with a semaphore to control concurrency).
4. For each query in the batch, the data is scraped and saved to the database via the UOW pattern.
5. **Reporting Module:** Once the background task finishes processing the entire list, it must generate a Markdown (`.md`) file inside a `reports/` directory at the root of the project. This report must record:
   * Total requests processed.
   * Total successful scrapes.
   * Total failed scrapes.
   * Detailed logs of which CINs/Names failed and the reasons (e.g., 404 Not Found, Timeout).

---

## 3. Strict Database Schema Mapping (`src/model/`)
You must define a SQLAlchemy model that strictly maps to the following database schema for saving the data. Pay special attention to PostgreSQL-specific types like `JSONB` and `TSVECTOR`.

**Table Name:** `company_data` (or similar domain entity)

| Column Name | SQLAlchemy Type | Nullable | Notes |
| :--- | :--- | :--- | :--- |
| `id` | `BigInteger` | NO | Primary Key |
| `company_name` | `String(255)` | NO | Original company name |
| `company_name_normalized` | `String(255)` | YES | Lowercase normalized searchable name |
| `cin` | `String(100)` | NO | Unique CIN number |
| `entity_type` | `String(100)` | YES | Pvt Ltd, LLP, OPC etc |
| `registration_number` | `String(100)` | YES | Company registration number |
| `current_status` | `String(100)` | YES | Active, Inactive etc |
| `incorporation_date` | `Date` | YES | Incorporation date |
| `company_age` | `String(50)` | YES | Company age |
| `registrar_of_companies` | `String(255)` | YES | ROC |
| `director_names` | `Text` | YES | Director names |
| `director_din` | `Text` | YES | Director DINs |
| `director_appointment_date`| `Date` | YES | Director appointment date |
| `address` | `Text` | YES | Registered address |
| `last_agm_date` | `Date` | YES | AGM date |
| `latest_revenue` | `BigInteger` | YES | Latest revenue |
| `latest_revenue_date` | `Date` | YES | Revenue date |
| `latest_balance_sheet_date`| `Date` | YES | Balance sheet date |
| `authorized_capital` | `BigInteger` | YES | Authorized capital |
| `paid_up_capital` | `BigInteger` | YES | Paid up capital |
| `main_activity_group_code` | `String(50)` | YES | Activity group code |
| `description_of_main_activity`| `Text`| YES | Main activity description |
| `business_activity_code` | `String(50)` | YES | Business activity code |
| `description_of_business_activity`| `Text` | YES | Business activity description |
| `data_source` | `String(100)` | YES | MCA / FalconBiz / Tracxn |
| `scraped_at` | `DateTime` | YES | Scraper execution timestamp |
| `raw_data` | `JSONB` | YES | Full raw scraper payload |
| `search_vector` | `TSVECTOR` | YES | Full-text search optimization |
| `is_active` | `Boolean` | NO | Default TRUE |
| `created_at` | `DateTime` | NO | Laravel timestamps |
| `updated_at` | `DateTime` | NO | Laravel timestamps |
| `deleted_at` | `DateTime` | YES | Soft delete |

*(All fields strictly follow the provided database schema)*

---

## 4. Target Sources & Data Mapping
Ensure the adapters in `src/common/adapter/` scrape data exclusively from these sources and map them to the DB columns above:
*   **Source 1 (Falcon Biz):** Base URL pattern `https://www.falconebiz.com/company/...`. Responsible for specific company fields AND all director details (`director_names`, `director_din`, `director_appointment_date`, `registration_number`, `current_status`).
*   **Source 2 (Tracxn):** Base URL pattern `https://tracxn.com/d/legal-entities/india/...`. Responsible for specific company fields (`latest_revenue`, `main_activity_group_code`, `description_of_main_activity`, `business_activity_code`, `description_of_business_activity`).
*   **Both Sources:** Shared fields (`cin`, `entity_type`, `incorporation_date`, `address`, `authorized_capital`, `paid_up_capital`, etc.) should be merged, prioritizing the most complete/recent data. Store the combined raw output in the `raw_data` JSONB column.

---

## 5. Implementation Directives
1.  **Unit of Work (`src/service/unit_of_work.py`):** Use the existing UOW to manage database transactions. Ensure that if a batch process fails on one record, it doesn't roll back the successfully scraped items in that batch.
2.  **File Generation:** Use Python's built-in file operations or `aiofiles` to generate the Markdown report in the `/reports` directory. Ensure the directory is created if it does not exist.
3.  **Error Handling & Logging:** Log validation failures or network errors explicitly in `src/common/base/logger.py`. The background batch task must silently catch individual `502` or `404` errors so the batch continues, but it must strictly document these failures in the final `.md` report.
```