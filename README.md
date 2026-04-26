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

If you only want to refresh the PPP snapshot and skip the GeoIP download:

```bash
ppp-api-refresh --skip-geoip --ppp-output ./dynamic_ppp_api/data/ppp_snapshot.json
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
- Secret Manager secret: `iplocate-api-key`
- IPLocate variant: `daily`
- Memory: `512Mi`
- CPU: `1`
- Min instances: `0`
- Max instances: `10`
- Container port: `8000`

Before Cloud Build can deploy successfully, it must download the GeoIP MMDB file into the build workspace. The build now does that automatically using an `IPLOCATE_API_KEY` stored in Secret Manager. The MMDB file stays out of Git, but it is still baked into the container image during the build.

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

### 5. Create the Secret Manager secret for the MMDB download

```bash
printf '%s' 'YOUR_IPLOCATE_API_KEY' | \
gcloud secrets create iplocate-api-key \
  --data-file=-
```

If the secret already exists, add a new version instead:

```bash
printf '%s' 'YOUR_IPLOCATE_API_KEY' | \
gcloud secrets versions add iplocate-api-key \
  --data-file=-
```

### 6. Grant Cloud Build permission to build, deploy, and read the secret

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

gcloud secrets add-iam-policy-binding iplocate-api-key \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/secretmanager.secretAccessor"

gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/iam.serviceAccountUser"
```

### 7. Connect GitHub and create the trigger

1. In Google Cloud Console, open `Cloud Build` > `Triggers`.
2. Click `Connect repository` and authorize the GitHub repo that contains this project.
3. Create a trigger for pushes to your production branch, typically `main`.
4. Choose `Cloud Build configuration file (yaml or json)` and set the path to `cloudbuild.yaml`.
5. Keep the trigger region the same as the Cloud Run service region, `us-east1`.

If you want different service names or sizing later, update the substitutions in [`cloudbuild.yaml`](/Users/nanaarkorful/Documents/Dynamic Purchasing Power Parity API/cloudbuild.yaml) or override them in the trigger configuration.

The build config also supports these substitutions:

- `_IPLOCATE_SECRET_NAME`, default `iplocate-api-key`
- `_IPLOCATE_VARIANT`, default `daily`

Only change those if you intentionally renamed the secret or want a different IPLocate feed variant.

### 8. Push to `main` to launch the first deploy

Each push to your production branch will:

1. Download `dynamic_ppp_api/data/ip-to-country.mmdb` from IPLocate using Secret Manager
2. Build the Docker image from the repo `Dockerfile`
3. Push it to Artifact Registry
4. Deploy a new Cloud Run revision

### 9. Verify the live service

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

If you fixed a previous failed deploy, re-run the trigger or push a new commit after the secret and IAM access are in place.

### 10. Add low-cost guardrails

- Set a billing budget alert for the project
- Keep `min instances` at `0` to allow scale-to-zero
- Keep the initial `max instances` cap at `10` until you have traffic data
- Add a custom domain only after the base deployment is stable

## Weekly PPP Refresh Automation

This repo includes the GitHub Actions workflow [`weekly-ppp-refresh.yml`](/Users/nanaarkorful/Documents/Dynamic Purchasing Power Parity API/.github/workflows/weekly-ppp-refresh.yml). It runs every Monday at `13:00 UTC`, refreshes `dynamic_ppp_api/data/ppp_snapshot.json`, commits the updated snapshot back to `main`, and lets your existing Cloud Build trigger redeploy the service.

The weekly workflow only refreshes the PPP snapshot. The GeoIP MMDB file is still downloaded at build time by Cloud Build, so the GitHub workflow does not need the IPLocate API key.
The PPP refresh now defaults to the World Bank `PA.NUS.GDP.PLI` indicator and converts that price-level index back into the ratio scale used by the API.

To use the weekly refresh:

1. Push the workflow file to GitHub.
2. In GitHub, open `Settings` > `Actions` > `General`.
3. Make sure GitHub Actions is enabled for the repository.
4. Under workflow permissions, allow `Read and write permissions` so the workflow can commit the refreshed snapshot.
5. If `main` is branch-protected, allow GitHub Actions to push to it or adjust the workflow to commit through a pull request instead.

You can also run it manually from `GitHub` > `Actions` > `Weekly PPP Refresh` > `Run workflow`.

## Notes

- The service validates both the GeoIP MMDB database and the PPP snapshot during startup.
- The refresh command builds the iplocate download URL from `IPLOCATE_API_KEY` and `IPLOCATE_VARIANT`, or uses `IPLOCATE_DOWNLOAD_URL` if you provide a full override.
- The bundled PPP snapshot is a starter dataset in the production schema. Run `ppp-api-refresh` before deployment to pull the latest World Bank data and replace it with a current snapshot.
- PPP snapshot refreshes now default to the World Bank `PA.NUS.GDP.PLI` indicator and convert that index to `price_level_ratio` by dividing by `100`.
- If the World Bank API changes again or returns an invalid payload, the weekly workflow fails clearly and leaves the last committed snapshot untouched.
- Use `--skip-geoip` when you only want to refresh the PPP snapshot without downloading the MMDB file.
- Cloud Build downloads the GeoIP MMDB file from IPLocate at build time using Secret Manager, so the MMDB file does not need to be committed to Git.
- Cloud Run is configured to send traffic to container port `8000`, which matches the current Docker image.
