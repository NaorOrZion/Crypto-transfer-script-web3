"""
Track one address (LP) on a single Aerodrome pool: AERO profit, commissions,
and liquidity provided per 1–7 full days + TOTAL.

Consts (as you provided):
  POOL_GAUGE_USDC_WETH  = USDC/WETH pool gauge
  CONTRACT_ADDRESS      = contract providing liquidity to that pool

Uses QuickNode Base RPC (QUICKNODE_BASE_ENDPOINT) or public Base RPC.
"""

from web3 import Web3
try:
    from web3.middleware import geth_poa_middleware
except ImportError:
    geth_poa_middleware = None
import os
import sys
from decimal import Decimal

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(_root, ".env"))

# --- Chain & RPC (Base) ---
BLOCKS_PER_DAY = 43_200
# Max blocks per get_logs request (avoid QuickNode 413)
LOG_CHUNK_BLOCKS = 8_000
QUICKNODE_BASE = os.getenv("QUICKNODE_BASE_ENDPOINT") or os.getenv("QUICKNODE_ENDPOINT")
BASE_RPC = QUICKNODE_BASE or "https://mainnet.base.org"

# --- Aerodrome: AERO token, pool gauge, and LP contract (as you provided) ---
AERO_TOKEN = Web3.to_checksum_address("0x940181a94A35A4569E4529A3CDfB74e38FD98631")
# USDC/WETH pool gauge (LP deposits/withdraws and rewards here)
POOL_GAUGE_USDC_WETH = Web3.to_checksum_address("0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59")
# Contract address providing liquidity to the pool above
CONTRACT_ADDRESS = Web3.to_checksum_address("0xCF979E05C91450e1FB5d98139101F0EFcd934d07")


def _topic_hex(signature: str) -> str:
    return "0x" + Web3.keccak(text=signature).hex()


TOPIC_TRANSFER = _topic_hex("Transfer(address,address,uint256)")
TOPIC_DEPOSIT = _topic_hex("Deposit(address,uint256)")
TOPIC_WITHDRAW = _topic_hex("Withdraw(address,uint256)")


def get_web3():
    w3 = Web3(Web3.HTTPProvider(BASE_RPC))
    if geth_poa_middleware is not None:
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    return w3


def to_topic_address(addr: str) -> str:
    return "0x" + addr.lower().replace("0x", "").zfill(64)


def _block_hex(n: int) -> str:
    return hex(n)


def block_ranges_for_days(w3, num_days: int):
    latest = w3.eth.block_number
    ranges = []
    for d in range(1, num_days + 1):
        to_block = latest if d == 1 else latest - ((d - 1) * BLOCKS_PER_DAY)
        from_block = max(0, latest - (d * BLOCKS_PER_DAY))
        ranges.append((f"day_{d}", from_block, to_block))
    return ranges


def fetch_aero_transfers(w3, address: str, from_block: int, to_block: int):
    topic_to = to_topic_address(address)
    total = 0
    b = from_block
    while b <= to_block:
        to_b = min(b + LOG_CHUNK_BLOCKS - 1, to_block)
        try:
            logs = w3.eth.get_logs({
                "address": AERO_TOKEN,
                "fromBlock": _block_hex(b),
                "toBlock": _block_hex(to_b),
                "topics": [TOPIC_TRANSFER, None, topic_to],
            })
            for log in logs:
                if len(log["data"]) == 66:
                    total += int(log["data"].hex(), 16)
        except Exception:
            pass
        b = to_b + 1
    return total


def fetch_gauge_deposits_withdraws(w3, gauge_address: str, user_topic: str, from_block: int, to_block: int):
    deposit_sum = withdraw_sum = 0
    b = from_block
    while b <= to_block:
        to_b = min(b + LOG_CHUNK_BLOCKS - 1, to_block)
        for topic0 in (TOPIC_DEPOSIT, TOPIC_WITHDRAW):
            try:
                logs = w3.eth.get_logs({
                    "address": gauge_address,
                    "fromBlock": _block_hex(b),
                    "toBlock": _block_hex(to_b),
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
        b = to_b + 1
    return deposit_sum, withdraw_sum


def run_investigation(lp_address: str = None, num_days: int = 7, gauge_address: str = None):
    """
    lp_address: the address providing liquidity (default CONTRACT_ADDRESS).
    gauge_address: pool gauge to look at (default POOL_GAUGE_USDC_WETH).
    """
    lp_address = Web3.to_checksum_address(lp_address or CONTRACT_ADDRESS)
    gauge = Web3.to_checksum_address(gauge_address or POOL_GAUGE_USDC_WETH)

    w3 = get_web3()
    if not w3.is_connected():
        print("ERROR: Could not connect to Base RPC.")
        return

    print(f"Base RPC:    {BASE_RPC[:50]}...")
    print(f"LP address:  {lp_address}")
    print(f"Pool gauge:  {gauge}  (USDC/WETH)")
    print(f"Windows:     day_1..day_{num_days} + TOTAL")
    print()

    ranges = block_ranges_for_days(w3, num_days)
    latest = w3.eth.block_number
    total_from = max(0, latest - num_days * BLOCKS_PER_DAY)
    total_to = latest
    all_ranges = list(ranges) + [("TOTAL", total_from, total_to)]

    user_topic = to_topic_address(lp_address)
    aero_decimals = 18

    aero_by_window = {}
    liquidity_by_window = {}
    commissions_by_window = {}

    for label, from_block, to_block in all_ranges:
        aero_by_window[label] = fetch_aero_transfers(w3, lp_address, from_block, to_block)
        dep, wd = fetch_gauge_deposits_withdraws(w3, gauge, user_topic, from_block, to_block)
        liquidity_by_window[label] = (dep - wd, dep, wd)
        commissions_by_window[label] = 0  # placeholder; use subgraph for voter fees

    def fmt_aero(wei):
        return f"{Decimal(wei) / 10**aero_decimals:,.4f} AERO"

    print("=" * 70)
    print("AERO PROFIT (AERO received by LP address)")
    print("=" * 70)
    for label, _, _ in all_ranges:
        print(f"  {label:20}  {fmt_aero(aero_by_window[label])}")
    print()

    print("=" * 70)
    print("COMMISSIONS (other token rewards; 0 = use subgraph for voter fees)")
    print("=" * 70)
    for label in aero_by_window:
        print(f"  {label:20}  {commissions_by_window[label]}")
    print()

    print("=" * 70)
    print("LIQUIDITY PROVIDED (net Deposit − Withdraw, LP units, at this pool)")
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
    parser = argparse.ArgumentParser(
        description="Track LP (CONTRACT_ADDRESS) on Aerodrome USDC/WETH pool (POOL_GAUGE_USDC_WETH): AERO profit, commissions, liquidity."
    )
    parser.add_argument("address", nargs="?", default=None, help=f"LP address to track (default: {CONTRACT_ADDRESS})")
    parser.add_argument("--days", type=int, default=7, help="Number of days (1–7)")
    parser.add_argument("--gauge", default=None, help=f"Gauge address (default: {POOL_GAUGE_USDC_WETH})")
    args = parser.parse_args()
    run_investigation(args.address, min(7, max(1, args.days)), args.gauge)
