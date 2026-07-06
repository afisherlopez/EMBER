# EMBER

This guide walks you through running EMBER on your own device (for now, assuming Mac), step by step. **No prior terminal experience needed** — just follow along and copy-paste each command. There's a [for developers](#for-developers) section at the bottom for those who want more technical details.

---

## Run EMBER on your computer

Everything below happens in an app called **Terminal**. Don't worry if you've never used it, you'll just paste in commands one at a time.

### What you need first: Python and Git

EMBER needs two free tools: **Python** (to run the app) and **Git** (to copy the code onto your computer). Let's check for each — you may already have them.

**1. Check Python** (version 3.11 or 3.12). Open the **Terminal** app (press `Cmd` + `Space`, type `Terminal`, press `Return`), then copy the line below, paste it in (`Cmd` + `V`), and press `Return`:

```bash
python3 --version
```

- If you see `Python 3.11.x` or `Python 3.12.x`, you're set.
- If you see `Python 3.13` or higher, or a "command not found" error, install Python **3.12** from [python.org/downloads/release/python-3128](https://www.python.org/downloads/release/python-3128/). Run the installer, then close and reopen Terminal.

**2. Check Git.** In the same Terminal window, run:

```bash
git --version
```

- If you see something like `git version 2.39.x`, you're set.
- If a small window pops up offering to install "command line developer tools," click **Install** and wait a few minutes — that includes Git. When it finishes, run `git --version` again to confirm.

> **How to run any command below:** copy it, click in the Terminal window, paste, and press `Return`. Do them one at a time, and wait for each to finish.

### Step 1: Copy EMBER onto your computer

Right now the EMBER code lives online. This command copies it onto your computer, into a new folder called **EMBER** in your home folder. You only do this once.

Paste this into Terminal and press `Return`:

```bash
git clone https://github.com/afisherlopez/EMBER.git
```

It prints a few lines as it downloads, then returns you to the prompt. The code is now on your device.

### Step 2: Go to the EMBER folder

Terminal needs to be "inside" the EMBER folder you just downloaded. The easiest way:

1. Type `cd` followed by a **single space** (don't press Return yet):

   ```bash
   cd 
   ```

2. Open **Finder**, go to your home folder, find the **EMBER** folder, and **drag it onto the Terminal window**. Terminal fills in the folder's location for you.
3. Now press `Return`.

Your Terminal prompt now shows you're inside the EMBER folder.

### Step 3: Create a virtual environment (one time only)

A "virtual environment" is a private, self-contained space for EMBER's building blocks, so it won't interfere with anything else on your computer. Create it once:

```bash
python3 -m venv ember-venv
```

This takes a few seconds and creates an `ember-venv` folder. You won't need to do this again.

### Step 4: Turn on the virtual environment

Do this **every time** you open a new Terminal to work on EMBER:

```bash
source ember-venv/bin/activate
```

You'll know it worked because your prompt now starts with `(ember-venv)`.

### Step 5: Install what EMBER needs (one time only)

This downloads all the building blocks EMBER uses. It may take a few minutes the first time — that's normal.

```bash
pip install -r requirements.txt
```

Wait until it finishes and you get your prompt back. You only need to do this once.

### Step 6: Start the app

```bash
bash scripts/run_local.sh
```

The first time, this sets up a small built-in sample dataset (so you don't need any passwords or accounts), then starts the app. After a few seconds, EMBER should **open automatically in your web browser**.

If it doesn't open on its own, open your browser and go to:

```
http://localhost:8501
```

That's it — you're running EMBER! 🎉

### Step 7: Stop the app

When you're done, click on the Terminal window and press `Control` + `C`. This shuts everything down cleanly.

---

## Running it again later

Once you've done the one-time setup above, starting EMBER again is quick (no need to download it again). Open Terminal and:

1. Type `cd ` and drag the EMBER folder onto Terminal, then press `Return` (like [Step 2](#step-2-go-to-the-ember-folder)).
2. Run these two lines:

   ```bash
   source ember-venv/bin/activate
   bash scripts/run_local.sh
   ```

## If something goes wrong

- **`command not found: python3`** — Python isn't installed. See [What you need first: Python and Git](#what-you-need-first-python-and-git).
- **`command not found: git`** — Git isn't installed. See [What you need first: Python and Git](#what-you-need-first-python-and-git).
- **Your prompt doesn't show `(ember-venv)`** — run `source ember-venv/bin/activate` again (Step 4). Every command must be run with the environment turned on.
- **`No such file or directory`** — you're probably not in the EMBER folder. Redo [Step 2](#step-2-go-to-the-ember-folder).
- **The browser page won't load** — give it a few more seconds, then refresh `http://localhost:8501`. Make sure the Terminal window is still running (you didn't press `Control` + `C`).
- **Still stuck?** Close Terminal completely, reopen it, and start again from [Running it again later](#running-it-again-later).

---
---

## For developers

EMBER is a read-only Streamlit dashboard over precalculated assets (Parquet and GeoTIFFs), with dynamic raster tiles served by TiTiler. `scripts/run_local.sh` (used above) wires the two processes together against the bundled sample data.

### Architecture

```text
                 +---------------------------+
                 |      Streamlit app        |
                 |  core/app/streamlit_app   |
                 +------------+--------------+
                              |
                   (feature + map requests)
                              |
          +-------------------+---------------------+
          |                                         |
+---------v-----------------+          +------------v-------------+
| DuckDB catalog layer      |          | TiTiler FastAPI service  |
| core/catalog.py           |          | core/tiler/main.py       |
| spatial + gcsfs (gs://)   |          | /cog/* endpoints         |
+------------+--------------+          +------------+-------------+
             |                                      |
             | reads Parquet/GeoParquet             | reads COGs
             |                                      |
     +-------v--------------------------------------v-------+
     |         Storage backend (local | gcs)               |
     |         core/storage.py                             |
     +-----------------------------------------------------+
```

### Manual local run (two processes)

`run_local.sh` is the shortcut. To run the pieces by hand instead (with the venv active):

```bash
python scripts/bootstrap_sample_data.py                    # one-time: build the sample dataset
uvicorn core.tiler.main:app --host 0.0.0.0 --port 8000     # terminal 1: tiler
TILER_URL=http://localhost:8000 streamlit run core/app/streamlit_app.py --server.port 8501  # terminal 2: app
```

- App: `http://localhost:8501` · Tiler health: `http://localhost:8000/healthz`
- The tiler mounts TiTiler under `/cog` (e.g. `/cog/WebMercatorQuad/tilejson.json`) and is restricted to datasets under the configured storage prefix.
- `run_local.sh` accepts `APP_PORT` (default 8501) and `TILER_PORT` (default 8000) overrides.

### Previewing real GCS data locally

Copy `.env.example` to `.env` and set the GCS backend:

```bash
EMBER_STORAGE_BACKEND=gcs
GCS_BUCKET=data_main_gcs
GCS_PREFIX=EMBER
GOOGLE_APPLICATION_CREDENTIALS=./secrets/ember-sa.json   # read-only service-account JSON
```

A single read-only service account authenticates every reader: DuckDB (Parquet via `gcsfs`), GDAL/TiTiler (COGs via native `gs://`), and the storage client — no HMAC keys. On Cloud Run, leave `GOOGLE_APPLICATION_CREDENTIALS` blank and attach the service account (Application Default Credentials).

> The reader SA is scoped to the `EMBER/` prefix via a bucket IAM **condition**. GCS conditions must use the full object-resource form, e.g. `resource.name.startsWith("projects/_/buckets/data_main_gcs/objects/EMBER/")` — a bare `EMBER/` prefix matches nothing and denies all reads. Object reads (`storage.objects.get`) work under this condition; `storage.objects.list` does not, and the app only needs `get`.

### Deploy to Cloud Run

Two services (app + tiler) build from one `Dockerfile`; `scripts/entrypoint.sh` selects the process via the `SERVICE` env var and binds to `$PORT`.

One-time setup:

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
gcloud artifacts repositories create ember --repository-format=docker --location=us-central1
```

Deploy (builds, deploys tiler + app, wires `TILER_URL` and the tiler's `CORS_ORIGINS`):

```bash
PROJECT_ID=data-gcp-main \
GCS_BUCKET=data_main_gcs \
GCS_PREFIX=EMBER \
SERVICE_ACCOUNT=ember-reader@data-gcp-main.iam.gserviceaccount.com \
./scripts/deploy_cloudrun.sh
```

Optional overrides: `REGION` (default `us-central1`), `REPO`, `TILER_SERVICE`, `APP_SERVICE`, `IMAGE_TAG`. The public app and tiler read the bucket server-side; the bucket itself stays private.

**Public access:** the script requests `--allow-unauthenticated`, but setting that binding needs `run.services.setIamPolicy` (`roles/run.admin` or Owner) — `roles/editor` can deploy but cannot open a service to the public. To make the services reachable without a Google identity token, an Owner runs:

```bash
gcloud run services add-iam-policy-binding ember-app   --region=us-central1 --member=allUsers --role=roles/run.invoker --project=data-gcp-main
gcloud run services add-iam-policy-binding ember-tiler --region=us-central1 --member=allUsers --role=roles/run.invoker --project=data-gcp-main
```

To preview a private service in your browser without opening it publicly:
`gcloud run services proxy ember-app --region=us-central1 --project=data-gcp-main`, then open `http://localhost:8080`.

### Tests

```bash
pip install pytest        # or: pip install -e ".[dev]"
pytest
```

### Extensibility contract

- New metric: add to `config/metrics.yaml` and insert rows into `scalar_metrics` or `raster_assets`.
- New profile: add to `config/profiles.yaml`.
- New utility/wildfire: add geometry rows + pair/metric rows.

Core code under `core/` should not change for any of these.

### Glossary

- **COG:** Cloud-Optimized GeoTIFF, designed for ranged tile reads.
- **Tiler:** service that serves map tiles from source rasters on demand.
- **Tidy schema:** one row per measurement with `metric_key` instead of one column per metric.
- **Source area:** utility watershed / source-water geometry used in overlap analysis.
