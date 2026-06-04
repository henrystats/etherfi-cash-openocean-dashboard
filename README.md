# ether.fi Cash OpenOcean Quote Data

This project analyzes OpenOcean quote snapshots for observed ether.fi Cash trade paths on Optimism.

The dashboard focuses on current quote execution cost for standardized input sizes:

```text
$25,000
$50,000
$100,000
$150,000
$200,000
```

## Core Data Files

```text
data/trade_paths.csv
data/openocean_optimism_quote_snapshots.csv
data/openocean_optimism_quote_history.csv
scripts/fetch_openocean_quotes.py
```

`data/trade_paths.csv` contains the observed trade paths to quote. Each row uses this format:

```text
INPUT_TOKEN>>>OUTPUT_TOKEN
```

Example:

```text
USDC>>>weETH
weETH>>>USDC
ETHFI>>>WETH
```

`data/openocean_optimism_quote_snapshots.csv` is the latest quote snapshot file read by the dashboard. It is a temporary point-in-time output from OpenOcean and can be refreshed from the app.

`data/openocean_optimism_quote_history.csv` stores the rolling 24-hour quote history used for the dashboard's 24h median baseline. The collector appends each new snapshot run to this file, then prunes rows outside the retention window.

## Quote Source

Quotes come from OpenOcean V4 read-only endpoints on Optimism:

```text
https://open-api.openocean.finance/v4/optimism/quote
https://open-api.openocean.finance/v4/optimism/gasPrice
https://open-api.openocean.finance/v4/optimism/tokenList
```

The collector does not submit swaps, sign messages, send transactions, or use private keys.

## Quote Sizing

The requested trade sizes are USD-denominated, but OpenOcean expects `amountDecimals` in raw token units.

For each quote path and size, the collector estimates the input token amount as:

```text
input_token_amount = trade_size_usd / estimated_in_token_price_usd
amountDecimals = input_token_amount * 10^input_token_decimals
```

For non-USDC input tokens, the script estimates the input token USD price using a small `1 token -> USDC` OpenOcean quote when possible. For USDC input, it uses USDC as the USD anchor.

If the input token price cannot be estimated, the quote attempt is recorded as failed instead of guessing a raw token amount.

## Execution Cost Metric

Quote Execution Cost compares requested USD input size versus quoted USD output value:

```text
execution_cost_usd = trade_size_usd - quoted_out_value_usd
execution_cost_pct = execution_cost_usd / trade_size_usd
dashboard_execution_cost_percent = execution_cost_pct * 100
```

Interpretation:

- Positive execution cost means the quoted output USD value is lower than the requested input size.
- Negative execution cost means apparent price improvement or quote/pricing noise.
- Gas is recorded separately and is not included in execution cost.
- This is quote execution cost, not submitted-swap slippage.

## Important Columns

```text
snapshot_time_utc
snapshot_run_id
chain
trade_size_usd
in_token_symbol
in_token_address
in_token_decimals
out_token_symbol
out_token_address
out_token_decimals
estimated_in_token_price_usd
price_source
amount_decimals
quoted_out_amount_decimals
quoted_out_value_usd
quoted_out_value_source
execution_cost_usd
execution_cost_pct
execution_cost_bps
openocean_price_impact
estimated_gas
gas_price
route_summary
dexes_used
quote_success
error_message
request_url
response_status_code
```

`execution_cost_bps` is kept in the raw CSV for compatibility, but the dashboard displays execution cost as a percent.

## Latest Snapshot vs 24h Median

The dashboard can show two quote bases:

- Latest snapshot: the most recent OpenOcean collector run.
- Last 24h median: median execution cost by quote pair and trade size across the rolling history file.

The 24h median is intended as a more stable baseline for volatile or thin-liquidity pairs, while the latest snapshot reflects current market conditions at the last refresh time.

## Data Caveats

- Quote snapshots are point-in-time and should be refreshed before analysis.
- OpenOcean route availability can change between runs.
- Price probes and quote responses may use slightly different price references.
- Thin liquidity or missing token price data can produce failed quotes or extreme execution cost values.
- OpenOcean public rate limits can cause HTTP 429 responses if quotes are refreshed too aggressively.

## Dashboard Behavior

The dashboard reads `data/openocean_optimism_quote_snapshots.csv` by default.

The sidebar **Refresh quotes** button reruns `scripts/fetch_openocean_quotes.py`, updates the latest CSV, appends to the rolling history CSV, and reloads the dashboard with the new quote data.

Generated timestamped CSV and raw JSONL files are audit artifacts. They are useful locally but are not required for the dashboard to run.

## Discord Heatmap Report

`scripts/post_discord_heatmap.py` generates a static PNG version of the dashboard heatmap and can post it to Discord through a webhook. The image defaults mirror the Streamlit heatmap layout, while report metadata is sent in the Discord message text.

By default, the report uses the rolling 24-hour median and limits the heatmap to USDC paths:

```bash
python scripts/post_discord_heatmap.py
```

Local outputs are written to:

```text
data/report_snapshots/
```

Each run writes:

```text
openocean_*_*.png
openocean_*_*_matrix.csv
openocean_*_*_rows.csv
```

To post locally, set a Discord webhook URL and pass `--post-discord`:

```bash
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." python scripts/post_discord_heatmap.py --post-discord
```

The GitHub workflow `.github/workflows/post-discord-heatmap.yml` posts automatically after the quote refresh workflow completes successfully. Add `DISCORD_WEBHOOK_URL` as a repository secret before enabling the workflow. The workflow can also be run manually, and it always posts the rolling 24-hour median for USDC paths.
