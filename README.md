# CNA News — 3 Apps

Three standalone apps that scrape, display, and let you chat with Channel NewsAsia news.

## Apps

| App | Script | Port | Purpose |
|-----|--------|------|---------|
| 1. Fetcher | `python fetch_news.py` | — | Batch-scrape CNA + summarise with DeepSeek |
| 2. Viewer  | `python viewer_server.py` | 8001 | Browse today's news by section |
| 3. Chat    | `python chat_server.py`  | 8002 | Chat with the news as a knowledge base |

All three apps share a single `news.db` SQLite database.

## Setup

### 1. Prerequisites
- Python 3.10+
- A [DeepSeek API key](https://platform.deepseek.com/)

### 2. Install dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure
```bash
cp .env.example .env
# Edit .env and set your DEEPSEEK_API_KEY
```

### 4. Run

**Step 1 — Fetch news** (run once, then schedule daily):
```bash
python fetch_news.py
```

**Step 2 — View news:**
```bash
python viewer_server.py
# Open http://localhost:8001
```

**Step 3 — Chat:**
```bash
python chat_server.py
# Open http://localhost:8002
```

## Scheduling (macOS/Linux)

Add to crontab (`crontab -e`) to run the fetcher every morning at 7 AM SGT:
```
0 23 * * * cd /path/to/project && /path/to/.venv/bin/python fetch_news.py >> fetch.log 2>&1
```
(7 AM SGT = 23:00 UTC the previous day)

## Sections Scraped
- Homepage (Top Stories)
- Singapore
- Asia
- World
- Business
- Sport

## Data Retention
Articles older than 7 days are automatically deleted each time `fetch_news.py` runs.

## Architecture

```
news.db (SQLite)
    ↑
    │ read/write
    ├── fetch_news.py   (App 1 — runs standalone, no server)
    │       ├── scraper.py
    │       └── summariser.py → DeepSeek API
    │
    ├── viewer_server.py  (App 2 — FastAPI, port 8001)
    │       └── news_viewer/  (HTML + CSS + JS)
    │
    └── chat_server.py    (App 3 — FastAPI, port 8002)
            ├── chat.py → DeepSeek API
            └── chat_app/   (HTML + CSS + JS)
```
