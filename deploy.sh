#!/usr/bin/env bash
# ==============================================================================
# Google Cloud Run Serverless Deployment Script
# ==============================================================================
# This script automates containerizing and deploying the E-Commerce Price Tracker 
# application to Google Cloud Run, securing secrets, and setting up Cloud Scheduler.

set -euo pipefail

# --- Configuration (Override via env variables or edit below) ---
PROJECT_ID="${GCP_PROJECT_ID:-}"
REGION="${GCP_REGION:-asia-south1}"       # Default to Mumbai
SERVICE_NAME="price-tracker"
REPO_NAME="price-tracker-repo"

echo "============================================="
echo "PriceGuard AI - Google Cloud Run Deployer"
echo "============================================="

# Verify gcloud CLI is authenticated and project is set
if [ -z "$PROJECT_ID" ]; then
    PROJECT_ID=$(gcloud config get-value project 2>/dev/null || echo "")
    if [ -z "$PROJECT_ID" ]; then
        echo "❌ Error: Google Cloud Project ID is not set."
        echo "Please set it with: export GCP_PROJECT_ID='your-project-id'"
        exit 1
    fi
fi

echo "🚀 Using Project ID: $PROJECT_ID"
echo "📍 Using Region:     $REGION"

# 1. Enable Required Google APIs
echo "🔑 Enabling necessary Google Cloud Services..."
gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    secretmanager.googleapis.com \
    cloudscheduler.googleapis.com \
    --project="$PROJECT_ID"

# 2. Create Artifact Registry Repository if not exists
echo "📦 Checking Artifact Registry..."
if ! gcloud artifacts repositories describe "$REPO_NAME" --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
    echo "Creating Artifact Registry Repository '$REPO_NAME'..."
    gcloud artifacts repositories create "$REPO_NAME" \
        --repository-format=docker \
        --location="$REGION" \
        --description="Docker repository for E-Commerce Price Tracker" \
        --project="$PROJECT_ID"
else
    echo "Artifact Registry repository already exists."
fi

# 3. Secure Secrets inside GCP Secret Manager
echo "🔒 Checking Secret Manager configurations..."
setup_secret() {
    local secret_name="$1"
    local secret_desc="$2"
    
    if ! gcloud secrets describe "$secret_name" --project="$PROJECT_ID" &>/dev/null; then
        echo "Creating secret '$secret_name'..."
        gcloud secrets create "$secret_name" --project="$PROJECT_ID" --replication-policy="automatic"
        
        echo "Enter value for $secret_desc:"
        read -rs secret_val
        echo -n "$secret_val" | gcloud secrets versions add "$secret_name" --data-file=- --project="$PROJECT_ID"
        echo "✅ Secret version added."
    else
        echo "Secret '$secret_name' already exists. Skip creation."
        echo "If you wish to update its value, run: echo -n 'VAL' | gcloud secrets versions add $secret_name --data-file=-"
    fi
}

setup_secret "TELEGRAM_BOT_TOKEN" "Telegram Bot Token (obtained from BotFather)"
setup_secret "DATABASE_URL" "Production database URL (e.g. postgresql+asyncpg://user:pass@host/dbname)"
setup_secret "API_KEY" "Secret token for secure Cron Scrape API trigger (leave empty to generate random)"

# Retrieve API key for Cloud Scheduler configuration
API_KEY_VAL=$(gcloud secrets versions access latest --secret="API_KEY" --project="$PROJECT_ID" 2>/dev/null || echo "")
if [ -z "$API_KEY_VAL" ]; then
    # Generate random API key if user skipped
    API_KEY_VAL=$(openssl rand -hex 16)
    echo -n "$API_KEY_VAL" | gcloud secrets versions add "API_KEY" --data-file=- --project="$PROJECT_ID"
    echo "Generated random API key for cron trigger."
fi

# 4. Build and Push Container Image using Cloud Build
IMAGE_TAG="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$SERVICE_NAME:latest"
echo "🛠️ Triggering Google Cloud Build for $IMAGE_TAG..."
gcloud builds submit --tag "$IMAGE_TAG" --project="$PROJECT_ID"

# 5. Deploy service to Google Cloud Run
echo "☁️ Deploying to Google Cloud Run..."
gcloud run deploy "$SERVICE_NAME" \
    --image="$IMAGE_TAG" \
    --region="$REGION" \
    --platform="managed" \
    --allow-unauthenticated \
    --memory="1Gi" \
    --cpu="1" \
    --timeout="300" \
    --set-env-vars="RUN_LOCAL_SCHEDULER=false" \
    --set-secrets="TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest,DATABASE_URL=DATABASE_URL:latest,API_KEY=API_KEY:latest" \
    --project="$PROJECT_ID"

# Get the URL of the deployed service
RUN_URL=$(gcloud run services describe "$SERVICE_NAME" --region="$REGION" --format="value(status.url)" --project="$PROJECT_ID")
echo "🌐 Live Web Application URL: $RUN_URL"

# 6. Configure Cloud Scheduler for Serverless Cron checks (every 15 minutes)
echo "📅 Configuring Cloud Scheduler cron task..."

# Create service account to authenticate scheduler to run invocations
SCHEDULER_SA="scheduler-run-invoker"
SCHEDULER_EMAIL="$SCHEDULER_SA@$PROJECT_ID.iam.gserviceaccount.com"

if ! gcloud iam service-accounts describe "$SCHEDULER_EMAIL" --project="$PROJECT_ID" &>/dev/null; then
    echo "Creating Service Account '$SCHEDULER_SA'..."
    gcloud iam service-accounts create "$SCHEDULER_SA" \
        --description="Service account for Cloud Scheduler to invoke Price Tracker Scraper" \
        --display-name="Price Tracker Scheduler Caller" \
        --project="$PROJECT_ID"
fi

# Assign Run Invoker roles to scheduler SA
gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
    --member="serviceAccount:$SCHEDULER_EMAIL" \
    --role="roles/run.invoker" \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --platform="managed" &>/dev/null

# Create or replace the cron job
JOB_NAME="price-tracker-15m-cron"
echo "Setting up Scheduler Job: $JOB_NAME..."
if gcloud scheduler jobs describe "$JOB_NAME" --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
    gcloud scheduler jobs delete "$JOB_NAME" --location="$REGION" --project="$PROJECT_ID" --quiet
fi

gcloud scheduler jobs create http "$JOB_NAME" \
    --schedule="*/15 * * * *" \
    --uri="$RUN_URL/api/scrape" \
    --http-method="POST" \
    --headers="X-API-Key=$API_KEY_VAL" \
    --oidc-service-account-email="$SCHEDULER_EMAIL" \
    --location="$REGION" \
    --project="$PROJECT_ID"

echo "============================================="
echo "🎉 DEPLOYMENT COMPLETED SUCCESSFULLY!"
echo "============================================="
echo "📱 UI Dashboard:      $RUN_URL/"
echo "⚙️ Swagger API Docs:  $RUN_URL/docs"
echo "🔑 X-API-Key (Cron):  $API_KEY_VAL"
echo "============================================="
