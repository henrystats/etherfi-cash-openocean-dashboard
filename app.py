from __future__ import annotations

import html
import io
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from utils import (
    DEFAULT_DATA_PATH,
    DEFAULT_HISTORY_PATH,
    aggregate_quote_history,
    clean_quote_data,
    explode_dexes,
    format_number,
    format_pct,
    format_share,
    format_usd,
    ordered_trade_size_labels,
    ordered_unique,
    pctile,
    quote_quality_summary,
    read_csv,
    to_csv_bytes,
)


st.set_page_config(
    page_title="ether.fi Cash OpenOcean Quotes",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded",
)

PLOTLY_CONFIG = {
    "displaylogo": False,
    "responsive": True,
    "modeBarButtonsToRemove": ["lasso2d", "select2d"],
}

COLORWAY = ["#63E6BE", "#74C0FC", "#FFD43B", "#FF922B", "#B197FC", "#F783AC", "#69DB7C", "#CED4DA"]
REFERENCE_PCT = [0, 0.1, 0.5, 1, 5]
HEATMAP_COST_COLORSCALE = [
    [0.0, "#2F9E44"],
    [0.3, "#2F9E44"],
    [0.3, "#FFD43B"],
    [0.6, "#FFD43B"],
    [0.6, "#FF922B"],
    [1.0, "#E03131"],
]
PROJECT_ROOT = Path(__file__).resolve().parent
QUOTE_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "fetch_openocean_quotes.py"
QUOTE_REFRESH_LOCK = PROJECT_ROOT / "data" / ".openocean_quote_refresh.lock"
QUOTE_REFRESH_TIMEOUT_SECONDS = 15 * 60
QUOTE_REFRESH_STALE_SECONDS = 30 * 60



def inject_css() -> None:
    st.markdown(
        """
        <style>
            :root {
                --panel: rgba(17, 24, 39, 0.78);
                --panel-border: rgba(148, 163, 184, 0.18);
                --muted: rgba(226, 232, 240, 0.66);
                --soft: rgba(226, 232, 240, 0.86);
                --accent: #63e6be;
            }

            .block-container {
                max-width: 1280px;
                padding-top: 3.25rem;
                padding-bottom: 2.25rem;
            }

            [data-testid="stHeader"] {
                background: #0d1117;
                height: 2.75rem;
            }

            [data-testid="stSidebar"] {
                background: #0d1117;
                border-right: 1px solid rgba(148, 163, 184, 0.14);
            }

            [data-testid="stSidebar"] .stExpander {
                border-color: rgba(148, 163, 184, 0.14);
            }

            .dashboard-hero {
                border: 1px solid var(--panel-border);
                background: linear-gradient(135deg, rgba(17, 24, 39, 0.98), rgba(15, 42, 47, 0.9));
                border-radius: 8px;
                padding: 0.95rem 1.1rem;
                margin-bottom: 0.85rem;
            }

            .eyebrow {
                color: var(--accent);
                font-size: 0.73rem;
                font-weight: 750;
                letter-spacing: 0;
                text-transform: uppercase;
                margin-bottom: 0.22rem;
            }

            .dashboard-hero h1 {
                margin: 0;
                line-height: 1.08;
                font-size: clamp(1.55rem, 2.35vw, 2.35rem);
                letter-spacing: 0;
            }

            .dashboard-hero p {
                color: var(--soft);
                margin: 0.45rem 0 0;
                max-width: 880px;
                font-size: 0.92rem;
                line-height: 1.42;
            }

            .section-note {
                color: var(--muted);
                font-size: 0.82rem;
                line-height: 1.38;
                margin: 0.15rem 0 0.7rem;
            }

            .kpi-card {
                height: 118px;
                border: 1px solid var(--panel-border);
                background: var(--panel);
                border-radius: 8px;
                padding: 0.78rem 0.82rem;
                display: flex;
                flex-direction: column;
                justify-content: space-between;
                overflow: hidden;
            }

            .kpi-label {
                color: var(--muted);
                font-size: 0.76rem;
                font-weight: 650;
                line-height: 1.22;
                min-height: 1.9em;
            }

            .kpi-value {
                color: #f8fafc;
                font-size: clamp(1.28rem, 1.8vw, 1.58rem);
                font-weight: 760;
                letter-spacing: 0;
                line-height: 1.08;
                overflow-wrap: anywhere;
            }

            .kpi-caption {
                color: var(--muted);
                font-size: 0.72rem;
                line-height: 1.22;
                white-space: normal;
            }

            .method-card {
                border: 1px solid var(--panel-border);
                background: var(--panel);
                border-radius: 8px;
                padding: 0.9rem 1rem;
                color: var(--soft);
                font-size: 0.9rem;
                line-height: 1.45;
                margin-bottom: 0.65rem;
            }

            div[data-testid="stVerticalBlockBorderWrapper"] {
                border-color: rgba(148, 163, 184, 0.16);
                background: rgba(15, 23, 42, 0.18);
            }

            div[data-testid="stTabs"] button {
                font-weight: 650;
            }

            .stDataFrame {
                border-radius: 8px;
            }

            @media (max-width: 920px) {
                .kpi-card { height: 124px; }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner="Loading quote CSV...")
def load_default_data(path: str, modified_at: float) -> pd.DataFrame:
    _ = modified_at
    return clean_quote_data(read_csv(path))


@st.cache_data(show_spinner="Loading quote history...")
def load_history_data(path: str, modified_at: float) -> pd.DataFrame:
    _ = modified_at
    return clean_quote_data(read_csv(path))


@st.cache_data(show_spinner="Loading uploaded quote CSV...")
def load_uploaded_data(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    _ = file_name
    return clean_quote_data(read_csv(io.BytesIO(file_bytes)))


def rolling_history_view(history_df: pd.DataFrame, hours: int = 24) -> tuple[pd.DataFrame, dict]:
    valid_times = history_df["snapshot_time"].dropna()
    if valid_times.empty:
        return history_df.iloc[0:0].copy(), {
            "window_start": pd.NaT,
            "window_end": pd.NaT,
            "raw_rows": 0,
            "snapshots": 0,
        }

    window_end = valid_times.max()
    window_start = window_end - pd.Timedelta(hours=hours)
    window_df = history_df[history_df["snapshot_time"].between(window_start, window_end, inclusive="both")].copy()
    snapshot_key = "snapshot_run_id" if "snapshot_run_id" in window_df.columns else "snapshot_minute"
    snapshots = window_df[snapshot_key].replace("", pd.NA).dropna().nunique() if not window_df.empty else 0
    summary_df = aggregate_quote_history(window_df)
    return summary_df, {
        "window_start": window_start,
        "window_end": window_end,
        "raw_rows": len(window_df),
        "snapshots": int(snapshots),
    }


def style_figure(fig: go.Figure, title: str, subtitle: str | None = None, height: int = 420) -> go.Figure:
    _ = subtitle
    fig.update_layout(
        title=title,
        template="plotly_dark",
        colorway=COLORWAY,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif", size=12),
        title_font=dict(size=16),
        title_x=0.01,
        margin=dict(l=38, r=28, t=58, b=82),
        height=height,
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.18,
            xanchor="left",
            x=0,
            font=dict(size=10),
            bgcolor="rgba(0,0,0,0)",
            itemclick="toggle",
            itemdoubleclick="toggleothers",
        ),
        hoverlabel=dict(bgcolor="#111827", bordercolor="rgba(148,163,184,0.35)", font_size=12),
    )
    fig.update_xaxes(showgrid=True, gridcolor="rgba(148,163,184,0.11)", zerolinecolor="rgba(148,163,184,0.25)", title_standoff=10)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(148,163,184,0.11)", zerolinecolor="rgba(148,163,184,0.25)", title_standoff=10)
    return fig


def render_chart(fig: go.Figure, title: str, caption: str | None = None, height: int = 420) -> None:
    fig = style_figure(fig, title, height=height)
    with st.container(border=True):
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)
        if caption:
            st.caption(caption)


def load_data_ui() -> tuple[pd.DataFrame, str, str]:
    st.sidebar.header("Data")
    uploaded_file = st.sidebar.file_uploader(
        "Upload OpenOcean quote CSV",
        type=["csv"],
        help="Defaults to data/openocean_optimism_quote_snapshots.csv.",
    )

    if uploaded_file is not None:
        df = load_uploaded_data(uploaded_file.getvalue(), uploaded_file.name)
        source = uploaded_file.name
        basis = "Uploaded CSV"
    else:
        if not DEFAULT_DATA_PATH.exists():
            st.error(f"Default quote CSV not found at {DEFAULT_DATA_PATH}. Run the OpenOcean collector or upload a CSV.")
            st.stop()
        latest_df = load_default_data(str(DEFAULT_DATA_PATH), DEFAULT_DATA_PATH.stat().st_mtime)
        basis_options = ["Latest snapshot"]
        history_df = None
        if DEFAULT_HISTORY_PATH.exists():
            history_df = load_history_data(str(DEFAULT_HISTORY_PATH), DEFAULT_HISTORY_PATH.stat().st_mtime)
            basis_options.append("Last 24h median")
        else:
            st.sidebar.caption("24h median appears after the hourly workflow creates history.")

        basis = st.sidebar.radio(
            "Quote basis",
            basis_options,
            horizontal=True,
            help="Latest uses the newest collector run. Last 24h median collapses hourly snapshots by pair and trade size.",
        )

        if basis == "Last 24h median" and history_df is not None:
            df, history_meta = rolling_history_view(history_df, hours=24)
            source = str(DEFAULT_HISTORY_PATH)
            start = history_meta["window_start"]
            end = history_meta["window_end"]
            if pd.notna(start) and pd.notna(end):
                st.sidebar.caption(
                    f"24h window: {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC"
                )
            st.sidebar.caption(
                f"{history_meta['snapshots']:,} snapshots | {history_meta['raw_rows']:,} raw quote rows"
            )
            if df.empty:
                st.sidebar.warning("No quote rows found in the 24h history window. Showing latest snapshot instead.")
                df = latest_df
                source = str(DEFAULT_DATA_PATH)
                basis = "Latest snapshot"
        else:
            df = latest_df
            source = str(DEFAULT_DATA_PATH)

    st.sidebar.caption(f"Loaded `{source}`")
    return df, source, basis


def latest_snapshot_text(df: pd.DataFrame) -> str:
    snapshot = df["snapshot_time"].max() if "snapshot_time" in df.columns else pd.NaT
    if pd.isna(snapshot):
        return "No successful snapshot timestamp found"
    return f"Latest snapshot: {snapshot:%Y-%m-%d %H:%M:%S} UTC"


def lock_is_active(lock_path: Path) -> bool:
    if not lock_path.exists():
        return False
    age = time.time() - lock_path.stat().st_mtime
    if age > QUOTE_REFRESH_STALE_SECONDS:
        lock_path.unlink(missing_ok=True)
        return False
    return True


def run_quote_refresh() -> subprocess.CompletedProcess[str] | None:
    if lock_is_active(QUOTE_REFRESH_LOCK):
        return None

    QUOTE_REFRESH_LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        with QUOTE_REFRESH_LOCK.open("x", encoding="utf-8") as lock_file:
            lock_file.write(str(time.time()))
    except FileExistsError:
        return None

    command = [
        sys.executable,
        str(QUOTE_SCRIPT_PATH),
        "--sleep",
        "2",
        "--max-requests-per-10s",
        "5",
    ]

    try:
        return subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=QUOTE_REFRESH_TIMEOUT_SECONDS,
            check=False,
        )
    finally:
        QUOTE_REFRESH_LOCK.unlink(missing_ok=True)


def render_refresh_controls(df: pd.DataFrame) -> None:
    st.sidebar.header("Quote refresh")
    if message := st.session_state.pop("quote_refresh_success", None):
        st.sidebar.success(message)

    st.sidebar.caption(latest_snapshot_text(df))
    with st.sidebar.expander("Run OpenOcean collector", expanded=False):
        st.caption("Runs the quote collector for the observed trade paths and updates the latest CSV. This can take several minutes.")
        refresh_clicked = st.button("Refresh quotes", width="stretch", type="primary")

    if not refresh_clicked:
        return

    if not QUOTE_SCRIPT_PATH.exists():
        st.sidebar.error(f"Collector script not found at {QUOTE_SCRIPT_PATH}")
        return

    with st.spinner("Fetching fresh OpenOcean quotes. This can take a few minutes..."):
        result = run_quote_refresh()

    if result is None:
        st.sidebar.warning("A quote refresh is already running. Try again in a few minutes.")
        return

    output = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
    if result.returncode == 0:
        st.cache_data.clear()
        st.session_state["quote_refresh_success"] = "OpenOcean quotes refreshed. Dashboard reloaded with the latest CSV."
        st.rerun()

    st.sidebar.error("Quote refresh failed. Existing CSV was left in place.")
    with st.sidebar.expander("Collector output", expanded=True):
        st.code(output[-6000:] if output else "No collector output captured.")


def sidebar_filters(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    st.sidebar.header("Filters")

    valid_dates = df["snapshot_time"].dropna()
    start_date = end_date = None
    asset_values = ordered_unique(pd.concat([df["in_token_symbol"], df["out_token_symbol"]], ignore_index=True))
    size_labels = ordered_trade_size_labels(df)

    with st.sidebar.expander("Core filters", expanded=True):
        if not valid_dates.empty:
            min_date = valid_dates.min().date()
            max_date = valid_dates.max().date()
            date_value = st.date_input("Snapshot date", value=(min_date, max_date), min_value=min_date, max_value=max_date)
            if isinstance(date_value, tuple):
                start_date = date_value[0]
                end_date = date_value[-1] if len(date_value) > 1 else date_value[0]
            else:
                start_date = end_date = date_value

        default_assets = ["USDC"] if "USDC" in asset_values else asset_values[:1]
        selected_assets = st.multiselect(
            "Asset",
            asset_values,
            default=default_assets,
            help="Includes any observed path where the selected asset appears as either input or output.",
        )
        selected_size_labels = st.multiselect("Trade size", size_labels, default=size_labels)

    with st.sidebar.expander("Advanced filters", expanded=False):
        status_values = ["Success", "Failed"]
        selected_statuses = st.multiselect("Quote status", status_values, default=status_values)
        in_tokens = ordered_unique(df["in_token_symbol"])
        out_tokens = ordered_unique(df["out_token_symbol"])
        selected_in_tokens = st.multiselect("Input token", in_tokens, default=in_tokens)
        selected_out_tokens = st.multiselect("Output token", out_tokens, default=out_tokens)
        chains = ordered_unique(df["chain"])
        selected_chains = st.multiselect("Chain", chains, default=chains)
        include_negative = st.checkbox("Include negative execution cost", value=True)
        show_p95 = st.checkbox("Show p95 markers", value=True)
        log_y = st.checkbox("Log y-axis where useful", value=False)

        valid_pct = pd.to_numeric(df["execution_cost_pct_display"], errors="coerce").dropna()
        if valid_pct.empty:
            min_pct = max_pct = 0.0
        else:
            min_pct = float(valid_pct.min())
            max_pct = float(valid_pct.max())
        pct_range = st.slider(
            "Execution cost % range",
            min_value=float(min_pct),
            max_value=float(max(max_pct, min_pct + 0.01)),
            value=(float(min_pct), float(max_pct)),
            step=max((max_pct - min_pct) / 200, 0.01),
        )

    mask = pd.Series(True, index=df.index)
    if start_date and end_date:
        dates = df["snapshot_time"].dt.date
        mask &= dates.ge(start_date) & dates.le(end_date)

    if selected_assets:
        mask &= df["in_token_symbol"].isin(selected_assets) | df["out_token_symbol"].isin(selected_assets)
    else:
        mask &= False

    filter_values = {
        "trade_size_label": selected_size_labels,
        "quote_status": selected_statuses,
        "in_token_symbol": selected_in_tokens,
        "out_token_symbol": selected_out_tokens,
        "chain": selected_chains,
    }
    for column, selected in filter_values.items():
        mask &= df[column].isin(selected) if selected else False

    mask &= df["execution_cost_pct_display"].between(pct_range[0], pct_range[1], inclusive="both") | df["execution_cost_pct_display"].isna()
    if not include_negative:
        mask &= df["execution_cost_pct_display"].isna() | df["execution_cost_pct_display"].ge(0)

    settings = {"show_p95": show_p95, "log_y": log_y, "include_negative": include_negative}
    return df.loc[mask].copy(), settings


def render_kpi_card(label: str, value: str, help_text: str) -> None:
    st.markdown(
        f"""
        <div class="kpi-card" title="{html.escape(help_text)}">
            <div class="kpi-label">{html.escape(label)}</div>
            <div class="kpi-value">{html.escape(value)}</div>
            <div class="kpi-caption">{html.escape(help_text)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_kpi_grid(metrics: list[tuple[str, str, str]]) -> None:
    for row_start in range(0, len(metrics), 3):
        cols = st.columns(3, gap="medium")
        for idx, col in enumerate(cols):
            with col:
                if row_start + idx >= len(metrics):
                    st.empty()
                    continue
                render_kpi_card(*metrics[row_start + idx])


def valid_quotes(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["valid_quote"]].copy()


def overview_metrics(filtered_df: pd.DataFrame, valid_df: pd.DataFrame) -> list[tuple[str, str, str]]:
    total = len(filtered_df)
    if "quote_count_24h" in filtered_df.columns:
        total = int(filtered_df["quote_count_24h"].sum()) if len(filtered_df) else 0
        success = int(filtered_df["success_count_24h"].sum()) if total else 0
    else:
        success = int(filtered_df["quote_success"].sum()) if total else 0
    if valid_df.empty:
        return [
            ("Simulated quote volume", "n/a", "Successful quote rows only"),
            ("Quote attempts", f"{total:,}", "Rows after filters"),
            ("Successful quotes", format_share(success, total), "OpenOcean returned usable output"),
            ("Median quote cost", "n/a", "Input USD minus quoted output USD"),
            ("P95 quote cost", "n/a", "Upper-tail quote cost"),
            ("Worst quote cost", "n/a", "Highest execution cost %"),
        ]

    volume = valid_df["trade_size_usd"].sum()
    if "success_count_24h" in valid_df.columns:
        volume = (valid_df["trade_size_usd"] * valid_df["success_count_24h"]).sum()

    return [
        ("Simulated quote volume", format_usd(volume), "Sum of successful quote input sizes"),
        ("Quote attempts", f"{total:,}", "Rows after filters"),
        ("Successful quotes", format_share(success, total), "OpenOcean returned usable output"),
        ("Median quote cost", format_pct(valid_df["execution_cost_pct_display"].median()), "Input USD minus quoted output USD"),
        ("P95 quote cost", format_pct(pctile(valid_df["execution_cost_pct_display"], 0.95)), "Upper-tail quote cost"),
        ("Worst quote cost", format_pct(valid_df["execution_cost_pct_display"].max()), "Highest execution cost %"),
    ]


def add_pct_reference_lines(fig: go.Figure) -> None:
    for ref in REFERENCE_PCT:
        label = f"{ref:g}%"
        fig.add_hline(
            y=ref,
            line_color="rgba(99,230,190,0.86)" if ref == 0 else "rgba(248,250,252,0.42)",
            line_dash="solid" if ref == 0 else "dot",
            annotation_text=label,
            annotation_position="top left",
        )


def apply_percent_y_axis(fig: go.Figure) -> None:
    fig.update_yaxes(ticksuffix="%")


def apply_percent_x_axis(fig: go.Figure) -> None:
    fig.update_xaxes(ticksuffix="%")


def apply_percent_colorbar(fig: go.Figure) -> None:
    fig.update_layout(coloraxis_colorbar=dict(ticksuffix="%"))


def render_cost_by_size(valid_df: pd.DataFrame, settings: dict) -> None:
    hover_data = {
        "snapshot_time": "|%Y-%m-%d %H:%M:%S",
        "trade_size_usd": ":$,.0f",
        "quoted_out_value_usd": ":$,.2f",
        "execution_cost_usd": ":$,.2f",
        "execution_cost_pct_display": ":.2f",
        "openocean_price_impact": True,
        "dex_count": True,
    }
    if "quote_count_24h" in valid_df.columns:
        hover_data.update({"quote_count_24h": True, "success_rate_24h": ":.0%"})

    fig = px.line(
        valid_df.sort_values(["quote_pair", "trade_size_usd"]),
        x="trade_size_usd",
        y="execution_cost_pct_display",
        color="quote_pair",
        markers=True,
        hover_data=hover_data,
        labels={
            "trade_size_usd": "Input size (USD)",
            "execution_cost_pct_display": "Quote execution cost (%)",
            "quote_pair": "Quote pair",
        },
    )
    add_pct_reference_lines(fig)
    apply_percent_y_axis(fig)
    if settings["log_y"] and (valid_df["execution_cost_pct_display"] > 0).all():
        fig.update_yaxes(type="log")
    fig.update_xaxes(tickprefix="$", separatethousands=True)
    render_chart(fig, "Quote Execution Cost by Size", "Positive % means the quoted output USD value is below the input USD size.", height=480)


def render_pair_summary(valid_df: pd.DataFrame, settings: dict) -> None:
    summary_input = valid_df.copy()
    summary_input["_quote_count"] = summary_input["quote_count_24h"] if "quote_count_24h" in summary_input.columns else 1
    summary = (
        summary_input.groupby("quote_pair", dropna=False)
        .agg(
            quotes=("_quote_count", "sum"),
            median_pct=("execution_cost_pct_display", "median"),
            p95_pct=("execution_cost_pct_display", lambda x: x.quantile(0.95)),
            max_pct=("execution_cost_pct_display", "max"),
        )
        .reset_index()
        .sort_values("median_pct", ascending=True)
    )
    fig = px.bar(
        summary,
        x="median_pct",
        y="quote_pair",
        color="median_pct",
        orientation="h",
        color_continuous_scale="Tealrose",
        text_auto=".1f",
        hover_data={"quotes": True, "p95_pct": ":.2f", "max_pct": ":.2f"},
        labels={"quote_pair": "Quote pair", "median_pct": "Median quote cost (%)"},
    )
    fig.update_traces(texttemplate="%{x:.1f}%", textposition="outside", cliponaxis=False)
    apply_percent_x_axis(fig)
    apply_percent_colorbar(fig)
    if settings["show_p95"]:
        fig.add_trace(
            go.Scatter(
                x=summary["p95_pct"],
                y=summary["quote_pair"],
                mode="markers",
                marker=dict(size=8, symbol="diamond", line=dict(width=1, color="#0B1220"), color="#f8fafc"),
                name="P95",
                hovertemplate="%{y}<br>P95: %{x:.2f}%<extra></extra>",
            )
        )
    fig.update_yaxes(automargin=True)
    fig.update_xaxes(automargin=True)
    summary_height = min(760, max(420, 190 + 24 * len(summary)))
    render_chart(fig, "Execution Cost by Pair", "Pairs are sorted by median execution cost; optional diamonds show p95.", height=summary_height)


def render_pair_heatmap(valid_df: pd.DataFrame) -> None:
    if valid_df.empty:
        st.info("No successful quotes available for the heatmap.")
        return
    pair_order = valid_df.groupby("quote_pair")["execution_cost_pct_display"].median().sort_values(ascending=False).index.tolist()
    matrix = pd.pivot_table(
        valid_df,
        index="quote_pair",
        columns="trade_size_label",
        values="execution_cost_pct_display",
        aggfunc="median",
    )
    size_order = ordered_trade_size_labels(valid_df)
    matrix = matrix.reindex(index=pair_order)
    matrix = matrix.reindex(columns=[size for size in size_order if size in matrix.columns])
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
    fig.update_layout(
        coloraxis_colorbar=dict(
            tickvals=[0, 0.3, 0.6, 1],
            ticktext=["0%", "0.3%", "0.6%", "1%+"],
        )
    )
    heatmap_height = min(760, max(420, 180 + 18 * len(matrix)))
    render_chart(fig, "Pair x Size Heatmap", "Pairs are sorted by aggregate median execution cost across sizes.", height=heatmap_height)


def render_dex_usage(valid_df: pd.DataFrame) -> None:
    dex_df = explode_dexes(valid_df)
    if dex_df.empty:
        st.info("No DEX route data found in the filtered quotes.")
        return
    summary = (
        dex_df.groupby("dex")
        .agg(quotes=("quote_pair", "count"), median_pct=("execution_cost_pct_display", "median"))
        .reset_index()
        .sort_values(["quotes", "median_pct"], ascending=[False, False])
        .head(14)
    )
    fig = px.bar(
        summary,
        x="quotes",
        y="dex",
        color="median_pct",
        orientation="h",
        color_continuous_scale="Tealrose",
        hover_data={"median_pct": ":.2f"},
        labels={"quotes": "Quote route appearances", "dex": "DEX", "median_pct": "Median %"},
    )
    fig.update_yaxes(autorange="reversed")
    apply_percent_colorbar(fig)
    render_chart(fig, "DEXs Used by OpenOcean", "Counts route appearances, so one quote can contribute multiple DEXs.", height=380)


def render_overview(filtered_df: pd.DataFrame, settings: dict) -> None:
    valid_df = valid_quotes(filtered_df)
    render_kpi_grid(overview_metrics(filtered_df, valid_df))
    st.markdown(
        '<div class="section-note">Quote execution cost = requested input USD size minus quoted output USD value. Gas is shown separately and is not included in cost.</div>',
        unsafe_allow_html=True,
    )

    if valid_df.empty:
        st.info("No successful quote rows match the current filters.")
        return

    render_cost_by_size(valid_df, settings)
    render_pair_summary(valid_df, settings)
    render_pair_heatmap(valid_df)
    render_dex_usage(valid_df)


QUOTE_COLUMNS = [
    "snapshot_time",
    "chain",
    "quote_pair",
    "quote_count_24h",
    "success_rate_24h",
    "trade_size_usd",
    "quoted_out_value_usd",
    "execution_cost_usd",
    "execution_cost_pct_display",
    "openocean_price_impact",
    "estimated_gas",
    "quote_success",
    "failed_reason",
    "request_url",
]


def quote_table(df: pd.DataFrame, view: str, n: int) -> pd.DataFrame:
    valid_df = valid_quotes(df)
    if view == "Failed quotes":
        selected = df[~df["quote_success"]].copy().sort_values(["target_token", "trade_size_usd"])
    elif valid_df.empty:
        selected = df.copy()
    elif view == "Worst quotes by execution cost %":
        selected = valid_df.sort_values("execution_cost_pct_display", ascending=False)
    elif view == "Worst quotes by execution cost USD":
        selected = valid_df.sort_values("execution_cost_usd", ascending=False)
    elif view == "Largest input quotes":
        selected = valid_df.sort_values("trade_size_usd", ascending=False)
    else:
        selected = valid_df[valid_df["execution_cost_pct_display"] < 0].sort_values("execution_cost_pct_display")
    return selected[[col for col in QUOTE_COLUMNS if col in selected.columns]].head(n)


def render_quote_explorer(filtered_df: pd.DataFrame) -> None:
    st.markdown("### Quote Explorer")
    st.markdown(
        '<div class="section-note">One row equals one OpenOcean quote attempt; filters apply across this table and all charts.</div>',
        unsafe_allow_html=True,
    )

    controls = st.columns([3, 1], gap="medium")
    view = controls[0].selectbox(
        "Table view",
        [
            "Worst quotes by execution cost %",
            "Worst quotes by execution cost USD",
            "Largest input quotes",
            "Negative execution cost quotes",
            "Failed quotes",
        ],
    )
    n = controls[1].selectbox("Rows", [25, 50, 100, 200, 500], index=1)
    table = quote_table(filtered_df, view, n)

    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        height=520,
        column_config={
            "snapshot_time": st.column_config.DatetimeColumn("Snapshot time", format="YYYY-MM-DD HH:mm:ss", width="medium"),
            "quote_pair": st.column_config.TextColumn("Quote pair", width="medium"),
            "quote_count_24h": st.column_config.NumberColumn("24h samples", format="%.0f", width="small"),
            "success_rate_24h": st.column_config.NumberColumn("24h success", format="%.0%%", width="small"),
            "trade_size_usd": st.column_config.NumberColumn("Input size", format="$%.0f", width="small"),
            "input_amount_decimals": st.column_config.NumberColumn("Input amount", format="%.6f"),
            "quoted_out_amount_decimals": st.column_config.NumberColumn("Quoted output", format="%.6f"),
            "quoted_out_value_usd": st.column_config.NumberColumn("Quoted output USD", format="$%.2f", width="small"),
            "execution_cost_usd": st.column_config.NumberColumn("Execution cost USD", format="$%.2f", width="small"),
            "execution_cost_pct_display": st.column_config.NumberColumn("Execution cost %", format="%.2f%%", width="small"),
            "estimated_gas": st.column_config.NumberColumn("Estimated gas", format="%.0f", width="small"),
            "quote_success": st.column_config.CheckboxColumn("Success", width="small"),
            "request_url": st.column_config.LinkColumn("OpenOcean request", display_text="Open quote", width="small"),
        },
    )
    st.download_button(
        "Download table",
        data=to_csv_bytes(table),
        file_name="etherfi_cash_openocean_quote_explorer.csv",
        mime="text/csv",
        width="stretch",
    )


def quality_metrics(df: pd.DataFrame) -> list[tuple[str, str, str]]:
    summary = quote_quality_summary(df)
    return [
        ("Total rows", f"{summary['total_rows']:,}", "Quote attempts in current filter"),
        ("Successful quotes", format_share(summary["successful_quotes"], summary["total_rows"]), "Returned usable output value"),
        ("Failed quotes", f"{summary['failed_quotes']:,}", "No usable quote/output value"),
        ("Null execution cost", f"{summary['null_execution_cost']:,}", "Cannot calculate %"),
        ("Negative execution cost", f"{summary['negative_execution_cost']:,}", "Apparent quote improvement/noise"),
        ("> 5% cost", f"{summary['high_execution_cost_gt_500_bps']:,}", "High positive quote cost"),
    ]


def render_methodology(raw_df: pd.DataFrame, filtered_df: pd.DataFrame, source: str) -> None:
    st.markdown("### Methodology")
    st.markdown(
        """
        <div class="method-card">
            <b>Quote Execution Cost</b> compares requested USD input size with OpenOcean's quoted output USD value. Gas is recorded separately.
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Formula details", expanded=False):
        st.markdown(
            """
            - `execution_cost_usd = trade_size_usd - quoted_out_value_usd`
            - `execution_cost_pct = execution_cost_usd / trade_size_usd`
            - Dashboard values show `execution_cost_pct * 100` as a readable percent.
            - Positive % means the quoted output USD value is lower than the requested input size.
            - Negative % means apparent improvement or quote/pricing noise.
            """
        )

    st.markdown("### Data Quality")
    st.caption(f"Source: `{source}`")
    render_kpi_grid(quality_metrics(filtered_df))

    left, right = st.columns(2, gap="medium")
    with left:
        status = filtered_df["quote_status"].value_counts().reindex(["Success", "Failed"]).dropna().reset_index()
        status.columns = ["quote_status", "rows"]
        fig = px.bar(status, x="quote_status", y="rows", color="quote_status", text_auto=True, labels={"quote_status": "Quote status", "rows": "Rows"})
        render_chart(fig, "Quote Status", None, height=330)

    with right:
        failures = filtered_df[~filtered_df["quote_success"]].copy()
        if failures.empty:
            st.success("No failed quotes in the current filter.")
        else:
            failure_counts = failures.groupby("failed_reason").size().reset_index(name="rows").sort_values("rows", ascending=False)
            fig = px.bar(failure_counts, x="rows", y="failed_reason", orientation="h", text_auto=True, labels={"rows": "Rows", "failed_reason": "Reason"})
            fig.update_yaxes(autorange="reversed")
            render_chart(fig, "Failure Reasons", None, height=330)

    with st.expander("Failed quote rows", expanded=False):
        failed_cols = ["snapshot_time", "quote_pair", "trade_size_usd", "failed_reason", "response_status_code", "request_url"]
        st.dataframe(filtered_df.loc[~filtered_df["quote_success"], [col for col in failed_cols if col in filtered_df.columns]], hide_index=True, width="stretch", height=300)

    with st.expander("Token decimal sources", expanded=False):
        in_sources = filtered_df[["in_token_symbol", "in_token_address", "in_token_decimals", "in_token_decimals_source"]].rename(
            columns={"in_token_symbol": "token_symbol", "in_token_address": "token_address", "in_token_decimals": "decimals", "in_token_decimals_source": "source"}
        )
        out_sources = filtered_df[["out_token_symbol", "out_token_address", "out_token_decimals", "out_token_decimals_source"]].rename(
            columns={"out_token_symbol": "token_symbol", "out_token_address": "token_address", "out_token_decimals": "decimals", "out_token_decimals_source": "source"}
        )
        sources = pd.concat([in_sources, out_sources], ignore_index=True).drop_duplicates().sort_values(["token_symbol", "token_address"])
        st.dataframe(sources, hide_index=True, width="stretch", height=360)

    with st.expander("Column null counts", expanded=False):
        null_counts = raw_df.isna().sum().sort_values(ascending=False).reset_index()
        null_counts.columns = ["column", "null_rows"]
        null_counts["null_share"] = null_counts["null_rows"] / len(raw_df) * 100 if len(raw_df) else 0
        st.dataframe(
            null_counts,
            hide_index=True,
            width="stretch",
            height=360,
            column_config={"null_share": st.column_config.NumberColumn("Null share", format="%.2f%%")},
        )


def render_downloads(filtered_df: pd.DataFrame) -> None:
    st.sidebar.header("Export")
    st.sidebar.download_button(
        "Download filtered quote data",
        data=to_csv_bytes(filtered_df),
        file_name="etherfi_cash_filtered_openocean_quotes.csv",
        mime="text/csv",
        width="stretch",
    )


def main() -> None:
    inject_css()
    df, source, basis = load_data_ui()
    render_refresh_controls(df)
    filtered_df, settings = sidebar_filters(df)
    render_downloads(filtered_df)

    st.markdown(
        """
        <div class="dashboard-hero">
            <div class="eyebrow">ether.fi Cash - observed OpenOcean trade paths</div>
            <h1>Current Liquidity Simulation</h1>
            <p>Optimism OpenOcean quote snapshots for observed ether.fi Cash trade paths, with a toggle between the latest quote run and a rolling 24h median baseline.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if filtered_df.empty:
        st.warning("No rows match the current filters.")
        return

    valid_df = valid_quotes(filtered_df)
    start = filtered_df["snapshot_time"].min()
    end = filtered_df["snapshot_time"].max()
    date_text = "n/a" if pd.isna(start) or pd.isna(end) else f"{start:%Y-%m-%d %H:%M:%S} to {end:%Y-%m-%d %H:%M:%S} UTC"
    st.caption(
        f"{basis} | {len(filtered_df):,} rows | {len(valid_df):,} successful quotes | {date_text}"
    )

    overview_tab, explorer_tab, methodology_tab = st.tabs(["Overview", "Quote Explorer", "Methodology & Data Quality"])
    with overview_tab:
        render_overview(filtered_df, settings)
    with explorer_tab:
        render_quote_explorer(filtered_df)
    with methodology_tab:
        render_methodology(df, filtered_df, source)


if __name__ == "__main__":
    main()
