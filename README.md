# Unstract OSS Extraction POC

Demo proving Unstract Open Source Edition extracts structured JSON from
Argentine documents (facturas, remitos, cartas de porte) via LLMWhisperer
OCR + Gemini LLM, served as a REST API and called from a Python client.

> Status: scaffold + platform bootstrap only (Work Unit 1). Prompt Studio
> schemas/deployments and the Python client land in follow-up PRs.

## Pinned release

`unstract/` is a gitignored, vendored shallow clone of
[Zipstack/unstract](https://github.com/Zipstack/unstract), pinned to
**`v0.186.1`** (never `latest`). The tag is set in `bootstrap.ps1` and
reproduced by cloning — it never enters this repo's git history.

## Requirements

- Windows with Docker Desktop (12GB+ RAM allocated), no WSL2 distro required
- Git, Python 3 on `PATH`
- Port 80 free (plus the platform's other ports — see `bootstrap.ps1`)

## Quick start

```powershell
.\bootstrap.ps1          # clone pinned tag, set up env files, docker compose up -d
.\healthcheck.ps1 -Wait 600   # poll until healthy (first boot can take a while)
```

Then visit `http://localhost:3100` (login: `unstract` / `unstract`).

Safe to re-run `bootstrap.ps1` at any time — every step is guarded and a
rerun on an already-running platform is a no-op.

## Rollback

```powershell
docker compose -f unstract/docker/docker-compose.yaml down -v
Remove-Item -Recurse -Force unstract
```

Then delete the generated `.env` files under `unstract/` (removed along with
the directory above) and re-run `bootstrap.ps1` to restore.

## License note

Unstract is AGPL-3.0 licensed. This repo vendors it unmodified for POC
purposes only; see upstream license before any production reuse.

## Layout

```
unstract-poc/
├── bootstrap.ps1               # platform bring-up (pinned tag, idempotent)
├── healthcheck.ps1             # per-service readiness probe
├── docker-compose.override.yaml # host port remap for this machine
├── unstract/                   # gitignored vendored clone (pinned tag)
├── schemas/                    # extraction field contracts (WU2)
├── deployments/                # exported Prompt Studio project + config (WU2)
├── client/                     # Python extraction client + tests (WU3)
└── Documentos/                 # demo document corpus
```

<!-- TODO (task 4.1, PR 3): setup/keys/demo runbook, docs/ports.md link. -->
