#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.io as pio
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import (  # noqa: E402
    DEFAULT_DATA_PATH,
    DEFAULT_HISTORY_PATH,
    HEATMAP_COST_COLORSCALE,
    aggregate_quote_history,
    build_pair_size_heatmap_matrix,
    clean_quote_data,
    read_csv,
)


DEFAULT_OUTPUT_DIR = Path("data/report_snapshots")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp_slug() -> str:
    return utc_now().strftime("%Y%m%d_%H%M%S")


def repo_path(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def load_latest(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Latest quote CSV not found: {path}")

    df = clean_quote_data(read_csv(path))
    snapshot_time = df["snapshot_time"].max() if "snapshot_time" in df.columns else pd.NaT
    return df, {
        "basis": "Latest snapshot",
        "source": str(path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path),
        "window_start": pd.NaT,
        "window_end": snapshot_time,
        "raw_rows": len(df),
        "snapshots": df["snapshot_run_id"].replace("", pd.NA).dropna().nunique() if "snapshot_run_id" in df.columns else 0,
    }


def load_24h_median(path: Path, hours: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Quote history CSV not found: {path}")

    history_df = clean_quote_data(read_csv(path))
    valid_times = history_df["snapshot_time"].dropna()
    if valid_times.empty:
        raise ValueError(f"Quote history has no parseable snapshot_time_utc values: {path}")

    window_end = valid_times.max()
    window_start = window_end - pd.Timedelta(hours=hours)
    window_df = history_df[history_df["snapshot_time"].between(window_start, window_end, inclusive="both")].copy()
    if window_df.empty:
        raise ValueError(f"No quote rows found in the last {hours} hours of history")

    snapshot_key = "snapshot_run_id" if "snapshot_run_id" in window_df.columns else "snapshot_minute"
    snapshots = window_df[snapshot_key].replace("", pd.NA).dropna().nunique()
    return aggregate_quote_history(window_df), {
        "basis": f"Last {hours}h median",
        "source": str(path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path),
        "window_start": window_start,
        "window_end": window_end,
        "raw_rows": len(window_df),
        "snapshots": int(snapshots),
    }


def load_report_data(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    if args.basis == "latest":
        return load_latest(repo_path(args.data_path))
    return load_24h_median(repo_path(args.history_path), args.hours)


def filter_pair_scope(df: pd.DataFrame, pair_scope: str) -> pd.DataFrame:
    if pair_scope == "all":
        return df.copy()
    if pair_scope == "usdc":
        return df[df["in_token_symbol"].eq("USDC") | df["out_token_symbol"].eq("USDC")].copy()
    raise ValueError(f"Unsupported pair scope: {pair_scope}")


def valid_report_rows(df: pd.DataFrame, pair_scope: str) -> pd.DataFrame:
    scoped_df = filter_pair_scope(df, pair_scope)
    valid_df = scoped_df[scoped_df["valid_quote"]].copy()
    if valid_df.empty:
        raise ValueError(f"No successful quotes available for pair scope '{pair_scope}'")
    return valid_df


def report_subtitle(meta: dict[str, Any], valid_df: pd.DataFrame, pair_scope: str) -> str:
    pair_label = "USDC paths" if pair_scope == "usdc" else "All pairs"
    start = meta.get("window_start")
    end = meta.get("window_end")
    if pd.notna(start) and pd.notna(end):
        window = f"{start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC"
    elif pd.notna(end):
        window = f"{end:%Y-%m-%d %H:%M:%S} UTC"
    else:
        window = "snapshot time unavailable"

    return (
        f"{meta['basis']} | {pair_label} | {len(valid_df):,} successful quote rows | "
        f"{valid_df['quote_pair'].nunique():,} pairs | {window}"
    )


def app_heatmap_height(row_count: int) -> int:
    return min(760, max(420, 180 + 18 * row_count))


def build_heatmap_figure(
    matrix: pd.DataFrame,
    title: str,
    subtitle: str | None,
    width: int,
    height: int,
):
    fig = px.imshow(
        matrix,
        aspect="auto",
        color_continuous_scale=HEATMAP_COST_COLORSCALE,
        range_color=(0, 1),
        text_auto=".1f",
        labels=dict(x="Input size", y="Quote pair", color="Median %"),
    )
    fig.update_traces(
        texttemplate="%{z:.1f}%",
        hovertemplate="Quote pair: %{y}<br>Input size: %{x}<br>median = %{z:.2f}%<extra></extra>",
    )
    title_text = f"{title}<br><sup>{subtitle}</sup>" if subtitle else title
    fig.update_layout(
        title=dict(text=title_text, x=0.01, xanchor="left"),
        template="plotly_dark",
        paper_bgcolor="#000000",
        plot_bgcolor="#000000",
        font=dict(
            family="Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif",
            size=12,
        ),
        title_font=dict(size=16),
        margin=dict(l=38, r=28, t=58, b=82),
        coloraxis_colorbar=dict(
            title="Median %",
            tickvals=[0, 0.3, 0.6, 1],
            ticktext=["0%", "0.3%", "0.6%", "1%+"],
            len=0.62,
            thickness=18,
        ),
        width=width,
        height=height,
        hoverlabel=dict(bgcolor="#111827", bordercolor="rgba(148,163,184,0.35)", font_size=12),
    )
    fig.update_xaxes(
        side="bottom",
        showgrid=True,
        gridcolor="rgba(148,163,184,0.11)",
        zerolinecolor="rgba(148,163,184,0.25)",
        title_standoff=10,
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="rgba(148,163,184,0.11)",
        zerolinecolor="rgba(148,163,184,0.25)",
        title_standoff=10,
        ticklabelstandoff=10,
        automargin=True,
    )
    return fig


def write_report_outputs(
    valid_df: pd.DataFrame,
    matrix: pd.DataFrame,
    fig,
    output_dir: Path,
    report_name: str,
    width: int,
    height: int,
    scale: float,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    png_path = output_dir / f"{report_name}.png"
    matrix_path = output_dir / f"{report_name}_matrix.csv"
    rows_path = output_dir / f"{report_name}_rows.csv"

    matrix.to_csv(matrix_path)
    valid_df.to_csv(rows_path, index=False)
    pio.write_image(fig, png_path, width=width, height=height, scale=scale)
    return {"png": png_path, "matrix": matrix_path, "rows": rows_path}


def post_to_discord(webhook_url: str, image_path: Path, content: str) -> None:
    webhook_url = webhook_url.strip()
    wait_url = f"{webhook_url}&wait=true" if "?" in webhook_url else f"{webhook_url}?wait=true"
    payload = {
        "content": content,
        "allowed_mentions": {"parse": []},
    }
    with image_path.open("rb") as image_file:
        response = requests.post(
            wait_url,
            data={"payload_json": json.dumps(payload)},
            files={"files[0]": (image_path.name, image_file, "image/png")},
            timeout=45,
        )
    response.raise_for_status()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and optionally post the OpenOcean heatmap to Discord.")
    parser.add_argument("--basis", choices=["latest", "24h-median"], default="24h-median", help="Quote basis to render. Default: 24h-median.")
    parser.add_argument("--pair-scope", choices=["usdc", "all"], default="usdc", help="Pairs to include. Default: usdc.")
    parser.add_argument("--data-path", default=str(DEFAULT_DATA_PATH), help="Latest quote CSV path.")
    parser.add_argument("--history-path", default=str(DEFAULT_HISTORY_PATH), help="Quote history CSV path.")
    parser.add_argument("--hours", type=int, default=24, help="Rolling history window for 24h-median basis. Default: 24.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for PNG/CSV report artifacts.")
    parser.add_argument("--report-name", default=None, help="Optional output file stem. Defaults to a timestamped report name.")
    parser.add_argument("--title", default="Pair x Size Heatmap", help="Image title.")
    parser.add_argument("--width", type=int, default=1088, help="PNG width in logical pixels. Default: 1088.")
    parser.add_argument("--height", type=int, default=None, help="PNG height in logical pixels. Defaults to the app heatmap formula.")
    parser.add_argument("--scale", type=float, default=1.0, help="PNG export scale. Default: 1.")
    parser.add_argument("--include-subtitle", action="store_true", help="Include report metadata as an image subtitle.")
    parser.add_argument("--post-discord", action="store_true", help="Post the generated PNG to Discord.")
    parser.add_argument("--discord-webhook-env", default="DISCORD_WEBHOOK_URL", help="Environment variable containing the Discord webhook URL.")
    parser.add_argument("--discord-content", default=None, help="Optional Discord message content.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    df, meta = load_report_data(args)
    valid_df = valid_report_rows(df, args.pair_scope)
    matrix = build_pair_size_heatmap_matrix(valid_df)
    if matrix.empty:
        raise ValueError("Heatmap matrix is empty after filtering")

    subtitle = report_subtitle(meta, valid_df, args.pair_scope)
    height = args.height or app_heatmap_height(len(matrix))
    fig = build_heatmap_figure(
        matrix=matrix,
        title=args.title,
        subtitle=subtitle if args.include_subtitle else None,
        width=args.width,
        height=height,
    )

    report_name = args.report_name or f"openocean_{args.pair_scope}_{args.basis}_{timestamp_slug()}"
    outputs = write_report_outputs(
        valid_df=valid_df,
        matrix=matrix,
        fig=fig,
        output_dir=repo_path(args.output_dir),
        report_name=report_name,
        width=args.width,
        height=height,
        scale=args.scale,
    )

    print(f"Generated heatmap PNG: {outputs['png']}")
    print(f"Generated matrix CSV: {outputs['matrix']}")
    print(f"Generated source rows CSV: {outputs['rows']}")
    print(subtitle)

    if not args.post_discord:
        return

    webhook_url = os.environ.get(args.discord_webhook_env)
    if not webhook_url:
        raise RuntimeError(f"--post-discord requires {args.discord_webhook_env} to be set")

    content = args.discord_content or f"**{args.title}**\n{subtitle}"
    post_to_discord(webhook_url, outputs["png"], content)
    print("Posted heatmap to Discord.")


if __name__ == "__main__":
    main()
