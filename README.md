# Multi-Property Executive Analytics & SEO Dashboard

A high-performance, minimalist Streamlit analytics engine and interactive dashboard tailored for tracking performance across three core brand properties: **BASLA**, **LILLI**, and **MOLECULE**.

---

## Key Features

- **Multi-Brand Isolation**: Strict data segregation between BASLA, LILLI, and MOLECULE properties.
- **Article Performance View**: Real-time GA4 engagement tracking with strict path validation (`is_valid_article()`) to isolate genuine content from store and utility pages.
- **SEO & Search Keyword Intelligence**: Search query metrics (clicks, impressions, CTR, position) integrated for deep search performance analysis.
- **SQLite Backed Engine**: Centralized local data pipeline (`analytics.db`) managed via `database.py` and `main.py`.

---

## Tech Stack

- **Frontend / Dashboard**: Streamlit
- **Data Engine**: SQLite, Pandas
- **APIs / Integration**: Google Analytics 4 (GA4) API, Google Search Console (GSC) API
- **Language**: Python 3.x

---

## Project Structure

```text
basla-analytics/
├── dashboard.py         # Multi-page Streamlit UI application
├── main.py              # Data ingestion and API fetch pipelines
├── database.py          # SQLite schema management & query handlers
├── requirements.txt     # Python dependencies
├── .gitignore           # Excludes local databases & service credentials
└── README.md            # Project documentation
```
