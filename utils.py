from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


DEFAULT_DATA_PATH = Path("data/openocean_optimism_quote_snapshots.csv")
DEFAULT_HISTORY_PATH = Path("data/openocean_optimism_quote_history.csv")
HISTORICAL_DATA_PATH = Path("data/Investigate_Cash_Swap_Slippage.csv")

EXPECTED_COLUMNS = [
    "snapshot_time_utc",
    "snapshot_run_id",
    "chain",
    "trade_size_usd",
    "direction",
    "in_token_symbol",
    "in_token_address",
    "in_token_decimals",
    "out_token_symbol",
    "out_token_address",
    "out_token_decimals",
    "estimated_in_token_price_usd",
    "price_source",
    "amount_decimals",
    "input_amount_decimals",
    "input_amount_raw",
    "quoted_out_amount_raw",
    "quoted_out_amount_decimals",
    "quoted_out_value_usd",
    "quoted_out_value_source",
    "execution_cost_usd",
    "execution_cost_bps",
    "execution_cost_pct",
    "openocean_price_impact",
    "estimated_gas",
    "gas_price",
    "route_summary",
    "dexes_used",
    "quote_success",
    "error_message",
    "request_url",
    "response_status_code",
    "in_token_decimals_source",
    "out_token_decimals_source",
]

NUMERIC_COLUMNS = [
    "trade_size_usd",
    "in_token_decimals",
    "out_token_decimals",
    "estimated_in_token_price_usd",
    "input_amount_decimals",
    "quoted_out_amount_decimals",
    "quoted_out_value_usd",
    "execution_cost_usd",
    "execution_cost_bps",
    "execution_cost_pct",
    "estimated_gas",
    "gas_price",
    "response_status_code",
]

STRING_COLUMNS = [
    "chain",
    "snapshot_run_id",
    "direction",
    "in_token_symbol",
    "in_token_address",
    "out_token_symbol",
    "out_token_address",
    "price_source",
    "amount_decimals",
    "input_amount_raw",
    "quoted_out_amount_raw",
    "quoted_out_value_source",
    "openocean_price_impact",
    "route_summary",
    "dexes_used",
    "error_message",
    "request_url",
    "in_token_decimals_source",
    "out_token_decimals_source",
]

TRADE_SIZE_ORDER = [50_000, 100_000, 150_000, 200_000]
DIRECTION_ORDER = ["actual_trade_path", "token_to_usdc", "usdc_to_token", "all_pairs"]


def read_csv(path_or_buffer) -> pd.DataFrame:
    return pd.read_csv(path_or_buffer, low_memory=False)


def safe_numeric(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    cleaned = (
        series.astype("string")
        .str.replace(",", "", regex=False)
        .str.strip()
        .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA})
    )
    return pd.to_numeric(cleaned, errors="coerce")


def safe_string(series: pd.Series, fill: str = "Unknown") -> pd.Series:
    return (
        series.astype("string")
        .str.strip()
        .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA})
        .fillna(fill)
    )


def safe_bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    values = series.astype("string").str.strip().str.lower()
    return values.isin(["true", "1", "yes", "y", "success"])


def parse_price_impact_pct(series: pd.Series) -> pd.Series:
    cleaned = series.astype("string").str.replace("%", "", regex=False).str.strip()
    return pd.to_numeric(cleaned, errors="coerce")


def format_trade_size_label(value: object) -> str:
    if pd.isna(value):
        return "Unknown"
    value_float = float(value)
    if value_float >= 1_000_000:
        return f"${value_float / 1_000_000:.1f}M"
    if value_float >= 1_000:
        return f"${value_float / 1_000:.0f}k"
    return f"${value_float:,.0f}"


def direction_label(value: object) -> str:
    mapping = {
        "actual_trade_path": "Observed trade path",
        "token_to_usdc": "Token -> USDC",
        "usdc_to_token": "USDC -> Token",
        "all_pairs": "All pairs",
    }
    return mapping.get(str(value), str(value).replace("_", " ").title())


def clean_quote_data(raw_df: pd.DataFrame) -> pd.DataFrame:
    df = raw_df.copy()
    df.columns = [str(col).strip() for col in df.columns]

    for column in EXPECTED_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA

    for column in NUMERIC_COLUMNS:
        df[column] = safe_numeric(df[column])

    for column in STRING_COLUMNS:
        df[column] = safe_string(df[column], fill="")

    df["quote_success"] = safe_bool(df["quote_success"])
    df["snapshot_time"] = pd.to_datetime(df["snapshot_time_utc"], errors="coerce", utc=True).dt.tz_convert(None)
    df["snapshot_date"] = df["snapshot_time"].dt.date
    df["snapshot_minute"] = df["snapshot_time"].dt.floor("min")

    df["direction_label"] = df["direction"].map(direction_label)
    df["quote_pair"] = df["in_token_symbol"] + " -> " + df["out_token_symbol"]
    df["target_token"] = np.where(df["in_token_symbol"].eq("USDC"), df["out_token_symbol"], df["in_token_symbol"])
    df["trade_size_label"] = df["trade_size_usd"].map(format_trade_size_label)
    df["trade_size_sort"] = df["trade_size_usd"].fillna(0)
    df["execution_cost_pct_display"] = df["execution_cost_pct"] * 100
    df["openocean_price_impact_pct"] = parse_price_impact_pct(df["openocean_price_impact"])
    df["quote_status"] = np.where(df["quote_success"], "Success", "Failed")
    df["valid_quote"] = (
        df["quote_success"]
        & df["execution_cost_bps"].notna()
        & df["execution_cost_usd"].notna()
        & df["trade_size_usd"].notna()
        & df["quoted_out_value_usd"].notna()
    )
    df["negative_execution_cost"] = df["execution_cost_bps"] < 0
    df["high_execution_cost_gt_500_bps"] = df["execution_cost_bps"] > 500
    df["missing_output_value"] = df["quoted_out_value_usd"].isna()
    df["missing_route"] = df["dexes_used"].eq("") | df["route_summary"].eq("")
    df["failed_reason"] = df["error_message"].replace("", pd.NA).fillna("No error")
    df["dex_count"] = df["dexes_used"].map(count_dexes)

    return df


def latest_nonempty(series: pd.Series) -> str:
    values = series.astype("string").str.strip()
    values = values.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA}).dropna()
    return "" if values.empty else str(values.iloc[-1])


def joined_unique(values: pd.Series) -> str:
    seen: list[str] = []
    for value in values.astype("string").dropna():
        for part in str(value).split(","):
            clean = part.strip()
            if clean and clean not in seen:
                seen.append(clean)
    return ", ".join(seen)


def aggregate_quote_history(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse raw quote attempts into one median row per pair and trade size."""
    if df.empty:
        return df.copy()

    group_cols = [
        "chain",
        "direction",
        "in_token_symbol",
        "in_token_address",
        "in_token_decimals",
        "out_token_symbol",
        "out_token_address",
        "out_token_decimals",
        "trade_size_usd",
        "in_token_decimals_source",
        "out_token_decimals_source",
    ]
    group_cols = [col for col in group_cols if col in df.columns]
    ordered = df.sort_values("snapshot_time")
    grouped = ordered.groupby(group_cols, dropna=False)

    summary = grouped.agg(
        quote_count_24h=("quote_success", "size"),
        success_count_24h=("quote_success", "sum"),
        snapshot_time=("snapshot_time", "max"),
        estimated_in_token_price_usd=("estimated_in_token_price_usd", "median"),
        input_amount_decimals=("input_amount_decimals", "median"),
        quoted_out_amount_decimals=("quoted_out_amount_decimals", "median"),
        quoted_out_value_usd=("quoted_out_value_usd", "median"),
        execution_cost_usd=("execution_cost_usd", "median"),
        execution_cost_bps=("execution_cost_bps", "median"),
        execution_cost_pct=("execution_cost_pct", "median"),
        estimated_gas=("estimated_gas", "median"),
        gas_price=("gas_price", "median"),
        amount_decimals=("amount_decimals", latest_nonempty),
        input_amount_raw=("input_amount_raw", latest_nonempty),
        quoted_out_amount_raw=("quoted_out_amount_raw", latest_nonempty),
        price_source=("price_source", latest_nonempty),
        quoted_out_value_source=("quoted_out_value_source", latest_nonempty),
        openocean_price_impact=("openocean_price_impact", latest_nonempty),
        route_summary=("route_summary", latest_nonempty),
        dexes_used=("dexes_used", joined_unique),
        error_message=("error_message", latest_nonempty),
        request_url=("request_url", latest_nonempty),
        response_status_code=("response_status_code", "max"),
    ).reset_index()

    summary["quote_success"] = summary["success_count_24h"].gt(0)
    summary["success_rate_24h"] = summary["success_count_24h"] / summary["quote_count_24h"]
    summary["snapshot_time_utc"] = summary["snapshot_time"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    summary["snapshot_run_id"] = "24h_median"
    summary["quote_pair"] = summary["in_token_symbol"] + " -> " + summary["out_token_symbol"]
    summary["target_token"] = np.where(summary["in_token_symbol"].eq("USDC"), summary["out_token_symbol"], summary["in_token_symbol"])
    summary["direction_label"] = summary["direction"].map(direction_label)
    summary["trade_size_label"] = summary["trade_size_usd"].map(format_trade_size_label)
    summary["trade_size_sort"] = summary["trade_size_usd"].fillna(0)
    summary["execution_cost_pct_display"] = summary["execution_cost_pct"] * 100
    summary["openocean_price_impact_pct"] = parse_price_impact_pct(summary["openocean_price_impact"])
    summary["quote_status"] = np.where(summary["quote_success"], "Success", "Failed")
    summary["valid_quote"] = (
        summary["quote_success"]
        & summary["execution_cost_bps"].notna()
        & summary["execution_cost_usd"].notna()
        & summary["trade_size_usd"].notna()
        & summary["quoted_out_value_usd"].notna()
    )
    summary["negative_execution_cost"] = summary["execution_cost_bps"] < 0
    summary["high_execution_cost_gt_500_bps"] = summary["execution_cost_bps"] > 500
    summary["missing_output_value"] = summary["quoted_out_value_usd"].isna()
    summary["missing_route"] = summary["dexes_used"].eq("") | summary["route_summary"].eq("")
    summary["failed_reason"] = summary["error_message"].replace("", pd.NA).fillna("No error")
    summary["dex_count"] = summary["dexes_used"].map(count_dexes)
    summary["basis"] = "Last 24h median"
    return summary


def count_dexes(value: object) -> int:
    if pd.isna(value) or not str(value).strip():
        return 0
    return len([part for part in str(value).split(",") if part.strip()])


def ordered_unique(values: Iterable[object], preferred_order: list[object] | None = None) -> list[object]:
    present = pd.Series(values).dropna().unique().tolist()
    if preferred_order:
        ordered = [item for item in preferred_order if item in present]
        ordered.extend(sorted(item for item in present if item not in ordered))
        return ordered
    return sorted(present)


def ordered_trade_size_labels(df: pd.DataFrame) -> list[str]:
    ordered = (
        df[["trade_size_usd", "trade_size_label"]]
        .dropna(subset=["trade_size_usd"])
        .drop_duplicates()
        .sort_values("trade_size_usd")["trade_size_label"]
        .tolist()
    )
    return ordered


def pctile(series: pd.Series, q: float) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return np.nan
    return float(values.quantile(q))


def format_usd(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    sign = "-" if value < 0 else ""
    value_abs = abs(float(value))
    if value_abs >= 1_000_000_000:
        return f"{sign}${value_abs / 1_000_000_000:.2f}B"
    if value_abs >= 1_000_000:
        return f"{sign}${value_abs / 1_000_000:.2f}M"
    if value_abs >= 1_000:
        return f"{sign}${value_abs / 1_000:.1f}k"
    return f"{sign}${value_abs:,.2f}"


def format_number(value: float | int | None, digits: int = 0) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):,.{digits}f}"


def format_bps(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):,.1f} bps"


def format_pct(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):,.2f}%"


def format_share(count: int, total: int) -> str:
    if total <= 0:
        return "0 (0.0%)"
    return f"{count:,} ({count / total * 100:.1f}%)"


def quote_quality_summary(df: pd.DataFrame) -> dict[str, int]:
    total = int(len(df))
    success = int(df["quote_success"].sum()) if total else 0
    return {
        "total_rows": total,
        "successful_quotes": success,
        "failed_quotes": total - success,
        "valid_quotes": int(df["valid_quote"].sum()) if total else 0,
        "null_execution_cost": int(df["execution_cost_bps"].isna().sum()) if total else 0,
        "null_output_value": int(df["quoted_out_value_usd"].isna().sum()) if total else 0,
        "negative_execution_cost": int((df["execution_cost_bps"] < 0).sum()) if total else 0,
        "high_execution_cost_gt_500_bps": int((df["execution_cost_bps"] > 500).sum()) if total else 0,
        "missing_route": int(df["missing_route"].sum()) if total else 0,
    }


def explode_dexes(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, row in df.iterrows():
        dexes = [part.strip() for part in str(row.get("dexes_used", "")).split(",") if part.strip()]
        for dex in dexes:
            rows.append(
                {
                    "dex": dex,
                    "quote_pair": row.get("quote_pair"),
                    "direction_label": row.get("direction_label"),
                    "trade_size_usd": row.get("trade_size_usd"),
                    "execution_cost_bps": row.get("execution_cost_bps"),
                    "execution_cost_pct_display": row.get("execution_cost_pct_display"),
                }
            )
    return pd.DataFrame(rows)


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")
