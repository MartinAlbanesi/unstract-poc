# Postman collection — Unstract POC extraction APIs

Tests the 3 deployed extraction endpoints (factura, remito, carta de
porte) directly, outside of `client.py`.

## Import

1. Postman → Import → select both files:
   - `unstract-poc.postman_collection.json`
   - `unstract-poc.postman_environment.json`
2. Select the **"Unstract POC (local)"** environment (top-right dropdown).
3. Fill in `factura_api_key`, `remito_api_key`, `carta_de_porte_api_key`
   from your local `deployments/.env` (Manage Keys in Prompt Studio → API
   Deployments if you need to regenerate them). These are marked
   `"secret"` type in the environment — Postman masks them and excludes
   them from environment exports by default. **Never commit real values**
   into either JSON file in this folder.
4. Make sure the platform is up (`.\healthcheck.ps1` from the repo root)
   and reachable at `http://frontend.unstract.localhost` — not the
   remapped `:3100` port, which bypasses Traefik and 404s on
   `/deployment` paths (see `README.md` / `docs/ports.md`).

## Usage per document type

Each folder (Factura / Remito / Carta de Porte) has 2 requests, run in order:

1. **Submit** — multipart POST. Open the "Body" tab, click "Select File"
   on the `files` field, pick a sample PDF from `Documentos/Demo OCR 1`.
   Send. On success, a test script auto-saves `execution_id` into a
   collection variable — the Poll request already reads it, no manual
   copy-paste needed.
2. **Poll Status** — GET. Send repeatedly (Postman doesn't auto-poll) until
   the response body's `status` is `COMPLETED` (extracted JSON is in
   `message`), or `ERROR`/`STOPPED` (failure — check `message` for why).
   Typical extraction takes ~5-30s depending on document type and OCR load.

## Notes

- Sync deployment API is deprecated — this collection only uses the
  async submit+poll flow, matching `client.py`.
- The Carta de Porte API deployment name is `carta-porte` (hyphen), while
  its schema/env-var name is `carta_de_porte` (underscore) — intentional,
  see `deployments/deployment-config.md`.
- Response shapes (`message.execution_id` on submit, `status`/`message` on
  poll) were confirmed against the live API this session, not assumed from
  docs — see `docs/integration-guide.md` section 2.1 for the full contract.
