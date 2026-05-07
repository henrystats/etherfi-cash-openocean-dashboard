#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, getcontext
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pandas as pd
import requests


getcontext().prec = 60

CHAIN = "optimism"
BASE_URL = "https://open-api.openocean.finance/v4/optimism"
QUOTE_URL = f"{BASE_URL}/quote"
GAS_PRICE_URL = f"{BASE_URL}/gasPrice"
TOKEN_LIST_URL = f"{BASE_URL}/tokenList"

DEFAULT_SIZES = [Decimal("50000"), Decimal("100000"), Decimal("150000"), Decimal("200000")]
DEFAULT_GAS_PRICE_DECIMALS = "1000000"
USDC_ADDRESS = "0x0b2c639c533813f4aa9d7837caf62653d097ff85"
DEFAULT_TRADE_PATHS_PATH = Path("data/trade_paths.csv")

# Decimals are intentionally centralized here so they are easy to audit/edit.
# eBTC, beHYPE, and WHYPE are seeded from the task prompt and marked as
# manual_config_unconfirmed until OpenOcean tokenList confirms them at runtime.
TOKENS: list[dict[str, Any]] = [
    {
        "token_address": "0x5a7facb970d094b6c7ff1df0ea68d99e6e73cbff",
        "token_symbol": "weETH",
        "decimals": 18,
        "decimals_source": "manual_config_known",
    },
    {
        "token_address": "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58",
        "token_symbol": "USDT",
        "decimals": 6,
        "decimals_source": "manual_config_known",
    },
    {
        "token_address": "0x4200000000000000000000000000000000000006",
        "token_symbol": "WETH",
        "decimals": 18,
        "decimals_source": "manual_config_known",
    },
    {
        "token_address": "0xdcb612005417dc906ff72c87df732e5a90d49e11",
        "token_symbol": "EURC",
        "decimals": 6,
        "decimals_source": "manual_config_known",
    },
    {
        "token_address": "0x657e8c867d8b37dcc18fa4caead9c45eb088c642",
        "token_symbol": "eBTC",
        "decimals": 8,
        "decimals_source": "manual_config_unconfirmed",
    },
    {
        "token_address": "0xa519afbc91986c0e7501d7e34968fee51cd901ac",
        "token_symbol": "beHYPE",
        "decimals": 18,
        "decimals_source": "manual_config_unconfirmed",
    },
    {
        "token_address": USDC_ADDRESS,
        "token_symbol": "USDC",
        "decimals": 6,
        "decimals_source": "manual_config_known",
    },
    {
        "token_address": "0xe0080d2f853ecddbd81a643dc10da075df26fd3f",
        "token_symbol": "ETHFI",
        "decimals": 18,
        "decimals_source": "manual_config_known",
    },
    {
        "token_address": "0xd83e3d560ba6f05094d9d8b3eb8aaea571d1864e",
        "token_symbol": "WHYPE",
        "decimals": 18,
        "decimals_source": "manual_config_unconfirmed",
    },
]

OUTPUT_COLUMNS = [
    "snapshot_time_utc",
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


@dataclass
class ApiResult:
    url: str
    params: dict[str, str]
    request_url: str
    status_code: int | None
    json_data: Any
    text: str | None
    error: str | None
    attempts: int


class RateLimiter:
    """Small client-side limiter for OpenOcean's public API.

    The docs mention a 20 requests / 10 seconds public-plan limit, but the
    live 429 response can enforce 1 r/s. This limiter defaults below that
    observed ceiling so reruns are less likely to get temporarily blocked.
    """

    def __init__(self, min_interval_seconds: float, max_requests_per_window: int, window_seconds: float = 10.0):
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.max_requests_per_window = max(1, max_requests_per_window)
        self.window_seconds = max(1.0, window_seconds)
        self.request_times: list[float] = []

    def wait(self) -> None:
        now = time.monotonic()
        self.request_times = [t for t in self.request_times if now - t < self.window_seconds]

        if self.request_times:
            elapsed = now - self.request_times[-1]
            if elapsed < self.min_interval_seconds:
                sleep_for = self.min_interval_seconds - elapsed
                print(f"  rate-limit sleep {sleep_for:.2f}s")
                time.sleep(sleep_for)

        now = time.monotonic()
        self.request_times = [t for t in self.request_times if now - t < self.window_seconds]
        if len(self.request_times) >= self.max_requests_per_window:
            sleep_for = self.window_seconds - (now - self.request_times[0]) + 0.05
            if sleep_for > 0:
                print(f"  rate-limit window sleep {sleep_for:.2f}s")
                time.sleep(sleep_for)

        self.request_times.append(time.monotonic())


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def run_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def decimal_to_output(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")


def raw_units(amount: Decimal, decimals: int) -> str:
    scale = Decimal(10) ** decimals
    return str((amount * scale).to_integral_value(rounding=ROUND_DOWN))


def human_amount(raw_value: Any, decimals: int) -> Decimal | None:
    raw_decimal = decimal_or_none(raw_value)
    if raw_decimal is None:
        return None
    return raw_decimal / (Decimal(10) ** decimals)


def prepared_url(url: str, params: dict[str, str]) -> str:
    return f"{url}?{urlencode(params)}" if params else url


def retry_after_seconds(response: requests.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    parsed = decimal_or_none(value)
    return float(parsed) if parsed is not None and parsed >= 0 else None


def request_json(
    session: requests.Session,
    url: str,
    params: dict[str, str] | None,
    max_retries: int,
    rate_limiter: RateLimiter,
    max_429_sleep: float,
    timeout: int = 30,
) -> ApiResult:
    params = params or {}
    request_url = prepared_url(url, params)
    last_status: int | None = None
    last_json: Any = None
    last_text: str | None = None
    last_error: str | None = None

    for attempt in range(1, max_retries + 1):
        try:
            rate_limiter.wait()
            response = session.get(url, params=params, timeout=timeout)
            last_status = response.status_code
            last_text = response.text
            try:
                last_json = response.json()
            except ValueError:
                last_json = None
                last_error = "response was not valid JSON"

            body_code = last_json.get("code") if isinstance(last_json, dict) else None
            if response.ok and (body_code in (None, 200, "200")):
                return ApiResult(url, params, request_url, last_status, last_json, last_text, None, attempt)

            last_error = extract_error_message(last_json) or f"HTTP {response.status_code}"
            if response.status_code == 429:
                hinted_sleep = retry_after_seconds(response)
                if hinted_sleep is None:
                    hinted_sleep = 30.0 * attempt
                delay = min(float(max_429_sleep), hinted_sleep)
                if attempt < max_retries and delay > 0:
                    print(f"  429 rate limit from OpenOcean; sleeping {delay:.1f}s before retry")
                    time.sleep(delay)
                    continue
        except requests.RequestException as exc:
            last_error = str(exc)

        if attempt < max_retries:
            delay = min(float(max_429_sleep), 2 ** (attempt - 1))
            print(f"  retry {attempt}/{max_retries - 1} after error: {last_error}; sleeping {delay:.1f}s")
            time.sleep(delay)

    return ApiResult(url, params, request_url, last_status, last_json, last_text, last_error, max_retries)


def write_raw(raw_handle, record_type: str, context: dict[str, Any], result: ApiResult) -> None:
    raw_record = {
        "record_type": record_type,
        "snapshot_time_utc": now_utc(),
        "chain": CHAIN,
        "context": context,
        "request_url": result.request_url,
        "response_status_code": result.status_code,
        "attempts": result.attempts,
        "error": result.error,
        "response_json": result.json_data,
        "response_text": None if result.json_data is not None else result.text,
    }
    raw_handle.write(json.dumps(raw_record, default=str) + "\n")
    raw_handle.flush()


def data_section(response_json: Any) -> dict[str, Any]:
    if isinstance(response_json, dict) and isinstance(response_json.get("data"), dict):
        return response_json["data"]
    return response_json if isinstance(response_json, dict) else {}


def extract_error_message(response_json: Any) -> str | None:
    if not isinstance(response_json, dict):
        return None
    for key in ("message", "msg", "error", "errorMessage", "desc"):
        value = response_json.get(key)
        if value:
            return str(value)
    data = response_json.get("data")
    if isinstance(data, dict):
        for key in ("message", "msg", "error", "errorMessage", "desc"):
            value = data.get(key)
            if value:
                return str(value)
    return None


def recursive_find(obj: Any, keys: set[str]) -> Any:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in keys:
                return value
        for value in obj.values():
            found = recursive_find(value, keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = recursive_find(item, keys)
            if found is not None:
                return found
    return None


def token_price_usd_from_response(data: dict[str, Any], side: str) -> Decimal | None:
    token = data.get(f"{side}Token")
    if not isinstance(token, dict):
        return None
    return decimal_or_none(token.get("usd") or token.get("price") or token.get("priceUsd"))


def token_decimals_from_response(data: dict[str, Any], side: str) -> int | None:
    token = data.get(f"{side}Token")
    if not isinstance(token, dict):
        return None
    decimals = token.get("decimals")
    if decimals is None:
        return None
    try:
        return int(decimals)
    except (TypeError, ValueError):
        return None


def direct_usd_value(data: dict[str, Any], side: str) -> tuple[Decimal | None, str | None]:
    token = data.get(f"{side}Token")
    if isinstance(token, dict):
        # OpenOcean commonly uses token.volume as the USD value for the quoted side.
        for key in ("volume", "valueUsd", "valueUSD", "amountUsd", "amountUSD"):
            parsed = decimal_or_none(token.get(key))
            if parsed is not None:
                return parsed, f"{side}Token.{key}"

    candidate_keys = [
        f"{side}Usd",
        f"{side}USD",
        f"{side}ValueUsd",
        f"{side}ValueUSD",
        f"{side}AmountUsd",
        f"{side}AmountUSD",
        f"{side}TokenUsd",
        f"{side}TokenUSD",
    ]
    for key in candidate_keys:
        value = recursive_find(data, {key})
        parsed = decimal_or_none(value)
        if parsed is not None:
            return parsed, key
    return None, None


def extract_out_amount_raw(data: dict[str, Any]) -> str | None:
    for key in ("outAmount", "outAmountDecimals", "toTokenAmount", "amountOut", "outputAmount"):
        value = recursive_find(data, {key})
        if value is not None:
            return str(value)
    return None


def extract_in_amount_raw(data: dict[str, Any]) -> str | None:
    for key in ("inAmount", "inAmountDecimals", "fromTokenAmount", "amountIn", "inputAmount"):
        value = recursive_find(data, {key})
        if value is not None:
            return str(value)
    return None


def looks_like_dex_name(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    if text.startswith("0x") or len(text) > 80:
        return False
    return True


def extract_dexes(obj: Any, path: tuple[str, ...] = ()) -> list[str]:
    dexes: list[str] = []
    dex_keys = {"dexCode", "dexName", "dex", "protocol", "source", "exchange", "routerName"}
    contextual_keys = {"code", "name"}
    context_words = ("dex", "route", "path", "swap", "protocol")

    if isinstance(obj, dict):
        for key, value in obj.items():
            key_path = path + (str(key),)
            lower_path = " ".join(key_path).lower()
            if key in dex_keys and looks_like_dex_name(value):
                dexes.append(value.strip())
            elif key in contextual_keys and any(word in lower_path for word in context_words) and looks_like_dex_name(value):
                dexes.append(value.strip())
            dexes.extend(extract_dexes(value, key_path))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            dexes.extend(extract_dexes(item, path + (str(idx),)))

    deduped: list[str] = []
    for dex in dexes:
        if dex not in deduped:
            deduped.append(dex)
    return deduped


def route_summary(data: dict[str, Any], dexes: list[str]) -> str | None:
    if dexes:
        shown = ", ".join(dexes[:10])
        suffix = "" if len(dexes) <= 10 else f", +{len(dexes) - 10} more"
        return f"{len(dexes)} dex route(s): {shown}{suffix}"
    for key in ("path", "routes", "route", "swapPath"):
        value = data.get(key)
        if value:
            return json.dumps(value, default=str)[:500]
    return None


def parse_price_impact(data: dict[str, Any]) -> str | None:
    value = recursive_find(data, {"price_impact", "priceImpact", "priceImpactPercent", "priceImpactBps"})
    return None if value is None else str(value)


def parse_estimated_gas(data: dict[str, Any]) -> str | None:
    value = recursive_find(data, {"estimatedGas", "estimateGas", "gas", "gasLimit"})
    return None if value is None else str(value)


def parse_gas_price(data: dict[str, Any]) -> str | None:
    if not data:
        return None
    direct = recursive_find(data, {"gasPrice", "gasPriceDecimals", "legacyGasPrice"})
    if direct is not None:
        return str(direct)
    for section_name in ("standard", "fast", "instant"):
        section = data.get(section_name)
        if isinstance(section, dict):
            for key in ("maxFeePerGas", "legacyGasPrice", "gasPrice", "gasPriceDecimals"):
                if section.get(key) is not None:
                    return str(section[key])
        elif section is not None:
            return str(section)
    if data.get("base") is not None:
        return str(data["base"])
    return None


def collect_token_entries(payload: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and (item.get("address") or item.get("tokenAddress")):
                entries.append(item)
            else:
                entries.extend(collect_token_entries(item))
    elif isinstance(payload, dict):
        if payload.get("address") or payload.get("tokenAddress"):
            entries.append(payload)
        else:
            for value in payload.values():
                entries.extend(collect_token_entries(value))
    return entries


def fetch_token_metadata(session: requests.Session, raw_handle, max_retries: int, rate_limiter: RateLimiter, max_429_sleep: float) -> dict[str, dict[str, Any]]:
    print("Fetching OpenOcean tokenList for decimal validation...")
    result = request_json(session, TOKEN_LIST_URL, {}, max_retries, rate_limiter, max_429_sleep)
    write_raw(raw_handle, "token_list", {}, result)
    data = result.json_data.get("data") if isinstance(result.json_data, dict) else None
    tokens = collect_token_entries(data)
    metadata: dict[str, dict[str, Any]] = {}
    for token in tokens:
        address = str(token.get("address") or token.get("tokenAddress") or "").lower()
        if address:
            metadata[address] = token
    print(f"  tokenList entries found: {len(metadata):,}")
    return metadata


def apply_token_metadata(tokens: list[dict[str, Any]], metadata: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    for token in tokens:
        item = token.copy()
        found = metadata.get(item["token_address"].lower())
        if found and found.get("decimals") is not None:
            try:
                metadata_decimals = int(found["decimals"])
                if metadata_decimals != item["decimals"]:
                    print(f"  decimals update from tokenList: {item['token_symbol']} {item['decimals']} -> {metadata_decimals}")
                item["decimals"] = metadata_decimals
                item["decimals_source"] = "openocean_tokenList"
            except (TypeError, ValueError):
                pass
        enriched.append(item)
    return enriched


def fetch_gas_price(session: requests.Session, raw_handle, max_retries: int, rate_limiter: RateLimiter, max_429_sleep: float) -> tuple[str, str]:
    print("Fetching OpenOcean gasPrice...")
    result = request_json(session, GAS_PRICE_URL, {}, max_retries, rate_limiter, max_429_sleep)
    write_raw(raw_handle, "gas_price", {}, result)
    data = data_section(result.json_data)
    gas_price = parse_gas_price(data)
    if gas_price:
        print(f"  gasPriceDecimals: {gas_price}")
        return gas_price, "openocean_gasPrice"
    print(f"  gasPrice fetch/parse failed; using fallback {DEFAULT_GAS_PRICE_DECIMALS}")
    return DEFAULT_GAS_PRICE_DECIMALS, "fallback_default"


def quote_api(
    session: requests.Session,
    raw_handle,
    in_token: dict[str, Any],
    out_token: dict[str, Any],
    amount_decimals: str,
    gas_price_decimals: str,
    max_retries: int,
    rate_limiter: RateLimiter,
    max_429_sleep: float,
    record_type: str,
    context: dict[str, Any],
) -> ApiResult:
    params = {
        "inTokenAddress": in_token["token_address"],
        "outTokenAddress": out_token["token_address"],
        "amountDecimals": amount_decimals,
        "gasPriceDecimals": gas_price_decimals,
    }
    result = request_json(session, QUOTE_URL, params, max_retries, rate_limiter, max_429_sleep)
    write_raw(raw_handle, record_type, context, result)
    return result


def token_usd_value_fallback(token: dict[str, Any], amount: Decimal, price_cache: dict[str, tuple[Decimal, str]]) -> tuple[Decimal | None, str | None]:
    symbol = token["token_symbol"]
    if symbol in {"USDC", "USDT"}:
        return amount, f"{symbol.lower()}_stablecoin_amount_fallback"
    cached = price_cache.get(symbol)
    if cached:
        return amount * cached[0], f"cached_{symbol}_price"
    return None, None


def output_value_usd(
    data: dict[str, Any],
    out_token: dict[str, Any],
    out_amount_decimals: Decimal | None,
    price_cache: dict[str, tuple[Decimal, str]],
) -> tuple[Decimal | None, str | None]:
    direct, source = direct_usd_value(data, "out")
    if direct is not None:
        return direct, source

    if out_amount_decimals is None:
        return None, None

    out_token_price = token_price_usd_from_response(data, "out")
    if out_token_price is not None:
        return out_amount_decimals * out_token_price, "out_token_usd_price_x_out_amount"

    return token_usd_value_fallback(out_token, out_amount_decimals, price_cache)


def estimate_token_price(
    session: requests.Session,
    raw_handle,
    token: dict[str, Any],
    usdc: dict[str, Any],
    gas_price_decimals: str,
    price_cache: dict[str, tuple[Decimal, str]],
    price_errors: dict[str, str],
    max_retries: int,
    rate_limiter: RateLimiter,
    max_429_sleep: float,
) -> tuple[Decimal | None, str, str | None]:
    symbol = token["token_symbol"]
    if symbol == "USDC":
        return Decimal("1"), "usdc_anchor", None
    if symbol in price_cache:
        price, source = price_cache[symbol]
        return price, source, None
    if symbol in price_errors:
        return None, "price_unavailable_cached", price_errors[symbol]

    one_token_raw = raw_units(Decimal("1"), token["decimals"])
    print(f"  price probe: 1 {symbol} -> USDC")
    result = quote_api(
        session,
        raw_handle,
        token,
        usdc,
        one_token_raw,
        gas_price_decimals,
        max_retries,
        rate_limiter,
        max_429_sleep,
        "price_probe",
        {"token_symbol": symbol, "amount_decimals": one_token_raw},
    )

    data = data_section(result.json_data)
    out_decimals = token_decimals_from_response(data, "out") or usdc["decimals"]
    out_raw = extract_out_amount_raw(data)
    out_amount = human_amount(out_raw, out_decimals)
    out_value, value_source = output_value_usd(data, usdc, out_amount, price_cache)

    if out_value is not None and out_value > 0:
        price_cache[symbol] = (out_value, f"openocean_1_unit_quote:{value_source}")
        return out_value, f"openocean_1_unit_quote:{value_source}", None

    in_token_price = token_price_usd_from_response(data, "in")
    if in_token_price is not None and in_token_price > 0:
        price_cache[symbol] = (in_token_price, "openocean_in_token_usd")
        return in_token_price, "openocean_in_token_usd", None

    if symbol == "USDT":
        price_cache[symbol] = (Decimal("1"), "usdt_fallback_1_usd")
        return Decimal("1"), "usdt_fallback_1_usd", None

    error = result.error or extract_error_message(result.json_data) or "unable to estimate token price"
    price_errors[symbol] = error
    return None, "price_unavailable", error


def quote_success(result: ApiResult, data: dict[str, Any]) -> bool:
    if result.status_code != 200:
        return False
    if isinstance(result.json_data, dict) and result.json_data.get("code") not in (None, 200, "200"):
        return False
    return bool(data) and extract_out_amount_raw(data) is not None


def blank_record(
    snapshot_time: str,
    trade_size: Decimal,
    direction: str,
    in_token: dict[str, Any],
    out_token: dict[str, Any],
    amount_decimals: str | None,
    estimated_price: Decimal | None,
    price_source: str | None,
    gas_price_decimals: str | None,
) -> dict[str, Any]:
    return {
        "snapshot_time_utc": snapshot_time,
        "chain": CHAIN,
        "trade_size_usd": decimal_to_output(trade_size),
        "direction": direction,
        "in_token_symbol": in_token["token_symbol"],
        "in_token_address": in_token["token_address"],
        "in_token_decimals": in_token["decimals"],
        "out_token_symbol": out_token["token_symbol"],
        "out_token_address": out_token["token_address"],
        "out_token_decimals": out_token["decimals"],
        "estimated_in_token_price_usd": decimal_to_output(estimated_price),
        "price_source": price_source,
        "amount_decimals": amount_decimals,
        "input_amount_decimals": None,
        "input_amount_raw": amount_decimals,
        "quoted_out_amount_raw": None,
        "quoted_out_amount_decimals": None,
        "quoted_out_value_usd": None,
        "quoted_out_value_source": None,
        "execution_cost_usd": None,
        "execution_cost_bps": None,
        "execution_cost_pct": None,
        "openocean_price_impact": None,
        "estimated_gas": None,
        "gas_price": gas_price_decimals,
        "route_summary": None,
        "dexes_used": None,
        "quote_success": False,
        "error_message": None,
        "request_url": None,
        "response_status_code": None,
        "in_token_decimals_source": in_token.get("decimals_source"),
        "out_token_decimals_source": out_token.get("decimals_source"),
    }


def build_quote_record(
    result: ApiResult,
    trade_size: Decimal,
    direction: str,
    in_token: dict[str, Any],
    out_token: dict[str, Any],
    amount_decimals: str,
    estimated_price: Decimal | None,
    price_source: str,
    gas_price_decimals: str,
    price_cache: dict[str, tuple[Decimal, str]],
) -> dict[str, Any]:
    snapshot_time = now_utc()
    record = blank_record(
        snapshot_time,
        trade_size,
        direction,
        in_token,
        out_token,
        amount_decimals,
        estimated_price,
        price_source,
        gas_price_decimals,
    )
    record["request_url"] = result.request_url
    record["response_status_code"] = result.status_code

    data = data_section(result.json_data)
    if not quote_success(result, data):
        record["error_message"] = result.error or extract_error_message(result.json_data) or "quote failed"
        return record

    response_in_decimals = token_decimals_from_response(data, "in")
    response_out_decimals = token_decimals_from_response(data, "out")
    if response_in_decimals is not None:
        record["in_token_decimals"] = response_in_decimals
    if response_out_decimals is not None:
        record["out_token_decimals"] = response_out_decimals

    in_amount_raw = extract_in_amount_raw(data) or amount_decimals
    out_amount_raw = extract_out_amount_raw(data)
    input_amount = human_amount(in_amount_raw, int(record["in_token_decimals"]))
    out_amount = human_amount(out_amount_raw, int(record["out_token_decimals"])) if out_amount_raw is not None else None
    out_value, out_value_source = output_value_usd(data, out_token, out_amount, price_cache)
    execution_cost_usd = trade_size - out_value if out_value is not None else None
    execution_cost_pct = execution_cost_usd / trade_size if execution_cost_usd is not None and trade_size else None
    execution_cost_bps = execution_cost_pct * Decimal("10000") if execution_cost_pct is not None else None
    dexes = extract_dexes(data)

    record.update(
        {
            "input_amount_decimals": decimal_to_output(input_amount),
            "input_amount_raw": in_amount_raw,
            "quoted_out_amount_raw": out_amount_raw,
            "quoted_out_amount_decimals": decimal_to_output(out_amount),
            "quoted_out_value_usd": decimal_to_output(out_value),
            "quoted_out_value_source": out_value_source,
            "execution_cost_usd": decimal_to_output(execution_cost_usd),
            "execution_cost_bps": decimal_to_output(execution_cost_bps),
            "execution_cost_pct": decimal_to_output(execution_cost_pct),
            "openocean_price_impact": parse_price_impact(data),
            "estimated_gas": parse_estimated_gas(data),
            "route_summary": route_summary(data, dexes),
            "dexes_used": ", ".join(dexes) if dexes else None,
            "quote_success": out_value is not None,
            "error_message": None if out_value is not None else "quote succeeded but output USD value could not be calculated",
        }
    )
    return record


def build_failed_price_record(
    trade_size: Decimal,
    direction: str,
    in_token: dict[str, Any],
    out_token: dict[str, Any],
    price_source: str,
    error_message: str | None,
    gas_price_decimals: str,
) -> dict[str, Any]:
    record = blank_record(now_utc(), trade_size, direction, in_token, out_token, None, None, price_source, gas_price_decimals)
    record["error_message"] = error_message or "price estimate unavailable"
    return record


def parse_trade_path_value(value: Any) -> tuple[str, str] | None:
    text = str(value or "").strip()
    if not text or ">>>" not in text:
        return None
    parts = [part.strip() for part in text.split(">>>")]
    if len(parts) != 2 or not parts[0] or not parts[1] or parts[0] == parts[1]:
        return None
    return parts[0], parts[1]


def load_trade_paths(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Trade paths CSV not found: {path}")
    df = pd.read_csv(path)
    column = "trade_paths" if "trade_paths" in df.columns else df.columns[0]
    paths: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for value in df[column].dropna().tolist():
        parsed = parse_trade_path_value(value)
        if parsed and parsed not in seen:
            paths.append(parsed)
            seen.add(parsed)
    if not paths:
        raise ValueError(f"No valid trade paths found in {path}")
    return paths


def token_lookup(tokens: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for token in tokens:
        symbol = str(token["token_symbol"])
        lookup[symbol] = token
        lookup[symbol.lower()] = token
    return lookup


def quote_plan(
    tokens: list[dict[str, Any]],
    sizes: list[Decimal],
    mode: str,
    trade_paths_path: Path | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any], Decimal, str]]:
    usdc = next(token for token in tokens if token["token_symbol"] == "USDC")
    non_usdc = [token for token in tokens if token["token_symbol"] != "USDC"]
    plan: list[tuple[dict[str, Any], dict[str, Any], Decimal, str]] = []

    if mode == "trade-paths":
        lookup = token_lookup(tokens)
        skipped: list[str] = []
        for in_symbol, out_symbol in load_trade_paths(trade_paths_path or DEFAULT_TRADE_PATHS_PATH):
            in_token = lookup.get(in_symbol) or lookup.get(in_symbol.lower())
            out_token = lookup.get(out_symbol) or lookup.get(out_symbol.lower())
            if in_token is None or out_token is None:
                skipped.append(f"{in_symbol}->{out_symbol}")
                continue
            for size in sizes:
                plan.append((in_token, out_token, size, "actual_trade_path"))
        if skipped:
            print(f"  skipped unsupported trade paths: {', '.join(skipped[:10])}{'...' if len(skipped) > 10 else ''}")
        return plan

    if mode in {"usdc-both", "token-to-usdc"}:
        for token in non_usdc:
            for size in sizes:
                plan.append((token, usdc, size, "token_to_usdc"))

    if mode in {"usdc-both", "usdc-to-token"}:
        for token in non_usdc:
            for size in sizes:
                plan.append((usdc, token, size, "usdc_to_token"))

    if mode == "all-pairs":
        for in_token in tokens:
            for out_token in tokens:
                if in_token["token_address"].lower() == out_token["token_address"].lower():
                    continue
                for size in sizes:
                    plan.append((in_token, out_token, size, "all_pairs"))

    return plan


def parse_sizes(value: str) -> list[Decimal]:
    sizes = []
    for part in value.split(","):
        stripped = part.strip()
        if not stripped:
            continue
        size = decimal_or_none(stripped)
        if size is None or size <= 0:
            raise argparse.ArgumentTypeError(f"Invalid size: {part}")
        sizes.append(size)
    if not sizes:
        raise argparse.ArgumentTypeError("At least one positive size is required")
    return sizes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch OpenOcean Optimism quote snapshots for ether.fi Cash tokens.")
    parser.add_argument("--out-dir", default="data", help="Output directory. Defaults to data.")
    parser.add_argument("--sizes", type=parse_sizes, default=DEFAULT_SIZES, help="Comma-separated USD sizes. Default: 50000,100000,150000,200000")
    parser.add_argument(
        "--mode",
        choices=["trade-paths", "usdc-both", "token-to-usdc", "usdc-to-token", "all-pairs"],
        default="trade-paths",
        help="Quote path mode. Default: trade-paths from data/trade_paths.csv.",
    )
    parser.add_argument("--trade-paths", default=str(DEFAULT_TRADE_PATHS_PATH), help="CSV of observed trade paths. Default: data/trade_paths.csv.")
    parser.add_argument("--sleep", type=float, default=1.25, help="Minimum seconds between HTTP requests. Default: 1.25 seconds.")
    parser.add_argument("--max-retries", type=int, default=3, help="Max retries per request. Default: 3.")
    parser.add_argument("--max-requests-per-10s", type=int, default=8, help="Client-side request window cap. Default: 8 requests per 10 seconds.")
    parser.add_argument("--max-429-sleep", type=float, default=60.0, help="Max seconds to sleep after a 429 before retrying. Default: 60.")
    return parser.parse_args()


def print_validation_summary(df: pd.DataFrame, expected_attempts: int) -> None:
    success_count = int(df["quote_success"].sum()) if not df.empty else 0
    failed = df[~df["quote_success"]] if not df.empty else pd.DataFrame()
    print("\nValidation summary")
    print(f"  expected quote attempts: {expected_attempts}")
    print(f"  successful quotes:       {success_count}")
    print(f"  failed quotes:           {len(failed)}")

    if not failed.empty:
        failed_pairs = (
            failed[["in_token_symbol", "out_token_symbol", "trade_size_usd", "error_message"]]
            .drop_duplicates()
            .head(20)
        )
        print("  failed pairs (first 20):")
        for row in failed_pairs.itertuples(index=False):
            print(f"    {row.in_token_symbol}->{row.out_token_symbol} ${row.trade_size_usd}: {row.error_message}")

    success = df[df["quote_success"]].copy() if not df.empty else pd.DataFrame()
    if success.empty:
        return

    success["execution_cost_pct_num"] = pd.to_numeric(success["execution_cost_pct"], errors="coerce") * 100
    unique_pairs = success[["in_token_symbol", "out_token_symbol"]].drop_duplicates()
    print(f"  successful unique pairs: {len(unique_pairs)}")
    median = (
        success.groupby(["in_token_symbol", "out_token_symbol", "trade_size_usd"])["execution_cost_pct_num"]
        .median()
        .reset_index()
        .sort_values("execution_cost_pct_num", ascending=False)
        .head(12)
    )
    print("  highest median execution cost % by pair/size (showing 12):")
    for row in median.itertuples(index=False):
        print(f"    {row.in_token_symbol}->{row.out_token_symbol} ${row.trade_size_usd}: {row.execution_cost_pct_num:.2f}%")

    worst_idx = success["execution_cost_pct_num"].idxmax()
    worst = success.loc[worst_idx]
    print(
        "  worst quote: "
        f"{worst['in_token_symbol']}->{worst['out_token_symbol']} ${worst['trade_size_usd']} "
        f"= {float(worst['execution_cost_pct']) * 100:.2f}%"
    )


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = run_timestamp()
    latest_csv = out_dir / "openocean_optimism_quote_snapshots.csv"
    timestamped_csv = out_dir / f"openocean_optimism_quote_snapshots_{timestamp}.csv"
    raw_jsonl = out_dir / f"openocean_optimism_quote_raw_{timestamp}.jsonl"

    session = requests.Session()
    session.headers.update({"User-Agent": "etherfi-cash-quote-collector/1.0"})

    records: list[dict[str, Any]] = []
    price_cache: dict[str, tuple[Decimal, str]] = {}
    price_errors: dict[str, str] = {}
    rate_limiter = RateLimiter(min_interval_seconds=args.sleep, max_requests_per_window=args.max_requests_per_10s)

    print("OpenOcean Optimism quote collector")
    print("  quote endpoint: https://open-api.openocean.finance/v4/optimism/quote")
    print("  mode:           ", args.mode)
    print("  sizes:          ", ", ".join(decimal_to_output(size) or str(size) for size in args.sizes))
    print("  output dir:     ", out_dir)
    if args.mode == "trade-paths":
        print("  trade paths:    ", args.trade_paths)
    print("  rate limit:     ", f"{args.max_requests_per_10s} requests / 10s, min {args.sleep}s between requests")

    with raw_jsonl.open("w", encoding="utf-8") as raw_handle:
        metadata = fetch_token_metadata(session, raw_handle, args.max_retries, rate_limiter, args.max_429_sleep)
        tokens = apply_token_metadata(TOKENS, metadata)
        usdc = next(token for token in tokens if token["token_symbol"] == "USDC")
        gas_price_decimals, gas_price_source = fetch_gas_price(session, raw_handle, args.max_retries, rate_limiter, args.max_429_sleep)
        print(f"  gas price source: {gas_price_source}")

        plan = quote_plan(tokens, args.sizes, args.mode, Path(args.trade_paths))
        print(f"Quote attempts planned: {len(plan)}")

        for index, (in_token, out_token, trade_size, direction) in enumerate(plan, start=1):
            pair = f"{in_token['token_symbol']}->{out_token['token_symbol']}"
            print(f"[{index}/{len(plan)}] {pair} ${decimal_to_output(trade_size)}")

            estimated_price, price_source, price_error = estimate_token_price(
                session,
                raw_handle,
                in_token,
                usdc,
                gas_price_decimals,
                price_cache,
                price_errors,
                args.max_retries,
                rate_limiter,
                args.max_429_sleep,
            )
            if estimated_price is None or estimated_price <= 0:
                print(f"  skipped: {price_error}")
                records.append(build_failed_price_record(trade_size, direction, in_token, out_token, price_source, price_error, gas_price_decimals))
                continue

            input_amount = trade_size / estimated_price
            amount_decimals = raw_units(input_amount, in_token["decimals"])
            result = quote_api(
                session,
                raw_handle,
                in_token,
                out_token,
                amount_decimals,
                gas_price_decimals,
                args.max_retries,
                rate_limiter,
                args.max_429_sleep,
                "quote",
                {
                    "direction": direction,
                    "in_token_symbol": in_token["token_symbol"],
                    "out_token_symbol": out_token["token_symbol"],
                    "trade_size_usd": decimal_to_output(trade_size),
                    "amount_decimals": amount_decimals,
                },
            )
            record = build_quote_record(
                result,
                trade_size,
                direction,
                in_token,
                out_token,
                amount_decimals,
                estimated_price,
                price_source,
                gas_price_decimals,
                price_cache,
            )
            records.append(record)
            if record["quote_success"]:
                print(f"  success: execution cost {float(record['execution_cost_pct']) * 100:.2f}%")
            else:
                print(f"  failed: {record['error_message']}")

    df = pd.DataFrame(records)
    for column in OUTPUT_COLUMNS:
        if column not in df.columns:
            df[column] = None
    df = df[OUTPUT_COLUMNS]
    df.to_csv(latest_csv, index=False)
    df.to_csv(timestamped_csv, index=False)

    print("\nSaved outputs")
    print(f"  latest CSV:      {latest_csv}")
    print(f"  timestamped CSV: {timestamped_csv}")
    print(f"  raw JSONL:       {raw_jsonl}")
    print_validation_summary(df, expected_attempts=len(records))


if __name__ == "__main__":
    main()
