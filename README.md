# Mom's Recipes

A lightweight recipe website that syncs from a Dropbox folder, parses `.txt`, `.docx`,
and `.pdf` files, tags them with an LLM, and serves them with full-text search and tag filtering.

## Stack

- **FastAPI** + Jinja2 + HTMX — web layer
- **SQLite** with FTS5 — database + full-text search
- **APScheduler** — built-in polling job (no cron needed)
- **Dropbox Python SDK** — file sync
- **pdfplumber** / **mammoth** / **odfpy** / **textract** — file parsing (PDF, DOCX, ODT, TXT, and more)
- **OpenAI** / **Anthropic** — LLM tagging (configurable provider)
- **uv** — dependency management
- **nox** — build pipeline (lint → typecheck → test → docker)
- **ruff** — linting + formatting
- **mypy** — type checking (strict)
- **pytest** — unit tests with coverage

---

## Project structure

```
recipes/
├── src/recipes/         # application source
│   ├── main.py          # FastAPI app + scheduler
│   ├── poller.py        # Dropbox sync
│   ├── parsers.py       # docx / pdf / odt / txt extraction
│   ├── tagger.py        # LLM structured tagging
│   ├── models.py        # Pydantic models
│   └── db.py            # SQLite schema + queries
├── tests/               # pytest test suite
├── static/              # CSS
├── templates/           # Jinja2 HTML
├── pyproject.toml       # dependencies + tool config
├── noxfile.py           # build pipeline
├── Dockerfile           # uv-based multi-stage build
└── docker-compose.yml   # production container
```

---

## Prerequisites

```bash
# Install uv (fast Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install nox via uv (globally, not in the project)
uv tool install nox
```

---

## Setup

```bash
cp .env.example .env
# Fill in DROPBOX_REFRESH_TOKEN, DROPBOX_APP_KEY, DROPBOX_APP_SECRET
# Configure LLM_PROVIDER (openai or anthropic), LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

# Install all dependencies (including dev)
uv sync --group dev
```

---

## Full build (lint + typecheck + test + docker image)

```bash
nox
```

This runs in order and stops on the first failure:
1. **lint** — ruff check + format verification
2. **typecheck** — mypy strict
3. **test** — pytest with coverage (fails if < 70%)
4. **docker** — builds the image only if all above pass

Individual sessions:

```bash
nox -s lint           # ruff only
nox -s fmt            # auto-fix formatting in place
nox -s typecheck      # mypy only
nox -s test           # pytest only
nox -s docker         # docker build only
```

---

## Running locally (without Docker)

```bash
uv sync --group dev
source .env    # or: export $(cat .env | xargs)
uvicorn recipes.main:app --port 8000 --reload
```

---

## Deploying

```bash
nox -s docker     # builds and tags the image as recipes:latest
docker compose up -d
docker compose logs -f
```

Your Nginx block (add to your existing config):

```nginx
location / {
    proxy_pass         http://127.0.0.1:8000;
    proxy_set_header   Host $host;
    proxy_set_header   X-Real-IP $remote_addr;
}
```

---

## Dropbox setup

1. Go to https://www.dropbox.com/developers/apps
2. Create app → Scoped access → Full Dropbox
3. Permissions: enable `files.content.read` + `files.metadata.read`
4. For production (recommended), use OAuth2 refresh tokens:
   - Authorize: `https://www.dropbox.com/oauth2/authorize?client_id=YOUR_APP_KEY&response_type=code&token_access_type=offline`
   - Exchange code for tokens:
     ```bash
     curl -X POST https://api.dropbox.com/oauth2/token \
       -d "code=YOUR_CODE&grant_type=authorization_code&client_id=YOUR_APP_KEY&client_secret=YOUR_APP_SECRET"
     ```
   - Copy the `refresh_token` from the response
5. Fill in `.env`: `DROPBOX_REFRESH_TOKEN`, `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`

Alternative: static token (expires after ~4 hours, not recommended for production)

---

## Adding a recipe

Drop any `.txt`, `.docx`, `.pdf`, or `.odt` file into the Dropbox folder.
The poller (running inside the app) picks it up on the next interval and it appears on the site automatically.
No special format required — the LLM extracts everything.

You can optionally filter files by pattern using `DROPBOX_FILE_FILTER` in `.env` (e.g., `BOEUF*` for beef recipes only, `*.odt` for ODT files only).
