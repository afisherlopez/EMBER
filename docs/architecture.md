# EMBER Architecture

## Supported production architecture

EMBER has two independently deployed web processes:

- The Streamlit application runs on Streamlit Community Cloud.
- A small public Cloud Run service renders only burn-severity tiles.

Both processes read private objects from the `EMBER/` prefix in GCS. The app reads
Parquet catalog tables and case-study documents. The tiler reads the annual
burn-severity COG manifest and COGs. The browser requests public PNG tiles from
`/burn-severity/tiles/{z}/{x}/{y}.png`; no generic user-supplied raster URL is exposed.

```text
Browser
  |-- app pages/data --> Streamlit Community Cloud --> private GCS Parquet/PDFs
  `-- PNG map tiles --> Cloud Run burn-severity tiler --> private GCS COGs
```

## Runtime data flow

1. The app loads selector metadata and scalar metrics through `core/catalog.py`.
2. Utility, wildfire, and case-study views query precomputed pair overlaps and
   simplified geometry from Parquet.
3. General Insights aggregates the published overlap and wildfire tables.
4. Maps add a burn-severity tile layer using the years listed in the published
   manifest.
5. The tiler orders selected annual COGs newest-first and uses the first valid
   categorical pixel, then applies the fixed burn-severity colormap.

## Storage and credentials

- Production data remains private in GCS.
- Streamlit receives a service-account JSON through Streamlit Secrets.
- Cloud Run uses its attached service account through Application Default
  Credentials; no JSON key is stored in the container.
- Read-only views need `storage.objects.get`. The in-app admin editor additionally
  needs create/update access only for `EMBER/tables/` and `EMBER/backups/`.
- The public tiler is constrained to burn-severity assets resolved from the fixed
  manifest. CORS allows the deployed Streamlit origin.

## Local development

`scripts/run_local.sh` starts Streamlit and the same burn-severity tiler as two local
processes. `core/storage.py` switches both local and GCS paths through environment
settings. See `docs/deployment.md` for production setup.
