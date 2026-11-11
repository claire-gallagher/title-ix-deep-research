# Deploy (Cloud Run, from GitHub)

Every push to `main` builds with Google Cloud's buildpacks and deploys (entrypoint
`uvicorn app:app --host 0.0.0.0 --port $PORT`). The database isn't in the repo: it's read from a
Cloud Storage bucket mounted into the container.

1. Upload `data/eada.duckdb` to the **top level** of a bucket (public access prevented). Re-upload it
   after changing the build, `data/column_notes.json` or `data/nil_facts.csv`.
2. Put the Tavily key in Secret Manager as `tavily-api-key`: the key's value only, not the `.env` line.
3. In the service's *Containers, Volumes, Networking, Security*:

| Setting | Value |
|---|---|
| Volume | the bucket, **read-only**, mounted at `/data` |
| Env vars | `EADA_DB=/data/eada.duckdb` (leading `/`), `VERTEXAI_PROJECT=<project id>`, `CACHE_DIR=/tmp/cache` |
| Secret as env var | name `TAVILY_API_KEY`, secret `tavily-api-key`, version `latest` |
| Resources | 2 GiB memory, request timeout 900 s, max instances 1 (conversations live in memory) |
| Authentication | require authentication, IAP restricted to `columbia.edu` |

4. The service's runtime service account (the default compute account unless you pick another) needs
   **Secret Manager Secret Accessor** on the secret, **Storage Object Viewer** on the bucket and
   **Vertex AI User** on the project. A missing grant shows up as a failed deploy (secret) or an
   error on the first question (bucket, Vertex AI).

## Troubleshooting (service → Logs)

| Symptom | Cause |
|---|---|
| "No database" | volume not mounted at `/data`, `EADA_DB` missing its leading `/`, or the file isn't at the bucket's top level |
| Deploy fails: permission denied on secret | runtime service account lacks Secret Accessor on `tavily-api-key` |
| 403 from Vertex AI | service account lacks Vertex AI User, or `VERTEXAI_PROJECT` unset |
| 504 during research | request timeout not raised to 900 s |
| DuckDB can't open the file | it was built with a newer DuckDB than `uv.lock` pins: rebuild with this project's `.venv` |
