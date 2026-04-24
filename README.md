# Dynamic Purchasing Power Parity API

FastAPI service that prices a product from a base price, a client IP address, a local GeoIP MMDB country lookup, and a World Bank PPP snapshot.

## Run locally

1. Install dependencies:

```bash
python3 -m pip install -e '.[dev]'
```

2. Provide a GeoIP MMDB country database file and point `GEOIP_DB_PATH` at it.

3. Optionally refresh the bundled PPP dataset and GeoIP MMDB database from iplocate:

```bash
export IPLOCATE_API_KEY=your-api-key
ppp-api-refresh --geoip-output ./data/ip-to-country.mmdb --ppp-output ./dynamic_ppp_api/data/ppp_snapshot.json
```

4. Start the API:

```bash
export GEOIP_DB_PATH=/absolute/path/to/ip-to-country.mmdb
uvicorn dynamic_ppp_api.main:app --reload
```

## API

- `GET /v1/ppp-price?base_price=100&ip=8.8.8.8`
- `GET /healthz`
- `GET /readyz`

Interactive docs are available at `/docs` and `/redoc`.

## Deploy to Google Cloud Run

This repo includes [`cloudbuild.yaml`](/Users/nanaarkorful/Documents/Dynamic Purchasing Power Parity API/cloudbuild.yaml) for GitHub-triggered deployments to Cloud Run. The pipeline builds the existing Docker image, pushes it to Artifact Registry, and deploys a public Cloud Run service with these defaults:

- Region: `us-east1`
- Artifact Registry repository: `ppp-api`
- Cloud Run service: `dynamic-ppp-api`
- Memory: `512Mi`
- CPU: `1`
- Min instances: `0`
- Max instances: `10`
- Container port: `8000`

### 1. Set your Google Cloud defaults

```bash
export PROJECT_ID="your-gcp-project-id"
export REGION="us-east1"
export REPOSITORY="ppp-api"
export SERVICE_NAME="dynamic-ppp-api"

gcloud config set project "$PROJECT_ID"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
```

### 2. Enable the required services

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  cloudresourcemanager.googleapis.com
```

### 3. Create the Artifact Registry repository

```bash
gcloud artifacts repositories create "$REPOSITORY" \
  --repository-format=docker \
  --location="$REGION" \
  --description="Containers for the Dynamic PPP API"
```

### 4. Create the Cloud Run runtime service account

The build deploys Cloud Run with a dedicated runtime identity named `${SERVICE_NAME}-runner@$PROJECT_ID.iam.gserviceaccount.com`.

```bash
gcloud iam service-accounts create "${SERVICE_NAME}-runner" \
  --display-name="Dynamic PPP API runtime"
```

### 5. Grant Cloud Build permission to build and deploy

```bash
export CLOUDBUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
export RUNTIME_SA="${SERVICE_NAME}-runner@$PROJECT_ID.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/run.admin"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/artifactregistry.writer"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/storage.admin"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/logging.logWriter"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/cloudbuild.builds.editor"

gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/iam.serviceAccountUser"
```

### 6. Connect GitHub and create the trigger

1. In Google Cloud Console, open `Cloud Build` > `Triggers`.
2. Click `Connect repository` and authorize the GitHub repo that contains this project.
3. Create a trigger for pushes to your production branch, typically `main`.
4. Choose `Cloud Build configuration file (yaml or json)` and set the path to `cloudbuild.yaml`.
5. Keep the trigger region the same as the Cloud Run service region, `us-east1`.

If you want different service names or sizing later, update the substitutions in [`cloudbuild.yaml`](/Users/nanaarkorful/Documents/Dynamic Purchasing Power Parity API/cloudbuild.yaml) or override them in the trigger configuration.

### 7. Push to `main` to launch the first deploy

Each push to your production branch will:

1. Build the Docker image from the repo `Dockerfile`
2. Push it to Artifact Registry
3. Deploy a new Cloud Run revision

### 8. Verify the live service

After the first build finishes, open the Cloud Run service URL and verify:

```bash
curl https://YOUR_CLOUD_RUN_URL/healthz
curl https://YOUR_CLOUD_RUN_URL/readyz
curl "https://YOUR_CLOUD_RUN_URL/v1/ppp-price?base_price=100&ip=8.8.8.8"
```

You can also check:

- `https://YOUR_CLOUD_RUN_URL/docs`
- Logs in `Cloud Run` > your service > `Logs`
- Revisions in `Cloud Run` > your service > `Revisions`

### 9. Add low-cost guardrails

- Set a billing budget alert for the project
- Keep `min instances` at `0` to allow scale-to-zero
- Keep the initial `max instances` cap at `10` until you have traffic data
- Add a custom domain only after the base deployment is stable

## Notes

- The service validates both the GeoIP MMDB database and the PPP snapshot during startup.
- The refresh command builds the iplocate download URL from `IPLOCATE_API_KEY` and `IPLOCATE_VARIANT`, or uses `IPLOCATE_DOWNLOAD_URL` if you provide a full override.
- The bundled PPP snapshot is a starter dataset in the production schema. Run `ppp-api-refresh` before deployment to pull the latest World Bank data and replace it with a current snapshot.
- Cloud Run is configured to send traffic to container port `8000`, which matches the current Docker image.
