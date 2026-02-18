"""
Aerodrome Slipstream LP PnL Analyzer (Base network).

Tracks for a given wallet address:
  1. Total Net Liquidity Provided (USD) — deposits minus withdrawals, valued at tx-time prices.
  2. Total Trading Fees Earned (USD) — Collect amounts in excess of principal (burned liquidity).
  3. Total AERO Rewards Claimed (USD) — from Gauge ClaimRewards events.
  4. Total Gas Fees Paid (USD) — for all txs from this address to Aerodrome contracts.

Logic outline:
  - Fetch all IncreaseLiquidity, DecreaseLiquidity, Collect from NonFungiblePositionManager,
    and ClaimRewards from each Gauge, over the block range.
  - Keep only events whose transaction is sent by ADDRESS (tx.from == ADDRESS).
  - Net liquidity: for each IncreaseLiquidity (deposit) and DecreaseLiquidity (withdrawal),
    get token0/token1 amounts and block; value at historical price; sum deposits minus withdrawals.
  - Fees: for each Collect, subtract same-tx DecreaseLiquidity amounts (principal) per tokenId;
    value the remainder (fee-only) in USD.
  - AERO: sum ClaimRewards amounts and value at historical AERO price.
  - Gas: for each distinct tx that our address sent to NFPM or Gauge, sum gas_used * effective_gas_price;
    convert ETH to USD at block price.
  - Net profit = Fees + AERO − Gas (all in USD).

Usage:
  Set RPC_URL, ADDRESS, NFPM_ADDRESS, GAUGE_ADDRESSES, and ABIs below (or load from env/files).
  Implement get_token_price_usd, get_eth_price_usd, get_aero_price_usd for real USD values.
  Run: python trace_address_aero_profit.py

Output: Summary table with the 4 metrics and Net Profit in USD.
"""

import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from typing import Any, List, Optional

import requests
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

# -----------------------------------------------------------------------------
# Configuration — set these or load from env/JSON
# -----------------------------------------------------------------------------
RPC_URL = os.getenv("QUICKNODE_BASE_ENDPOINT") or "https://mainnet.base.org"
# When primary RPC returns 413, we retry with this (e.g. public Base). Set to None to disable.
FALLBACK_RPC_URL = os.getenv("FALLBACK_RPC_URL") or "https://mainnet.base.org"
ADDRESS = "0xCF979E05C91450e1FB5d98139101F0EFcd934d07"  # LP wallet to analyze

# Aerodrome Slipstream on Base (replace if using different deployment)
NFPM_ADDRESS = "0xc9a6168af88b35a9313183cd5bd4f362c34a6c71"  # NonFungiblePositionManager
GAUGE_ADDRESSES: list[str] = ["0xF33a96b5932D9E9B9A0eDA447AbD8C9d48d2e0c8"]  # Add gauge address(es), e.g. ["0x..."] — one per pool

# ABIs — you can replace these with full ABIs from your source
# Minimal ABIs for events and the positions() view
NFPM_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "tokenId", "type": "uint256"},
            {"indexed": False, "name": "liquidity", "type": "uint128"},
            {"indexed": False, "name": "amount0", "type": "uint256"},
            {"indexed": False, "name": "amount1", "type": "uint256"},
        ],
        "name": "IncreaseLiquidity",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "tokenId", "type": "uint256"},
            {"indexed": False, "name": "liquidity", "type": "uint128"},
            {"indexed": False, "name": "amount0", "type": "uint256"},
            {"indexed": False, "name": "amount1", "type": "uint256"},
        ],
        "name": "DecreaseLiquidity",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "tokenId", "type": "uint256"},
            {"indexed": False, "name": "recipient", "type": "address"},
            {"indexed": False, "name": "amount0", "type": "uint256"},
            {"indexed": False, "name": "amount1", "type": "uint256"},
        ],
        "name": "Collect",
        "type": "event",
    },
    {
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "name": "positions",
        "outputs": [
            {"name": "nonce", "type": "uint96"},
            {"name": "operator", "type": "address"},
            {"name": "token0", "type": "address"},
            {"name": "token1", "type": "address"},
            {"name": "fee", "type": "uint24"},
            {"name": "tickLower", "type": "int24"},
            {"name": "tickUpper", "type": "int24"},
            {"name": "liquidity", "type": "uint128"},
            {"name": "feeGrowthInside0LastX128", "type": "uint256"},
            {"name": "feeGrowthInside1LastX128", "type": "uint256"},
            {"name": "tokensOwed0", "type": "uint128"},
            {"name": "tokensOwed1", "type": "uint128"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

# Gauge: ClaimRewards event (signature may vary; common: ClaimRewards(address indexed user, uint256 amount))
GAUGE_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "user", "type": "address"},
            {"indexed": False, "name": "amount", "type": "uint256"},
        ],
        "name": "ClaimRewards",
        "type": "event",
    },
]

# Optional: AERO token address on Base for decimals
AERO_TOKEN_ADDRESS = "0x940181a94A35A4569E4529A3CDfB74e38FD98631"

# Block range to scan (set to None to use from genesis / earliest or a fixed start)
FROM_BLOCK: Optional[int] = 42269956  # e.g. 5_000_000
TO_BLOCK: Optional[int] = None    # None = latest

# Chunk size for get_logs to avoid RPC limits (413). QuickNode often needs small chunks.
LOG_CHUNK_SIZE = 100


def _topic_hex(signature: str) -> str:
    return "0x" + Web3.keccak(text=signature).hex()


def _block_hex(n: int) -> str:
    return hex(n)


def _address_to_topic(addr: str) -> str:
    return "0x" + addr.lower().replace("0x", "").zfill(64)


# -----------------------------------------------------------------------------
# Price fetcher (historical USD at block/time)
# Replace with your own implementation (e.g. DeFiLlama, CoinGecko, subgraph).
# -----------------------------------------------------------------------------
def get_token_price_usd(
    w3: Web3,
    token_address: str,
    block_identifier: int,
    amount_wei: int,
    decimals: int = 18,
) -> Decimal:
    """
    Return USD value of `amount_wei` of token at `block_identifier`.
    Default implementation returns 0; you should plug in real price source.
    """
    # Placeholder: implement with your API or on-chain oracle.
    # Example: use DefiLlama API by block timestamp, or a price contract.
    _ = w3, token_address, block_identifier, amount_wei, decimals
    return Decimal("0")


def get_eth_price_usd(w3: Web3, block_identifier: int) -> Decimal:
    """ETH price in USD at block. Placeholder; replace with your source."""
    _ = w3, block_identifier
    return Decimal("0")


def get_aero_price_usd(w3: Web3, block_identifier: int) -> Decimal:
    """AERO price in USD at block. Placeholder; replace with your source."""
    _ = w3, block_identifier
    return Decimal("0")


# -----------------------------------------------------------------------------
# Event topic hashes (Uniswap V3 / Slipstream compatible)
# -----------------------------------------------------------------------------
INCREASE_LIQUIDITY_TOPIC = _topic_hex(
    "IncreaseLiquidity(uint256,uint128,uint256,uint256)"
)
DECREASE_LIQUIDITY_TOPIC = _topic_hex(
    "DecreaseLiquidity(uint256,uint128,uint256,uint256)"
)
COLLECT_TOPIC = _topic_hex("Collect(uint256,address,uint256,uint256)")
# Gauge ClaimRewards — adjust signature if your gauge uses different params
CLAIM_REWARDS_TOPIC = _topic_hex("ClaimRewards(address,uint256)")


def _is_413(e: Exception) -> bool:
    """True if exception is HTTP 413 (request/response too large)."""
    if e is None:
        return False
    if hasattr(e, "response") and e.response is not None and getattr(e.response, "status_code", None) == 413:
        return True
    err = (str(e) + str(getattr(e, "__cause__", ""))).lower()
    return "413" in err or "request entity too large" in err or "entity too large" in err


def _get_logs_one_request(w3: Web3, params: dict, fallback_w3: Optional[Web3] = None) -> list:
    """Call eth_getLogs; on 413 try fallback_w3 if provided."""
    try:
        return w3.eth.get_logs(params)
    except Exception as e:
        if not _is_413(e) or fallback_w3 is None:
            raise
        return fallback_w3.eth.get_logs(params)


def _fetch_logs_chunk(
    w3: Web3,
    address: str,
    topics: list,
    from_b: int,
    to_b: int,
    fallback_w3: Optional[Web3] = None,
) -> list:
    """Single eth_getLogs request for one chunk; on 413 tries fallback."""
    params = {
        "fromBlock": _block_hex(from_b),
        "toBlock": _block_hex(to_b),
        "address": Web3.to_checksum_address(address),
        "topics": topics,
    }
    return _get_logs_one_request(w3, params, fallback_w3)


def get_logs_chunked(
    w3: Web3,
    address: str,
    topics: list,
    from_block: int,
    to_block: int,
    fallback_w3: Optional[Web3] = None,
    retry_single_topics: Optional[List[list]] = None,
):
    """Yield logs in chunks. On 413: try fallback RPC; if retry_single_topics set, retry chunk with one request per topic (parallel)."""
    current = from_block
    chunk = LOG_CHUNK_SIZE
    addr = Web3.to_checksum_address(address)
    while current <= to_block:
        end = min(current + chunk - 1, to_block)
        try:
            logs = _fetch_logs_chunk(w3, addr, topics, current, end, fallback_w3)
        except Exception as e:
            if not _is_413(e):
                raise
            if retry_single_topics and len(retry_single_topics) > 1:
                chunks_logs: List[list] = []
                with ThreadPoolExecutor(max_workers=len(retry_single_topics)) as ex:
                    futures = {
                        ex.submit(
                            _fetch_logs_chunk, w3, addr, st, current, end, fallback_w3
                        ): st
                        for st in retry_single_topics
                    }
                    for fut in as_completed(futures):
                        chunks_logs.append(fut.result())
                logs = []
                for L in chunks_logs:
                    logs.extend(L)
                logs.sort(key=lambda log: (log.get("blockNumber", 0), log.get("logIndex", 0)))
            elif chunk > 50:
                chunk = max(50, chunk // 2)
                continue
            else:
                raise
        yield from logs
        chunk = LOG_CHUNK_SIZE
        current = end + 1


def get_all_nfpm_logs(
    w3: Web3, from_block: int, to_block: int, fallback_w3: Optional[Web3] = None
):
    """Fetch NFPM events. One request per chunk (all 3 topics); on 413 retries that chunk with 3 parallel single-topic requests."""
    nfpm = Web3.to_checksum_address(NFPM_ADDRESS)
    all_three = [
        [INCREASE_LIQUIDITY_TOPIC],
        [DECREASE_LIQUIDITY_TOPIC],
        [COLLECT_TOPIC],
    ]
    retry_single = all_three
    topics_combined = [
        [INCREASE_LIQUIDITY_TOPIC, DECREASE_LIQUIDITY_TOPIC, COLLECT_TOPIC]
    ]
    all_logs = list(
        get_logs_chunked(
            w3,
            nfpm,
            topics_combined,
            from_block,
            to_block,
            fallback_w3,
            retry_single_topics=retry_single,
        )
    )
    all_logs.sort(key=lambda log: (log.get("blockNumber", 0), log.get("logIndex", 0)))
    return all_logs


def get_all_gauge_logs(
    w3: Web3, from_block: int, to_block: int, fallback_w3: Optional[Web3] = None
):
    """Fetch ClaimRewards from all configured gauges."""
    out = []
    for addr in GAUGE_ADDRESSES:
        topics = [[CLAIM_REWARDS_TOPIC]]
        out.extend(
            get_logs_chunked(
                w3, Web3.to_checksum_address(addr), topics, from_block, to_block, fallback_w3
            )
        )
    return out


def decode_nfpm_log(w3: Web3, log: dict, nfpm_contract: Any):
    """Decode NFPM log to event name and args (tokenId, amount0, amount1, ...)."""
    topic = log["topics"][0].hex() if log.get("topics") else ""
    data = log.get("data") or b""
    if isinstance(data, str) and data.startswith("0x"):
        data = bytes.fromhex(data[2:])
    if topic == INCREASE_LIQUIDITY_TOPIC:
        token_id = int(log["topics"][1].hex(), 16)
        # data: liquidity (uint128), amount0 (uint256), amount1 (uint256)
        liquidity = int.from_bytes(data[:16], "big")
        amount0 = int.from_bytes(data[16:48], "big")
        amount1 = int.from_bytes(data[48:80], "big")
        return ("IncreaseLiquidity", token_id, amount0, amount1, log["blockNumber"])
    if topic == DECREASE_LIQUIDITY_TOPIC:
        token_id = int(log["topics"][1].hex(), 16)
        liquidity = int.from_bytes(data[:16], "big")
        amount0 = int.from_bytes(data[16:48], "big")
        amount1 = int.from_bytes(data[48:80], "big")
        return ("DecreaseLiquidity", token_id, amount0, amount1, log["blockNumber"])
    if topic == COLLECT_TOPIC:
        token_id = int(log["topics"][1].hex(), 16)
        recipient = "0x" + data[12:32].hex()[-40:]
        amount0 = int.from_bytes(data[32:64], "big")
        amount1 = int.from_bytes(data[64:96], "big")
        return ("Collect", token_id, amount0, amount1, log["blockNumber"], recipient)
    return None


def decode_gauge_log(log: dict):
    """Decode Gauge ClaimRewards: user (indexed), amount. Returns (user, amount, block_number, tx_hash)."""
    if not log.get("topics") or len(log["topics"]) < 2:
        return None
    user = "0x" + log["topics"][1].hex()[-40:]
    data = log.get("data") or b""
    if isinstance(data, str) and data.startswith("0x"):
        data = bytes.fromhex(data[2:])
    amount = int.from_bytes(data[:32], "big") if len(data) >= 32 else 0
    tx_hash = log.get("transactionHash")
    if isinstance(tx_hash, bytes):
        tx_hash = tx_hash.hex()
    return (user, amount, log["blockNumber"], tx_hash)


def get_tx_sender(w3: Web3, tx_hash: bytes) -> Optional[str]:
    """Return tx.from for the given hash."""
    try:
        tx = w3.eth.get_transaction(tx_hash)
        return tx.get("from") and Web3.to_checksum_address(tx["from"])
    except Exception:
        return None


def get_tx_receipt(w3: Web3, tx_hash: bytes) -> Optional[dict]:
    try:
        return w3.eth.get_transaction_receipt(tx_hash)
    except Exception:
        return None


def run_analysis() -> None:
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        raise RuntimeError("Failed to connect to RPC")

    fallback_w3: Optional[Web3] = None
    if FALLBACK_RPC_URL and FALLBACK_RPC_URL.strip() and FALLBACK_RPC_URL != RPC_URL:
        fallback_w3 = Web3(Web3.HTTPProvider(FALLBACK_RPC_URL))
        if not fallback_w3.is_connected():
            fallback_w3 = None

    address = Web3.to_checksum_address(ADDRESS)
    from_block = FROM_BLOCK or 0
    to_block = TO_BLOCK or w3.eth.block_number

    nfpm = w3.eth.contract(
        address=Web3.to_checksum_address(NFPM_ADDRESS),
        abi=NFPM_ABI,
    )

    # ---------- 1) Fetch and filter NFPM logs (only txs from our address) ----------
    nfpm_logs = get_all_nfpm_logs(w3, from_block, to_block, fallback_w3)
    tx_to_sender: dict = {}
    our_nfpm_logs = []
    for log in nfpm_logs:
        tx_hash = log["transactionHash"]
        if isinstance(tx_hash, bytes):
            tx_hash = tx_hash.hex()
        if tx_hash not in tx_to_sender:
            tx_to_sender[tx_hash] = get_tx_sender(w3, tx_hash)
        if tx_to_sender[tx_hash] == address:
            our_nfpm_logs.append(log)

    # ---------- 2) Fetch and filter Gauge logs ----------
    gauge_logs = get_all_gauge_logs(w3, from_block, to_block, fallback_w3)
    our_claim_events = []  # (user, amount, block, tx_hash)
    for log in gauge_logs:
        decoded = decode_gauge_log(log)
        if decoded and decoded[0] and decoded[0].lower() == address.lower():
            our_claim_events.append(decoded)

    # ---------- 3) Decode NFPM events and group by tx for Collect fee logic ----------
    # Liquidity: sum (Increase - Decrease) per token in raw amounts; we'll value in USD later.
    deposit_amount0: dict[int, list[tuple[int, int, int]]] = defaultdict(list)  # tokenId -> [(amount0, amount1, block)]
    withdraw_amount0: dict[int, list[tuple[int, int, int]]] = defaultdict(list)

    # Per-tx: tokenId -> (amount0, amount1) from DecreaseLiquidity (principal to subtract from Collect)
    tx_decrease: dict[str, dict[int, tuple[int, int]]] = defaultdict(dict)
    # Per-tx: list of (tokenId, amount0, amount1, block) for Collect
    tx_collects: dict[str, list[tuple[int, int, int, int]]] = defaultdict(list)

    for log in our_nfpm_logs:
        decoded = decode_nfpm_log(w3, log, nfpm)
        if not decoded:
            continue
        tx_hash = log["transactionHash"].hex() if isinstance(log["transactionHash"], bytes) else log["transactionHash"]

        if decoded[0] == "IncreaseLiquidity":
            _, token_id, amt0, amt1, block = decoded
            deposit_amount0[token_id].append((amt0, amt1, block))
        elif decoded[0] == "DecreaseLiquidity":
            _, token_id, amt0, amt1, block = decoded
            withdraw_amount0[token_id].append((amt0, amt1, block))
            tx_decrease[tx_hash][token_id] = (amt0, amt1)
        elif decoded[0] == "Collect":
            _, token_id, amt0, amt1, block, _ = decoded
            tx_collects[tx_hash].append((token_id, amt0, amt1, block))

    # ---------- 4) Build deposit/withdraw event lists for USD valuation ----------
    deposit_events: list[tuple[int, int, int, int]] = []  # tokenId, amount0, amount1, block
    withdraw_events: list[tuple[int, int, int, int]] = []
    for token_id, events in deposit_amount0.items():
        for amt0, amt1, block in events:
            deposit_events.append((token_id, amt0, amt1, block))
    for token_id, events in withdraw_amount0.items():
        for amt0, amt1, block in events:
            withdraw_events.append((token_id, amt0, amt1, block))

    # ---------- 5) Fee-only Collect amounts (Collect - same-tx Decrease per tokenId) ----------
    fee_collect_events: list[tuple[int, int, int, int]] = []  # tokenId, fee0, fee1, block
    for tx_hash, collects in tx_collects.items():
        decrease_for_tx = tx_decrease.get(tx_hash, {})
        for token_id, amt0, amt1, block in collects:
            principal0, principal1 = decrease_for_tx.get(token_id, (0, 0))
            fee0 = amt0 - principal0 if amt0 >= principal0 else 0
            fee1 = amt1 - principal1 if amt1 >= principal1 else 0
            fee_collect_events.append((token_id, fee0, fee1, block))

    # ---------- 6) Resolve token0/token1 for each tokenId (cache) ----------
    token_id_to_tokens: dict[int, tuple[str, str]] = {}
    all_token_ids = set(e[0] for e in deposit_events + withdraw_events + fee_collect_events)
    for token_id in all_token_ids:
        try:
            pos = nfpm.functions.positions(token_id).call(block_identifier=to_block)
            token_id_to_tokens[token_id] = (pos[2], pos[3])  # token0, token1
        except Exception:
            token_id_to_tokens[token_id] = ("", "")

    def token_decimals(addr: str) -> int:
        if not addr:
            return 18
        try:
            c = w3.eth.contract(
                address=Web3.to_checksum_address(addr),
                abi=[{"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "type": "function"}],
            )
            return c.functions.decimals().call(block_identifier=to_block)
        except Exception:
            return 18

    # ---------- 7) USD values (using placeholder price fetcher; replace with real source) ----------
    def usd_deposit_withdraw(events: list[tuple[int, int, int, int]], sign: int) -> Decimal:
        total = Decimal("0")
        for token_id, amt0, amt1, block in events:
            t0, t1 = token_id_to_tokens.get(token_id, ("", ""))
            if t0:
                total += sign * get_token_price_usd(w3, t0, block, amt0, token_decimals(t0))
            if t1:
                total += sign * get_token_price_usd(w3, t1, block, amt1, token_decimals(t1))
        return total

    net_liquidity_usd = usd_deposit_withdraw(deposit_events, 1) + usd_deposit_withdraw(withdraw_events, -1)

    fee_earned_usd = Decimal("0")
    for token_id, fee0, fee1, block in fee_collect_events:
        t0, t1 = token_id_to_tokens.get(token_id, ("", ""))
        if t0:
            fee_earned_usd += get_token_price_usd(w3, t0, block, fee0, token_decimals(t0))
        if t1:
            fee_earned_usd += get_token_price_usd(w3, t1, block, fee1, token_decimals(t1))

    aero_claimed_wei = sum(e[1] for e in our_claim_events)
    aero_claimed_usd = Decimal("0")
    for _, amount, block, _ in our_claim_events:
        aero_claimed_usd += get_aero_price_usd(w3, block) * (Decimal(amount) / Decimal(10**18))

    # ---------- 8) Gas costs for our txs that hit NFPM or Gauge ----------
    gas_cost_wei = 0
    seen_tx = set()
    tx_hashes_to_check = set()
    for log in our_nfpm_logs:
        h = log["transactionHash"]
        tx_hashes_to_check.add(h.hex() if isinstance(h, bytes) else h)
    for _, _amt, _block, tx_hash in our_claim_events:
        tx_hashes_to_check.add(tx_hash)
    for tx_hash in tx_hashes_to_check:
        if tx_hash in seen_tx:
            continue
        seen_tx.add(tx_hash)
        receipt = get_tx_receipt(w3, tx_hash)
        if not receipt:
            continue
        tx = w3.eth.get_transaction(tx_hash)
        if tx.get("from", "").lower() != address.lower():
            continue
        gas_used = receipt.get("gasUsed") or 0
        if hasattr(gas_used, "to_wei"):
            gas_used = gas_used.to_wei()
        eff = receipt.get("effectiveGasPrice") or tx.get("gasPrice") or 0
        if hasattr(eff, "to_wei"):
            eff = eff.to_wei()
        gas_cost_wei += gas_used * eff

    eth_price = get_eth_price_usd(w3, to_block)
    gas_cost_usd = (Decimal(gas_cost_wei) / Decimal(10**18)) * eth_price

    # ---------- 9) Net profit and summary ----------
    net_profit_usd = fee_earned_usd + aero_claimed_usd - gas_cost_usd
    # Net liquidity is capital in/out; profit from fees + rewards - gas. Optionally: net_profit_usd -= net_liquidity_usd only if you treat liquidity as “cost” (usually you don’t for PnL).

    print("\n" + "=" * 60)
    print("Aerodrome Slipstream LP PnL Summary")
    print("=" * 60)
    print(f"Address:     {ADDRESS}")
    print(f"Block range: {from_block} -> {to_block}")
    print("-" * 60)
    print(f"1. Total Net Liquidity Provided (USD): {net_liquidity_usd:,.2f}")
    print(f"2. Total Trading Fees Earned (USD):    {fee_earned_usd:,.2f}")
    print(f"3. Total AERO Rewards Claimed:        {aero_claimed_wei} wei | USD: {aero_claimed_usd:,.2f}")
    print(f"4. Total Gas Fees Paid (USD):         {gas_cost_usd:,.2f}")
    print("-" * 60)
    print(f"   Net Profit (Fees + AERO - Gas) USD: {net_profit_usd:,.2f}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    run_analysis()
