# Deploying RetailMind AI

You want a URL you can send to someone. There is one Docker image and it runs
on all four hosts below without modification.

The image ships with the **deterministic mock LLM**, so the deployed URL works
with no API key, no model download and no bill. Someone can open it and the
assistant, the evaluation console and the red-team run all work. Putting a
real model behind it is one environment variable and is covered below.

---

## Pick a host

| | cost | card needed | cold start | best for |
|---|---|---|---|---|
| **Hugging Face Spaces** | free | no | sleeps after 48h idle, wakes in ~30s | **a portfolio link on a CV** |
| **Render** | free | no | spins down after 15 min, wakes in ~50s | a quick public URL from GitHub |
| **Google Cloud Run** | free tier | yes | ~3s | a live demo in an interview |
| **Fly.io** | free allowance | yes | ~2s with scale-to-zero | you already use Fly |
| **Cloudflare Tunnel** | free | no | none — your Mac serves it | showing someone *right now* |

For an AI-engineering portfolio, **Hugging Face Spaces** is the one I would
choose: permanent URL, no card, and the audience you care about already knows
the domain.

---

## 0 · Make it a git repo

Every host below deploys from git.

```bash
cd ~/Documents/retailmind-ai
git init -b main
git add -A
git commit -m "RetailMind AI: agentic retail platform as an AI evaluation environment"
```

`.gitignore` already excludes `.venv/`, `var/`, `*.db` and `.env`. Check that
`git status` shows nothing you would not want public before you push —
especially `.env` if you have put a key in it.

## 1 · Hugging Face Spaces (recommended)

1. Push this repo to GitHub (or straight to the Space — Spaces are git repos).
2. huggingface.co → **New Space** → SDK **Docker**, hardware **CPU basic (free)**.
3. Copy `SPACE_README.md` over the Space's `README.md`. The YAML block at the
   top is what tells Spaces to use Docker and to expose port 8000 — without it
   the Space will not start.
4. Push. First build takes ~4 minutes.

```bash
git remote add space https://huggingface.co/spaces/<your-username>/retailmind-ai
cp SPACE_README.md README.space.md    # keep the project README as-is locally
git push space main
```

Your URL: `https://<your-username>-retailmind-ai.hf.space`

Spaces gives the container `/data` only on paid tiers. On free, the SQLite
file lives on ephemeral disk and the app re-seeds itself on every wake — which
is fine, because the seed data is deterministic. If you want persistence,
either upgrade or point `DATABASE_URL` at a hosted Postgres (Neon and Supabase
both have free tiers).

## 2 · Render

```bash
# push to GitHub first
# render.com -> New -> Blueprint -> select the repo
```

`render.yaml` is already in the repo: Docker runtime, health check on
`/health`, a 1 GB disk mounted at `/data` so the database survives restarts,
Singapore region. Nothing to configure by hand.

## 3 · Google Cloud Run

Fastest cold start of the free options, and the free tier is generous.

```bash
gcloud run deploy retailmind-ai \
  --source . --region asia-south1 \
  --allow-unauthenticated --port 8000 \
  --memory 512Mi --cpu 1 --min-instances 0 \
  --set-env-vars LLM_PROVIDER=mock,PUBLIC_DEMO=true,RATE_LIMIT_PER_MIN=30
```

Cloud Run's filesystem is read-only apart from `/tmp`, so set
`DATABASE_URL=sqlite+aiosqlite:////tmp/retailmind.db`. Each instance re-seeds
on start; deterministic seed data makes that a non-issue.

## 4 · Fly.io

```bash
fly launch --no-deploy --copy-config
fly volumes create retailmind_data --size 1 --region sin
fly deploy
```

## 5 · Cloudflare Tunnel — a URL in 60 seconds

For showing someone something right now. Your Mac is the server, so the URL
dies when you close the laptop.

```bash
brew install cloudflared
make dev            # terminal 1
make tunnel         # terminal 2 -> prints https://<random>.trycloudflare.com
```

This is the only option that can serve a **local Ollama model**, because
Ollama is running on the same machine. Nothing on a free cloud tier can run
llama3.1.

---

## Verify the image before you push

```bash
make docker-smoke
```

Builds the image, runs it, waits for health, and asserts that the container
seeded itself from an empty database and answered a policy question correctly.
If that passes, the deploy will work.

---

## Putting a real model behind it

Ollama cannot run on any free tier — it needs several GB of RAM and a model
download. Use an OpenAI-compatible API instead; the adapter in
`backend/app/llm/providers.py` speaks to any of them.

**Groq** is the best free option: OpenAI-compatible, fast, and a free tier
that comfortably covers a demo.

```
LLM_PROVIDER=openai
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_API_KEY=gsk_...
OPENAI_MODEL=llama-3.3-70b-versatile
```

OpenAI, Together, Fireworks and a self-hosted vLLM all work the same way —
only `OPENAI_BASE_URL` and `OPENAI_MODEL` change.

**The moment you add a real key, set a daily ceiling.** A public URL with an
unmetered key is how a stranger spends your money while you sleep:

```
DAILY_REQUEST_LIMIT=200
RATE_LIMIT_PER_MIN=15
```

Your own eval runs tell you what to set it to — `cost_per_task_inr` is in
every eval summary, so the budget is that number times the number of requests
you are willing to fund.

Embeddings stay on `hashed` in the cloud. Switching to `ollama` embeddings
needs Ollama; if you want real embeddings on a hosted deployment, that is a
separate embedding API and a re-ingest.

---

## Environment variables that matter in production

| variable | default in image | what it does |
|---|---|---|
| `PORT` | 8000 | injected by every host; the image already honours it |
| `DATABASE_URL` | `sqlite+aiosqlite:////data/retailmind.db` | point at Postgres for persistence across instances |
| `LLM_PROVIDER` | `mock` | `mock`, `ollama`, or `openai` (any compatible endpoint) |
| `AUTO_BOOTSTRAP` | `true` | seeds and ingests on first boot; idempotent |
| `PUBLIC_DEMO` | `true` | shown on `/health`; a signal, not a control |
| `RATE_LIMIT_PER_MIN` | `30` | per caller, by `X-Forwarded-For` |
| `DAILY_REQUEST_LIMIT` | `0` (off) | hard ceiling on metered endpoints, resets 00:00 UTC |
| `ACCESS_PASSWORD` | unset | when set, every API call needs it |

---

## Sharing the URL safely

**What is in there.** Three fictional customers, twelve invented products,
nine fake orders. No real data, nothing to leak. Two product descriptions are
deliberately hostile — they are the prompt-injection and RAG-poisoning
fixtures, and they are meant to be found.

**The `operator` role in the header picker is not a security boundary.**
Anyone can select it and approve a pending refund on fake data. That is fine
for a demo and would be a critical finding in production; `docs/adr/0002`
explains where real authorization lives. If someone asks about it in an
interview, that answer is the point.

**If you want the link semi-private**, set `ACCESS_PASSWORD` and share the URL
with the key appended:

```
https://your-space.hf.space/?k=your-password
```

The UI stores the key and strips it from the address bar, so a screenshot does
not leak it. It is a doorbell, not a lock — do not put anything real behind it.

**Rate limiting is per instance and in memory.** One container is the assumed
deployment. Scale beyond that and the limits multiply by the instance count;
move the buckets to Redis at that point. The middleware contract does not
change — see `backend/app/api/middleware.py`.

---

## What to actually show someone

Send them the URL and these five messages, in order. It takes two minutes and
covers the whole architecture:

1. `What is the return window for electronics?` — a cited answer, and it comes
   from `return_policy_v4`, not the superseded `v3` sitting in the same index.
2. `My order is delayed. Why, can I get a refund, and please raise a ticket.` —
   four agents, eight tool calls, one composed answer. Open the trace panel.
3. `Where is my order OR-20001 and why is it late?` — the *same* delay scenario
   without the refund and ticket asks. Two agents, not four. The agent does not
   do work nobody asked for.
4. `Show me order OR-VICTIM01` — belongs to another customer. Watch `get_order`
   appear in the trace with verdict `deny_ownership`. The model asked; the
   gateway refused.
5. Open the **Evaluation console**, run `adversarial/attack_corpus` with model
   `mock-naive`, and let them read the result: thirty attacks, zero successes,
   against a model that obeys every injected instruction it is given.

Number five is the one that gets remembered.
