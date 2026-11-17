# 📘 ExamNotes

Upload a PDF of lecture notes and get back **short revision notes grouped by
topic** and **likely exam questions with model answers**, each tied to the page
it came from.

Built as a university school project: one FastAPI process, one SQLite file, one
HTML page. No accounts, no job queue, no build step.

---

## What it does

1. You drop a PDF of lecture notes on the page and pick how many questions you
   want, which types, and how hard.
2. The server validates the file, pulls the text out page by page, splits it
   into chunks of a few pages, and asks an AI model what the key points and
   "testable items" (definitions, formulas, comparisons, processes, worked
   examples, learning objectives) are on each page.
3. Those are merged twice: once into notes grouped by topic, once into exam
   questions with model answers.
4. You get notes, questions with hidden answers and page numbers, a follow-up
   chat box ("make the notes shorter", "10 more questions on chapter 3"),
   a copy button and a Markdown download, split across three tabs. Past results
   are kept in your browser's localStorage.

The page is styled as a sheet of ruled paper — serif for reading, monospace for
labels, a red margin rule and a highlighter for emphasis — and has a paper/dark
theme toggle that follows the system setting until you change it.

The uploaded PDF is never written to disk. The extracted text and the result
are stored in SQLite and **deleted after 24 hours**.

---

## Architecture

```
Browser (one HTML page)
   │  POST /api/documents  (multipart)
   ▼
FastAPI  ──►  rate limit (5/hour/IP, in memory)
              │
              ▼
          pipeline.py ── all of this inside the request:
              1. validate     pdf.py        signature + 20 MB + 100 pages
              2. cache        store.py      SHA-256 of the file + options hash
              3. extract      pdf.py        PyMuPDF, one string per page
                                            (no text ⇒ "scanned PDF" error)
              4. chunk        chunking.py   ~3 pages, breaking at headings
              5. per chunk    llm.py        key points + testable items  ──┐
                                            3 chunks at a time             │
              6. merge A      llm.py        key points   → notes by topic  │ JSON
                              (falls back to the page text if a model       │ only
                               extracts no key points)                      │
              7. merge B      llm.py        testable items → questions     │
                              (same text fallback when no items found)      │
              8. save         store.py      SQLite; the PDF bytes are dropped
              ▼
          {"notes": [...], "questions": [...]}
```

Every model call must come back as a JSON object. `schemas.py` strips code
fences and surrounding prose, validates with Pydantic, and the pipeline retries
**once** with a blunt reminder before giving up with a readable error.

### Folder structure

```
examnotes/
├── app/
│   ├── main.py        FastAPI app, static files, error handlers, startup purge
│   ├── api.py         the four routes
│   ├── config.py      every limit and tunable in one place
│   ├── llm.py         THE ONLY file that talks to an AI provider
│   ├── prompts.py     all prompt text
│   ├── pdf.py         validation + text extraction + scanned-PDF detection
│   ├── chunking.py    heading-aware chunks that remember their page numbers
│   ├── pipeline.py    the eight steps above
│   ├── schemas.py     Pydantic result models + tolerant JSON parsing
│   ├── store.py       SQLite: save, cache lookup, 24-hour purge
│   ├── ratelimit.py   in-memory 5 uploads/hour/IP
│   ├── errors.py      exceptions that carry a status code and a friendly message
│   └── static/        index.html, style.css, app.js  (no framework)
├── tools/
│   └── llm_agent.py   runs on YOUR machine; answers a hosted app with claude -p
├── tests/             pytest, all against the fake provider
├── .env.example       copy to .env and add your key
├── render.yaml        free deployment on Render
└── requirements.txt
```

---

## The LLM engine adapter

All model access goes through one function:

```python
from app.llm import generate
generate(prompt, system=None) -> str
```

Nothing else in the codebase knows which provider is in use. The provider is
chosen by environment variables:

| Variable       | Meaning                                              |
| -------------- | ---------------------------------------------------- |
| `LLM_PROVIDER` | `gemini`, `claude_cli`, `remote` or `fake` (defaults to `fake` with no key) |
| `LLM_API_KEY`  | your provider key — lives only in `.env`             |
| `LLM_MODEL`    | the model id — never hardcoded, see `.env.example`   |

* **gemini** — uses the official `google-genai` SDK through Google's current
  Interactions API (`client.interactions.create(...)`, recommended since
  June 2026; the older `generateContent` is now legacy). HTTP 429 /
  `RESOURCE_EXHAUSTED` replies are retried with a short backoff (2s, 8s, 20s)
  and then reported as *"The free AI service is busy right now"*.
* **claude_cli** — shells out to the Claude Code CLI in print mode
  (`claude -p`, with `--restricted` so the call gets no shell or file tools).
  It reuses whatever login that terminal already has, so **no API key is
  needed** — the easiest way to see real output while testing. Each call
  spawns a CLI process, so it is slower than an API and it only works on a
  machine where Claude Code is installed and logged in: it cannot be deployed
  to Render or any other server. `LLM_MODEL` takes a model id or a CLI alias
  (`opus`, `sonnet`, `haiku`), or may be left empty to use the terminal's
  default. Usage-limit replies are retried with the same backoff as Gemini.
* **remote** — forwards each prompt over HTTPS to the agent in
  `tools/llm_agent.py` running on your own machine, which answers with
  `claude -p`. This is how a *deployed* copy can still use your Claude Code
  login — see "Hosted demo without an API key" below.
* **fake** — returns one fixed, valid JSON document. Used by every test and
  whenever no key is set, so the app runs end to end offline.

---

## Run it locally

```bash
cd examnotes
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Offline demo — no key needed, returns sample output:
LLM_PROVIDER=fake LLM_MODEL=fake uvicorn app.main:app --reload

# Real output with no API key, using the Claude Code CLI on this machine:
LLM_PROVIDER=claude_cli LLM_MODEL=haiku uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>.

For real output, copy the example env file and add your key:

```bash
cp .env.example .env
# edit .env: set LLM_API_KEY=...
uvicorn app.main:app --reload
```

### Getting a free API key (Google Gemini)

1. Go to <https://aistudio.google.com/apikey> and sign in with a Google account.
2. Click **Create API key** and copy it.
3. Paste it into `.env` as `LLM_API_KEY=...`, keep `LLM_PROVIDER=gemini`.
4. Check which models your project may call for free, and at what rate, at
   <https://aistudio.google.com/rate-limit> — Google no longer publishes the
   free-tier numbers in the docs. `.env.example` ships with
   `LLM_MODEL=gemini-3.6-flash`; `gemini-3.5-flash-lite` is the cheaper option
   if you hit limits.

> On the free tier a 100-page PDF means roughly 20–35 model calls, so an upload
> can take a minute or two and may pause for the backoff. That is expected.

### Tests

```bash
pytest -q
```

81 tests, all offline on the fake provider. The Claude CLI provider is tested
with a stubbed `subprocess.run` and the remote provider with a stubbed
`httpx.post`, so no real CLI is launched and no network call is made: upload validation, chunking,
caching and expiry, JSON parsing and the retry, and every API endpoint.

---

## API

| Method | Path                           | Notes                                                   |
| ------ | ------------------------------ | ------------------------------------------------------- |
| `POST` | `/api/documents`               | multipart: `file`, `question_count` (default 10, max 30), `question_types` (comma separated), `difficulty` |
| `GET`  | `/api/documents/{id}`          | the saved result (`410` once it has expired)            |
| `POST` | `/api/documents/{id}/chat`     | JSON `{"message": "..."}` → `{"reply": "..."}`          |
| `GET`  | `/api/health`                  | status, active provider and the current limits          |

Successful upload:

```json
{
  "document_id": "a1b2c3d4e5f6",
  "cached": false,
  "pages": 24,
  "filename": "lecture-3.pdf",
  "options": {"question_count": 10, "question_types": ["short_answer"], "difficulty": "mixed"},
  "result": {
    "notes": [{"topic": "Photosynthesis", "points": ["..."]}],
    "questions": [{
      "type": "multiple_choice",
      "question": "...",
      "options": ["...", "...", "...", "..."],
      "answer": "...",
      "page": 12,
      "difficulty": "easy"
    }]
  }
}
```

Every rejected request returns `{"error": "a sentence a student can read"}`
with a sensible status code: `400` bad upload, `422` scanned PDF, `429` rate
limited, `404` unknown document, `410` expired, `503` AI service trouble.

---

## Hosted demo without an API key

If you have no API key but you do have Claude Code, the app can be hosted
publicly and still use your login. The web app runs on Render as usual; model
calls are forwarded back to a small agent on your own machine:

```
lecturer's browser → Render (public URL) → HTTPS tunnel → your laptop → claude -p
```

**1. On your machine, start the agent** (from the project root). Pick a long
random secret; it is the only thing protecting your Claude quota:

```bash
export AGENT_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo "$AGENT_TOKEN"            # you will paste this into Render
EXAMNOTES_AGENT_TOKEN=$AGENT_TOKEN LLM_MODEL=haiku .venv/bin/python -m tools.llm_agent
```

**2. Expose it with a tunnel.** Cloudflare's quick tunnels need no account:

```bash
# Debian/Ubuntu/Kali, once:
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb

cloudflared tunnel --url http://127.0.0.1:8010
# prints something like  https://brave-forest-1234.trycloudflare.com
```

**3. Point the hosted app at it.** In Render's Environment tab:

```
LLM_PROVIDER    = remote
LLM_REMOTE_URL  = https://brave-forest-1234.trycloudflare.com
LLM_REMOTE_TOKEN= <the same AGENT_TOKEN>
LLM_MODEL       = (unused by the hosted app; the agent chooses the model)
```

Redeploy, open the public URL, upload a PDF: the work happens on your laptop.

**What to know before you rely on this for a graded demo**

* Your machine must be awake, online, and running both the agent and the
  tunnel for the whole demo. Close the laptop and the site returns
  *"The AI backend for this demo is offline."*
* A free `trycloudflare.com` URL changes every time you restart the tunnel, so
  you must update `LLM_REMOTE_URL` on Render each time.
* Every upload spends your Claude Code quota, and anyone with the public URL
  can spend it — the 5-uploads-per-hour-per-IP limit is your main protection,
  so do not advertise the link.
* It adds a network hop, so expect it to be a little slower than local.
* The agent refuses to start without a token and rejects any request whose
  `x-examnotes-token` header does not match.

For a demo that survives your laptop being shut, use the `gemini` provider
with a free key instead — that is what the blueprint below defaults to.

## Deploy free on Render

`render.yaml` is included.

> Deploy with the `gemini` provider. `claude_cli` depends on a logged-in Claude
> Code install, which a hosting platform does not have.

1. Push this folder to a GitHub repository (`.env` is git-ignored — check that
   your key is not in the repo before pushing).
2. On <https://render.com>: **New → Blueprint**, pick the repository, apply.
3. In the service's **Environment** tab add `LLM_API_KEY` (it is marked
   `sync: false` in the blueprint, so it is never stored in git).
4. Deploy. The health check is `/api/health`.

Free Render instances sleep when idle and have an ephemeral disk, so the first
request after a pause is slow and the SQLite cache starts empty after a
restart. Nothing breaks — a repeat upload is simply processed again.

---

## Limits and privacy

* PDF only (the file signature is checked, not the extension), 20 MB, 100 pages.
* Scanned PDFs are rejected with a clear message — there is no OCR.
* 5 uploads per hour per IP address, counted in memory.
* The uploaded file is never stored; extracted text and results are deleted
  after 24 hours (purged on startup and on every upload).
* The page carries a visible notice that text is sent to a third-party AI
  service and that private documents should not be uploaded.
* The API key lives only in `.env` on the server (git-ignored) or in the host's
  environment settings.

## Known limitations

* Generated answers can be wrong — always check them against your own notes.
* Processing happens inside the request, so a very long PDF on a slow free tier
  can approach a proxy timeout. Keep uploads to one lecture or chapter.
* The rate limit is per process and resets on restart.
