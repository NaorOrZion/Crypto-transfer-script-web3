"""
Investigate an address on Aerodrome (Base): AERO profit, commissions,
and liquidity provided per 1–7 full days, plus totals.

Uses QuickNode Base RPC (QUICKNODE_BASE_ENDPOINT) or public Base RPC.
"""

from web3 import Web3
try:
    from web3.middleware import geth_poa_middleware
except ImportError:
    geth_poa_middleware = None  # web3 v7+ removed it; Base often works without it
import os
import sys
from collections import defaultdict
from decimal import Decimal
import requests

# Load .env from project root (parent of Aerodrome/)
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(_root, ".env"))

# --- Chain & RPC (Base) ---
BASE_CHAIN_ID = 8453
# Blocks per day on Base (~2s block time)
BLOCKS_PER_DAY = 43_200

# QuickNode Base endpoint; fallback to public Base RPC
QUICKNODE_BASE = os.getenv("QUICKNODE_BASE_ENDPOINT") or os.getenv("QUICKNODE_ENDPOINT")
BASE_RPC = QUICKNODE_BASE or "https://mainnet.base.org"

# --- Aerodrome (Base) ---
AERO_TOKEN = Web3.to_checksum_address("0x940181a94A35A4569E4529A3CDfB74e38FD98631")
VOTER = Web3.to_checksum_address("0x16613524e02ad97eDfeF371bC883F2F5d6C480A5")
def _topic_hex(signature: str) -> str:
    """Event topic hash with 0x prefix (QuickNode requires 0x)."""
    return "0x" + Web3.keccak(text=signature).hex()


TOPIC_TRANSFER = _topic_hex("Transfer(address,address,uint256)")
TOPIC_DEPOSIT = _topic_hex("Deposit(address,uint256)")
TOPIC_WITHDRAW = _topic_hex("Withdraw(address,uint256)")
TOPIC_GAUGE_CREATED = _topic_hex("GaugeCreated(address,address)")


def get_web3():
    w3 = Web3(Web3.HTTPProvider(BASE_RPC))
    if geth_poa_middleware is not None:
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    return w3


def to_topic_address(addr: str) -> str:
    return "0x" + addr.lower().replace("0x", "").zfill(64)


def _block_hex(n: int) -> str:
    """Block number as hex string with 0x prefix (QuickNode requires this)."""
    return hex(n)


def block_ranges_for_days(w3, num_days: int):
    """
    Return list of (day_label, from_block, to_block).
    Each day is a full 24h window, non-overlapping: day1 = most recent 24h, day2 = previous 24h, etc.
    """
    latest = w3.eth.block_number
    ranges = []
    for d in range(1, num_days + 1):
        to_block = latest - ((d - 1) * BLOCKS_PER_DAY) if d > 1 else latest
        from_block = max(0, latest - (d * BLOCKS_PER_DAY))
        ranges.append((f"day_{d}", from_block, to_block))
    return ranges


def fetch_aero_transfers(w3, address: str, from_block: int, to_block: int):
    """ERC20 Transfer(to=address) for AERO token. Returns list of (block, amount_wei)."""
    topic_to = to_topic_address(address)
    logs = w3.eth.get_logs({
        "address": AERO_TOKEN,
        "fromBlock": _block_hex(from_block),
        "toBlock": _block_hex(to_block),
        "topics": [TOPIC_TRANSFER, None, topic_to],  # from=any, to=address
    })
    out = []
    for log in logs:
        if len(log["data"]) != 66:  # 0x + 64 hex
            continue
        amount_wei = int(log["data"].hex(), 16)
        out.append((log["blockNumber"], amount_wei))
    return out


def fetch_gauge_addresses(w3, from_block: int, to_block: int):
    """GaugeCreated(pool, gauge) from Voter. Returns set of gauge addresses."""
    logs = w3.eth.get_logs({
        "address": VOTER,
        "fromBlock": _block_hex(from_block),
        "toBlock": _block_hex(to_block),
        "topics": [TOPIC_GAUGE_CREATED],
    })
    gauges = set()
    for log in logs:
        if len(log["topics"]) >= 3:
            gauge = "0x" + log["topics"][2].hex()[-40:]
            gauges.add(Web3.to_checksum_address(gauge))
        elif len(log["data"]) >= 66:
            # gauge in data (first 32 bytes = gauge address)
            gauge = "0x" + log["data"].hex()[26:66]  # last 20 bytes of first word
            gauges.add(Web3.to_checksum_address(gauge))
    return gauges


def fetch_gauge_deposits_withdraws(w3, gauge_address: str, user_topic: str, from_block: int, to_block: int):
    """Deposit and Withdraw for user. Returns (deposit_sum_wei, withdraw_sum_wei)."""
    deposit_sum = withdraw_sum = 0
    for topic0 in (TOPIC_DEPOSIT, TOPIC_WITHDRAW):
        try:
            logs = w3.eth.get_logs({
                "address": gauge_address,
                "fromBlock": _block_hex(from_block),
                "toBlock": _block_hex(to_block),
                "topics": [topic0, user_topic],
            })
            for log in logs:
                if len(log["data"]) != 66:
                    continue
                amount = int(log["data"].hex(), 16)
                if topic0 == TOPIC_DEPOSIT:
                    deposit_sum += amount
                else:
                    withdraw_sum += amount
        except Exception:
            continue
    return deposit_sum, withdraw_sum


def _fetch_gauges_chunk(w3, from_b: int, to_b: int, gauges: set) -> None:
    """Fetch GaugeCreated for [from_b, to_b]; on 413 retry with half range."""
    try:
        gauges |= fetch_gauge_addresses(w3, from_b, to_b)
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 413:
            if to_b - from_b <= 1:
                raise
            mid = (from_b + to_b) // 2
            _fetch_gauges_chunk(w3, from_b, mid, gauges)
            _fetch_gauges_chunk(w3, mid + 1, to_b, gauges)
        else:
            raise
    except Exception as e:
        err_str = str(e)
        if "413" in err_str or "Request Entity Too Large" in err_str or "too large" in err_str.lower():
            if to_b - from_b <= 1:
                raise
            mid = (from_b + to_b) // 2
            _fetch_gauges_chunk(w3, from_b, mid, gauges)
            _fetch_gauges_chunk(w3, mid + 1, to_b, gauges)
        else:
            raise


def fetch_all_gauges_up_to(w3, to_block: int):
    """Collect all gauge addresses from Voter GaugeCreated from block 0 to to_block."""
    # Small chunks to avoid 413 Request Entity Too Large (QuickNode/size limits)
    chunk = 2_000
    gauges = set()
    from_b = 0
    while from_b <= to_block:
        to_b = min(from_b + chunk - 1, to_block)
        _fetch_gauges_chunk(w3, from_b, to_b, gauges)
        from_b = to_b + 1
    return gauges


def fetch_other_transfers_to_address(w3, address: str, from_block: int, to_block: int, exclude_token: str):
    """
    Any ERC20 Transfer(to=address). Exclude exclude_token.
    Returns list of (token_address, block, amount_wei).
    """
    topic_to = to_topic_address(address)
    # We can't filter by token in one call; we'd need to query per token or use a service.
    # Instead: get all logs with topic0=Transfer and topic2=address from a known set of reward contracts,
    # or skip and return []. For a generic script we skip broad "all tokens" (too many contracts).
    # So we only return AERO from dedicated call; "commissions" we'll try via known fee/reward contracts later.
    return []


def run_investigation(address: str, num_days: int = 7):
    address = Web3.to_checksum_address(address)
    w3 = get_web3()
    if not w3.is_connected():
        print("ERROR: Could not connect to Base RPC. Check QUICKNODE_BASE_ENDPOINT or network.")
        return

    print(f"Base RPC: {BASE_RPC[:50]}...")
    print(f"Address:  {address}")
    print(f"Windows:  day_1 = most recent 24h, day_2 = previous 24h, ... day_{num_days}, plus TOTAL")
    print()

    ranges = block_ranges_for_days(w3, num_days)
    latest = w3.eth.block_number
    total_from = max(0, latest - num_days * BLOCKS_PER_DAY)
    total_to = latest
    all_ranges = list(ranges) + [("TOTAL", total_from, total_to)]  # TOTAL = full 7-day range

    # Collect gauges once (up to latest block)
    print("Fetching gauge list from Voter (GaugeCreated)...")
    gauges = fetch_all_gauges_up_to(w3, total_to)
    print(f"Found {len(gauges)} gauge(s).")
    user_topic = to_topic_address(address)

    aero_decimals = 18
    # AERO profit per window
    aero_by_window = {}
    # Liquidity (LP units): net deposit per window
    liquidity_by_window = {}
    # Commissions: other token transfers to address (we don't have full list; use 0 or optional later)
    commissions_by_window = {}

    for label, from_block, to_block in all_ranges:
        # AERO received
        transfers = fetch_aero_transfers(w3, address, from_block, to_block)
        aero_wei = sum(amt for _, amt in transfers)
        aero_by_window[label] = aero_wei

        # Liquidity: sum (deposits - withdraws) across all gauges in this block range
        liq_dep = liq_wd = 0
        for gauge in gauges:
            d, w = fetch_gauge_deposits_withdraws(w3, gauge, user_topic, from_block, to_block)
            liq_dep += d
            liq_wd += w
        liquidity_by_window[label] = (liq_dep - liq_wd, liq_dep, liq_wd)

        # Commissions: placeholder (would need reward/fee contract list)
        commissions_by_window[label] = 0

    # Print report
    def fmt_aero(wei):
        return f"{Decimal(wei) / 10**aero_decimals:,.4f} AERO"

    print()
    print("=" * 70)
    print("AERO PROFIT (AERO received by address)")
    print("=" * 70)
    for label, from_block, to_block in all_ranges:
        v = aero_by_window[label]
        print(f"  {label:20}  {fmt_aero(v)}")
    print()

    print("=" * 70)
    print("COMMISSIONS (other token rewards to address)")
    print("  (Aerodrome fee claims go to veAERO voters; LP fees are forgone for emissions.)")
    print("  (Set to 0 here; use protocol/subgraph for voter fee breakdown.)")
    print("=" * 70)
    for label in aero_by_window:
        print(f"  {label:20}  {commissions_by_window[label]}")
    print()

    print("=" * 70)
    print("LIQUIDITY PROVIDED (net gauge Deposit − Withdraw in LP units)")
    print("=" * 70)
    for label in liquidity_by_window:
        net, dep, wd = liquidity_by_window[label]
        print(f"  {label:20}  net: {net:,}  (deposits: {dep:,}, withdrawals: {wd:,})")
    print()

    return {
        "aero_by_window": aero_by_window,
        "commissions_by_window": commissions_by_window,
        "liquidity_by_window": liquidity_by_window,
    }


if __name__ == "__main__":
    import argparse
    ADDRESS = "0xCF979E05C91450e1FB5d98139101F0EFcd934d07"
    parser = argparse.ArgumentParser(description="Aerodrome address: AERO profit, commissions, liquidity (1–7 days).")
    parser.add_argument("--days", type=int, default=7, help="Number of days (1–7, default 7)")
    args = parser.parse_args()
    run_investigation(ADDRESS, min(7, max(1, args.days)))
