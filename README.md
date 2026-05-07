# ether.fi Cash OpenOcean Quote Dashboard

Local Streamlit + Plotly dashboard for reviewing current OpenOcean quote snapshots for observed ether.fi Cash trade paths on Optimism.

The dashboard now reads quote collector output by default:

```text
data/openocean_optimism_quote_snapshots.csv
```

Quote Execution Cost compares requested USD input size versus quoted USD output value:

```text
execution_cost_usd = trade_size_usd - quoted_out_value_usd
execution_cost_pct = execution_cost_usd / trade_size_usd
dashboard_execution_cost_percent = execution_cost_pct * 100
```

Positive execution cost means the quoted output USD value is lower than the requested input size. Negative execution cost means apparent quote improvement or token-pricing noise. Gas is recorded separately and is not included in execution cost.

## Project Structure

```text
app.py
utils.py
requirements.txt
README.md
scripts/
  fetch_openocean_quotes.py
data/
  openocean_optimism_quote_snapshots.csv
  openocean_optimism_quote_snapshots_YYYYMMDD_HHMMSS.csv
  openocean_optimism_quote_raw_YYYYMMDD_HHMMSS.jsonl
  trade_paths.csv
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The dashboard reads `data/openocean_optimism_quote_snapshots.csv` by default. You can upload another quote CSV from the sidebar.

## Dashboard Tabs

- Overview: quote volume, success rate, median/p95/worst execution cost, cost by size, pair summary, pair heatmap, and DEX route usage.
- Quote Explorer: one focused table for worst quotes, largest inputs, negative quotes, and failed quote attempts.
- Methodology & Data Quality: short metric definition, formula details, quote status, failure reasons, decimal sources, and null-count diagnostics.


## Refreshing Quote Data From The Dashboard

The dashboard has a sidebar **Refresh quotes** button. It runs the existing collector script with conservative rate-limit settings:

```bash
python scripts/fetch_openocean_quotes.py --sleep 2 --max-requests-per-10s 5
```

This updates `data/openocean_optimism_quote_snapshots.csv` and creates timestamped CSV/JSONL audit files. The dashboard uses a lock file at `data/.openocean_quote_refresh.lock` so two users cannot start the collector at the same time.

The app does **not** refresh OpenOcean quotes on every page load. That is intentional: Streamlit reruns on refreshes and widget changes, and automatic quote fetching would risk slow page loads and OpenOcean 429 rate limits.


## Free Streamlit Community Cloud Deployment

This app works well on Streamlit Community Cloud when refreshed quote data is treated as temporary. The app can run the OpenOcean collector from the sidebar **Refresh quotes** button, update the in-session CSV, and display the latest quote snapshot to viewers. If the app hibernates or redeploys, generated quote files may disappear; that is acceptable for this workflow because quote snapshots are only useful at view time.

Recommended files to commit to GitHub:

```text
app.py
utils.py
requirements.txt
README.md
scripts/fetch_openocean_quotes.py
data/trade_paths.csv
data/openocean_optimism_quote_snapshots.csv  # small seed file so the app opens immediately
```

Do not commit generated raw/timestamped quote outputs or the old historical CSV. They are ignored by `.gitignore`.

Deploy steps:

1. Push the project to a GitHub repo.
2. Go to `https://share.streamlit.io` and create an app from the repo.
3. Set the entrypoint to `app.py`.
4. Use Python 3.12 if Streamlit asks for a Python version.
5. Share the generated `streamlit.app` URL with the team.

Notes:

- The app may sleep after inactivity on Community Cloud. When someone opens it again, they can wake it up.
- The first view uses the seed CSV from GitHub.
- Clicking **Refresh quotes** runs `scripts/fetch_openocean_quotes.py` on Streamlit Cloud and updates the displayed data for that running session.
- Refreshed CSVs are not durable across hibernation/redeploy, which is fine for quote snapshots.
- The refresh can take several minutes and can hit OpenOcean rate limits if used too aggressively.

## Self-Hosted Deployment

You do not need Streamlit Community Cloud. The app can be self-hosted anywhere that can run Python or Docker.

### Option A: Run directly on a machine

```bash
cd "ether.fi Cash Swap Slippage"
source .venv/bin/activate
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

Open the app at:

```text
http://localhost:8501
```

If this runs on a team/internal server, your team lead can open `http://SERVER_IP:8501` if the network allows that port.

### Option B: Run with Docker Compose

```bash
docker compose up --build
```

Open:

```text
http://localhost:8501
```

`docker-compose.yml` mounts `./data` into the container, so refreshed CSVs and timestamped raw outputs persist on your machine.

### Option C: Share without opening router ports

After the app is running locally on port `8501`, use a tunnel.

For a quick Cloudflare test tunnel:

```bash
cloudflared tunnel --url http://localhost:8501
```

Cloudflare will print a temporary `trycloudflare.com` URL you can share. For a more permanent team URL, create a Cloudflare Tunnel route that maps a hostname such as `cash-quotes.yourdomain.com` to `http://localhost:8501`.

Tailscale is another good option if your team already uses it. Use Tailscale Serve for private tailnet sharing, or Tailscale Funnel if you need a public HTTPS URL.

## OpenOcean Optimism Quote Collector

Fetch quote-only OpenOcean snapshots for ether.fi Cash supported assets on Optimism:

```bash
source .venv/bin/activate
python scripts/fetch_openocean_quotes.py
```

Default behavior:

- Chain: Optimism only.
- Endpoint: `https://open-api.openocean.finance/v4/optimism/quote`.
- Sizes: `$50,000`, `$100,000`, `$150,000`, `$200,000`.
- Mode: `trade-paths`, meaning routes are read from `data/trade_paths.csv`.
- Trade path format: one `trade_paths` column with values like `ETHFI>>>WETH`.
- Output folder: `data/`.

Outputs:

```text
data/openocean_optimism_quote_snapshots.csv
data/openocean_optimism_quote_snapshots_YYYYMMDD_HHMMSS.csv
data/openocean_optimism_quote_raw_YYYYMMDD_HHMMSS.jsonl
```

Optional safer rate-limit run:

```bash
python scripts/fetch_openocean_quotes.py --sleep 2 --max-requests-per-10s 5
```

The collector defaults to a conservative client-side limit of `8` requests per 10 seconds and at least `1.25` seconds between requests. OpenOcean public docs mention `20` requests per 10 seconds, but the live API can return a stricter `1 r/s` 429 response. If you see `Your data usage has exceeded the limit of 1 r/s`, wait for the cool-down window in the response, then rerun with the safer command above.

Supported modes:

- `trade-paths`: observed routes from `data/trade_paths.csv` (default).
- `usdc-both`: token -> USDC and USDC -> token.
- `token-to-usdc`: only token -> USDC.
- `usdc-to-token`: only USDC -> token.
- `all-pairs`: every supported token -> every other supported token, excluding same-token pairs.

The collector only calls read/quote endpoints (`quote`, `gasPrice`, and `tokenList`). It does not call swap endpoints, submit transactions, sign messages, or require private keys.
