# Prompt Studio Deployment Config

Manual UI steps performed in Prompt Studio (v0.186.1) to configure the 3
extraction schemas and their API deployments. UI-driven, not scriptable —
this doc is the versioned record of what was clicked, per task 2.2/2.3/2.4.

## Adapters (Settings → Adapters)

| Adapter | Type | Provider | Name |
|---|---|---|---|
| LLM | LLM | Google Gemini (`gemini-flash`) | `gemini-flash-poc` |
| Embedding | EMBEDDING | Google Gemini (embedding model) | `geminiembedding-poc` |
| Vector DB | VECTOR_DB | Qdrant (bundled, `unstract-vector-db:6333`) | `qdrant-poc` |
| Text Extractor | X2TEXT | LLMWhisperer (free tier) | `llmwhisperer-poc` |

Credentials are entered directly in the adapter form and encrypted by the
platform using `ENCRYPTION_KEY` (see `bootstrap.ps1`) — never stored in
any file in this repo. LLMWhisperer free tier is capped at **100 pages/day**.

Org-level defaults (Settings → Complete Setup) point to these 4 adapters.

## Prompt Studio Projects

One project per document type, each using the same 4 adapters above.
Each project has a single combined-extraction prompt (not one prompt per
field) that returns the full JSON object matching the corresponding
`schemas/*.yaml` contract.

| Project | Sample doc used | Schema |
|---|---|---|
| `factura-poc` | `Documentos/.../Factura_C_1.pdf` (real text layer) | `schemas/factura.yaml` |
| `remito-poc` | `Documentos/...` (scanned, LLMWhisperer OCR) | `schemas/remito.yaml` |
| `carta-porte-poc` | `Documentos/...` (scanned, LLMWhisperer OCR) | `schemas/carta_de_porte.yaml` |

Exported project definitions: `deployments/factura-poc.json`,
`deployments/remito-poc.json`, `deployments/carta-porte-poc.json`.

## API Deployments (Deploy as API)

| Type | Deployment path | API name |
|---|---|---|
| Factura | `/deployment/api/mock_org/factura/` | `factura` |
| Remito | `/deployment/api/mock_org/remito/` | `remito` |
| Carta de Porte | `/deployment/api/mock_org/carta-porte/` | `carta-porte` |

Each deployment has its own API key generated via "Manage Keys" in the
API Deployments screen. Keys are bearer tokens for `client.py` (Phase 3) —
never committed; set them via the env vars in `deployments/.env.example`.

Full URL pattern: `http://frontend.unstract.localhost/deployment/api/mock_org/{api_name}/`
(must go through Traefik on port 80 — see the `frontend.unstract.localhost`
note in the root `README.md`; the remapped `localhost:3100` bypass does not
route `/deployment` paths).
