# E-Commerce Price Tracker App

A resilient, multi-platform, serverless e-commerce tracker built using **FastAPI**, **SQLAlchemy Async**, **Playwright**, and **Telegram Bot Integration**. It monitors price fluctuations and inventory stock changes on Amazon.in, Flipkart.com, Myntra.com, and Meesho.com, and sends alerts using structured Telegram MarkdownV2 formatting.

---

## Features
- **FastAPI Core**: A RESTful backend and a premium, dark-mode glassmorphic single-page dashboard.
- **Resilient Browser Crawler (Playwright)**: Employs a dual-layer strategy:
  1. *Primary*: Structured JSON-LD schema parsing.
  2. *Fallback*: Resilient CSS Selectors with User-Agent spoofing.
- **Alert State Engine**: Evaluates Target Price breaches (with no-repeat logic), general Price Drops, and Back-in-Stock status changes.
- **Telegram Bot API**: Sends styled alerts with strict MarkdownV2 formatting.
- **GCP Native Deployment**: Easy serverless container deployment using `deploy.sh` or Terraform to Google Cloud Run, backed by Secret Manager and Cloud Scheduler.

---

## Directory Structure
```
price-alert/
├── Dockerfile                  # Optimized, multi-stage Docker build
├── README.md                   # System user manual
├── requirements.txt            # Python dependencies
├── deploy.sh                   # Automation shell script using gcloud CLI
├── main.tf                     # Declared infrastructure using Terraform
├── src/
│   ├── config.py               # Env settings & validations
│   ├── database.py             # SQLite/PostgreSQL Async Engine setup
│   ├── models.py               # Products DB model
│   ├── schemas.py              # Pydantic schemas for API inputs
│   ├── scraper.py              # Playwright scraper logic
│   ├── alerts.py               # Price and Stock change alerts
│   ├── notifier.py             # Telegram formatters & communications
│   ├── scheduler.py            # Local APScheduler (optional)
│   └── main.py                 # FastAPI application routes & UI dashboard
└── tests/
    ├── conftest.py             # Test fixtures (sqlite memory db, mock scraper)
    ├── test_api.py             # API route tests
    ├── test_scraper.py         # Crawler unit tests
    ├── test_alerts.py          # Alert state transitions tests
    └── test_notifier.py        # Telegram template unit tests
```

---

## Getting Started

### 1. Telegram Bot Setup
1. Message [@BotFather](https://t.me/BotFather) on Telegram and type `/newbot`.
2. Follow prompts to get your **HTTP API Token** (e.g. `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`).
3. Search for your bot username in Telegram and click **Start**.
4. To find your **Chat ID**, message [@userinfobot](https://t.me/userinfobot) or search your bot updates.

### 2. Local Setup
Ensure Python 3.10+ is installed.

```bash
# Clone/move into project directory
cd "c:\price alert"

# Create a virtual environment
python -m venv venv
venv\Scripts\activate

# Install requirements
pip install -r requirements.txt

# Install Playwright browser dependencies
playwright install chromium
```

### 3. Running Locally
Create a `.env` file in the project root:
```env
TELEGRAM_BOT_TOKEN="your_bot_token_here"
DATABASE_URL="sqlite+aiosqlite:///./tracker.db"
RUN_LOCAL_SCHEDULER="true"
SCRAPE_INTERVAL_MINUTES=15
```

Start the local server:
```bash
uvicorn src.main:app --reload
```
Open **`http://localhost:8000`** in your browser to view the beautiful dashboard. You can access the Swagger documentation at `http://localhost:8000/docs`.

---

## Running Automated Tests
The test suite utilizes `pytest` with an in-memory SQLite database, verifying all endpoints, scrapers, and alerting conditions without initiating real browser sessions.

```bash
python -m pytest -v
```

---

## Docker Containerization
Build and run the container locally:

```bash
# Build
docker build -t price-tracker .

# Run
docker run -p 8080:8080 \
  -e TELEGRAM_BOT_TOKEN="your_bot_token" \
  -e DATABASE_URL="sqlite+aiosqlite:///./tracker.db" \
  -e RUN_LOCAL_SCHEDULER="true" \
  price-tracker
```

---

## Cloud Deployment (Google Cloud Run)

### Option A: Shell Script (Recommended)
Make sure you are authenticated with the `gcloud` CLI.
```bash
chmod +x deploy.sh
export GCP_PROJECT_ID="your-gcp-project-id"
./deploy.sh
```
This script:
1. Enables Cloud Run, Cloud Build, Secret Manager, and Cloud Scheduler.
2. Builds and pushes the Docker container using Cloud Build.
3. Sets up safe mounts for secrets from Secret Manager.
4. Generates an `API_KEY` for secure webhook triggers.
5. Deploys the service serverless on Cloud Run.
6. Sets up Cloud Scheduler to hit `/api/scrape` every 15 minutes to run updates.

### Option B: Terraform
1. Initialize Terraform:
   ```bash
   terraform init
   ```
2. Provision resource structure:
   ```bash
   terraform apply -var="project_id=your-gcp-project-id"
   ```
*(Note: You'll need to upload secret values into Secret Manager manually after deployment or edit the variables to feed secret configurations.)*
