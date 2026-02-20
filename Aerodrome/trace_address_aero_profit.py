"""
Aerodrome Slipstream LP PnL Analyzer (Base network).

Tracks for a given wallet address:
  1. Total Net Liquidity Provided (USD) — deposits minus withdrawals, valued at tx-time prices.
  2. Total Trading Fees Earned (USD) — Collect amounts in excess of principal (burned liquidity).
  3. Total AERO Rewards Claimed (USD) — from Gauge ClaimRewards events.
  4. Total Gas Fees Paid (USD) — for all txs from this address to Aerodrome contracts.

Logic outline:
  - Fetch all IncreaseLiquidity, DecreaseLiquidity, and Collect from NonFungiblePositionManager,
    and ClaimRewards from each Gauge, over the block range.
  - NFPM events are attributed to ADDRESS via: (1) ownerOf(tokenId) or positions(tokenId).operator == ADDRESS
    at to_block, or (2) if the NFT is staked (ownerOf is a Gauge), then if the tx involves ADDRESS
    (tx.from, Collect recipient, or ERC20 transfer to/from ADDRESS in that tx). No historical Transfer scan.
  - Gas is attributed to ADDRESS for every tx that contains any of our NFPM or Gauge events (bot may be tx.from).
  - Net liquidity: for each IncreaseLiquidity (deposit) and DecreaseLiquidity (withdrawal),
    get token0/token1 amounts and block; value at historical price; sum deposits minus withdrawals.
  - Fees: for each Collect, subtract same-tx DecreaseLiquidity amounts (principal) per tokenId;
    value the remainder (fee-only) in USD.
  - AERO: sum ClaimRewards amounts and value at historical AERO price.
  - Gas: for each distinct tx that contains our NFPM or Gauge events, sum gas_used * effective_gas_price;
    convert ETH to USD at block price.
  - Net profit = Fees + AERO − Gas (all in USD).

Usage:
  Set RPC_URL, ADDRESS, NFPM_ADDRESS, GAUGE_ADDRESSES, and ABIs below (or load from env/files).
  Price data is fetched from the DeFiLlama API (historical prices at block timestamp).
  Optional: set DEFILLAMA_API_KEY and/or DEFILLAMA_BASE_URL in env.
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
        "name": "ownerOf",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
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
FROM_BLOCK: Optional[int] = 42401684  # e.g. 5_000_000
TO_BLOCK: Optional[int] = 42401685    # None = latest

# Chunk size for get_logs to avoid RPC limits (413). QuickNode often needs small chunks.
LOG_CHUNK_SIZE = 100

# DeFiLlama API (optional API key for higher rate limits)
DEFILLAMA_BASE_URL = os.getenv("DEFILLAMA_BASE_URL", "https://pro-api.llama.fi").rstrip("/")
DEFILLAMA_API_KEY = os.getenv("DEFILLAMA_API_KEY")  # optional; if set, inserted in path
DEFILLAMA_CHAIN = "base"  # chain name for DefiLlama (this script targets Base)
# Base WETH (wrapped native) for ETH price
WETH_BASE_ADDRESS = "0x4200000000000000000000000000000000000006"

# In-memory caches to avoid repeated API/RPC calls
_block_timestamp_cache: dict[int, int] = {}
_defillama_price_cache: dict[tuple[str, int], Optional[tuple[Decimal, int]]] = {}  # (coin_key, timestamp) -> (price, decimals)


def _topic_hex(signature: str) -> str:
    return "0x" + Web3.keccak(text=signature).hex()


def _block_hex(n: int) -> str:
    return hex(n)


def _address_to_topic(addr: str) -> str:
    return "0x" + addr.lower().replace("0x", "").zfill(64)


# -----------------------------------------------------------------------------
# Price fetcher via DeFiLlama API (historical USD at block/time)
# -----------------------------------------------------------------------------
def _block_timestamp(w3: Web3, block_identifier: int) -> int:
    """Return Unix timestamp for block; uses cache."""
    if block_identifier not in _block_timestamp_cache:
        block = w3.eth.get_block(block_identifier)
        _block_timestamp_cache[block_identifier] = block["timestamp"]
    return _block_timestamp_cache[block_identifier]


def _defillama_price_at_block(
    w3: Web3,
    chain: str,
    token_address: str,
    block_identifier: int,
) -> Optional[tuple[Decimal, int]]:
    """Get (price_usd, decimals) from DeFiLlama for token at block; uses cache."""
    ts = _block_timestamp(w3, block_identifier)
    addr = token_address.strip().lower()
    if not addr.startswith("0x"):
        addr = "0x" + addr
    coin_key = f"{chain}:{addr}"
    cache_key = (coin_key, ts)
    if cache_key in _defillama_price_cache:
        return _defillama_price_cache[cache_key]
    base = DEFILLAMA_BASE_URL
    if DEFILLAMA_API_KEY:
        base = f"{base}/{DEFILLAMA_API_KEY}"
    url = f"{base}/coins/prices/historical/{ts}/{coin_key}"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        coins = data.get("coins") or {}
        if not coins:
            _defillama_price_cache[cache_key] = None
            return None
        entry = coins.get(coin_key)
        if not entry and isinstance(coins, dict):
            for k, v in coins.items():
                if isinstance(v, dict) and "price" in v:
                    entry = v
                    break
        if not entry or not isinstance(entry, dict) or "price" not in entry:
            _defillama_price_cache[cache_key] = None
            return None
        price_decimal = Decimal(str(entry["price"]))
        decimals = int(entry.get("decimals", 18))
        _defillama_price_cache[cache_key] = (price_decimal, decimals)
        return (price_decimal, decimals)
    except Exception:
        _defillama_price_cache[cache_key] = None
        return None


def get_token_price_usd(
    w3: Web3,
    token_address: str,
    block_identifier: int,
    amount_wei: int,
    decimals: int = 18,
) -> Decimal:
    """
    Return USD value of `amount_wei` of token at `block_identifier` using DeFiLlama API.
    """
    result = _defillama_price_at_block(w3, DEFILLAMA_CHAIN, token_address, block_identifier)
    if result is None:
        return Decimal("0")
    price, _ = result
    amount_human = Decimal(amount_wei) / Decimal(10**decimals)
    return amount_human * price


def get_eth_price_usd(w3: Web3, block_identifier: int) -> Decimal:
    """ETH price in USD at block via DeFiLlama (WETH on Base)."""
    result = _defillama_price_at_block(w3, DEFILLAMA_CHAIN, WETH_BASE_ADDRESS, block_identifier)
    if result is None:
        return Decimal("0")
    price, _ = result
    return price


def get_aero_price_usd(w3: Web3, block_identifier: int) -> Decimal:
    """AERO price in USD at block via DeFiLlama."""
    result = _defillama_price_at_block(w3, DEFILLAMA_CHAIN, AERO_TOKEN_ADDRESS, block_identifier)
    if result is None:
        return Decimal("0")
    price, _ = result
    return price


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
# ERC20/ERC721 Transfer(address,address,uint256) — used to detect token transfers to/from ADDRESS in tx receipts
TRANSFER_TOPIC = _topic_hex("Transfer(address,address,uint256)")
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
    """Fetch NFPM events (IncreaseLiquidity, DecreaseLiquidity, Collect). One request per chunk (all 3 topics); on 413 retries with parallel single-topic requests."""
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


def _topic_match(log_topic: Any, expected_hex: str) -> bool:
    """Compare log topic (may be HexBytes) to expected '0x...' hex string."""
    if log_topic is None:
        return False
    h = log_topic.hex() if hasattr(log_topic, "hex") else str(log_topic)
    if not h.startswith("0x"):
        h = "0x" + h
    return h.lower() == expected_hex.lower()


def _token_ids_from_nfpm_logs(w3: Web3, nfpm: Any, logs: list) -> set:
    """Extract unique tokenIds from NFPM IncreaseLiquidity, DecreaseLiquidity, Collect logs."""
    out = set()
    for log in logs:
        decoded = decode_nfpm_log(w3, log, nfpm)
        if decoded and len(decoded) >= 2:
            out.add(decoded[1])
    return out


def _token_ownership_at_block(
    w3: Web3,
    nfpm: Any,
    token_ids: set,
    block: int,
    address: str,
    gauge_addresses: list,
) -> tuple[set, set]:
    """
    For each tokenId call ownerOf and positions(tokenId).operator at block.
    Returns (token_ids_owned_or_operated, token_ids_staked).
    - owned_or_operated: ownerOf == ADDRESS or operator == ADDRESS (direct attribution).
    - staked: ownerOf in GAUGE_ADDRESSES (attribute via tx involvement).
    """
    address_lower = address.lower()
    gauge_lower = {(a or "").strip().lower() for a in gauge_addresses if a}
    gauge_lower = {g if g.startswith("0x") else "0x" + g for g in gauge_lower}
    owned_or_operated: set = set()
    staked: set = set()
    for tid in token_ids:
        try:
            owner = nfpm.functions.ownerOf(tid).call(block_identifier=block)
            owner_lower = (owner or "").lower()
            if owner_lower == address_lower:
                owned_or_operated.add(tid)
                continue
            if owner_lower in gauge_lower:
                staked.add(tid)
                continue
            pos = nfpm.functions.positions(tid).call(block_identifier=block)
            operator = (pos[1] or "").lower()  # operator is index 1 in positions tuple
            if operator == address_lower:
                owned_or_operated.add(tid)
        except Exception:
            pass
    return (owned_or_operated, staked)


def _tx_involves_address(
    receipt: Optional[dict],
    tx: Optional[dict],
    address_lower: str,
    nfpm_address_lower: str,
    collect_recipients_in_tx: set,
) -> bool:
    """
    True if this tx involves ADDRESS: tx.from, or Collect recipient, or any ERC20 Transfer to/from ADDRESS.
    """
    if tx and (tx.get("from") or "").lower() == address_lower:
        return True
    if collect_recipients_in_tx and address_lower in {r.lower() for r in collect_recipients_in_tx}:
        return True
    if not receipt or not receipt.get("logs"):
        return False
    for log in receipt["logs"]:
        addr = (log.get("address") or b"").hex() if hasattr(log.get("address"), "hex") else str(log.get("address") or "")
        if not addr.startswith("0x"):
            addr = "0x" + addr
        addr = addr.lower()
        topics = log.get("topics") or []
        if len(topics) < 3:
            continue
        if not _topic_match(topics[0], TRANSFER_TOPIC):
            continue
        def _topic_to_addr(t):
            h = t.hex() if hasattr(t, "hex") else str(t)
            if h.startswith("0x"):
                h = h[2:]
            return ("0x" + h[-40:]).lower()
        from_addr = _topic_to_addr(topics[1])
        to_addr = _topic_to_addr(topics[2])
        if from_addr == address_lower or to_addr == address_lower:
            return True
    return False


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

    address_lower = address.lower()
    nfpm_address_lower = Web3.to_checksum_address(NFPM_ADDRESS).lower()

    # ---------- 1) Fetch all NFPM logs and extract unique tokenIds ----------
    nfpm_logs = get_all_nfpm_logs(w3, from_block, to_block, fallback_w3)
    unique_token_ids = _token_ids_from_nfpm_logs(w3, nfpm, nfpm_logs)

    # ---------- 2) Direct contract calls: ownerOf and positions().operator at to_block ----------
    token_ids_owned_or_operated, token_ids_staked = _token_ownership_at_block(
        w3, nfpm, unique_token_ids, to_block, address, GAUGE_ADDRESSES
    )

    # ---------- 3) Per-tx: does this tx involve ADDRESS? (tx.from, Collect recipient, or ERC20 transfer) ----------
    logs_by_tx: dict = defaultdict(list)
    for log in nfpm_logs:
        tx_hash = log["transactionHash"]
        tx_hash = tx_hash.hex() if isinstance(tx_hash, bytes) else tx_hash
        logs_by_tx[tx_hash].append(log)
    collect_recipients_by_tx: dict = defaultdict(set)
    for tx_hash, logs in logs_by_tx.items():
        for log in logs:
            decoded = decode_nfpm_log(w3, log, nfpm)
            if decoded and decoded[0] == "Collect" and len(decoded) > 5:
                collect_recipients_by_tx[tx_hash].add(decoded[5])
    tx_involves_address: dict = {}
    for tx_hash in logs_by_tx:
        receipt = get_tx_receipt(w3, tx_hash)
        tx_obj = w3.eth.get_transaction(tx_hash) if tx_hash else None
        tx_involves_address[tx_hash] = _tx_involves_address(
            receipt,
            tx_obj,
            address_lower,
            nfpm_address_lower,
            collect_recipients_by_tx.get(tx_hash, set()),
        )

    # ---------- 4) Filter NFPM logs: keep if tokenId is owned/operated by ADDRESS, or staked and tx involves ADDRESS, or tx involves ADDRESS ----------
    our_nfpm_logs = []
    for log in nfpm_logs:
        decoded = decode_nfpm_log(w3, log, nfpm)
        if not decoded or len(decoded) < 2:
            continue
        token_id = decoded[1]
        tx_hash = log["transactionHash"]
        tx_hash = tx_hash.hex() if isinstance(tx_hash, bytes) else tx_hash
        if token_id in token_ids_owned_or_operated:
            our_nfpm_logs.append(log)
        elif token_id in token_ids_staked and tx_involves_address.get(tx_hash, False):
            our_nfpm_logs.append(log)
        elif tx_involves_address.get(tx_hash, False):
            our_nfpm_logs.append(log)

    # ---------- 5) Fetch and filter Gauge logs (ClaimRewards where user == ADDRESS) ----------
    gauge_logs = get_all_gauge_logs(w3, from_block, to_block, fallback_w3)
    our_claim_events = []  # (user, amount, block, tx_hash)
    for log in gauge_logs:
        decoded = decode_gauge_log(log)
        if decoded and decoded[0] and decoded[0].lower() == address.lower():
            our_claim_events.append(decoded)

    # ---------- 6) Decode NFPM events and group by tx for Collect fee logic ----------
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

    # ---------- 7) Build deposit/withdraw event lists for USD valuation ----------
    deposit_events: list[tuple[int, int, int, int]] = []  # tokenId, amount0, amount1, block
    withdraw_events: list[tuple[int, int, int, int]] = []
    for token_id, events in deposit_amount0.items():
        for amt0, amt1, block in events:
            deposit_events.append((token_id, amt0, amt1, block))
    for token_id, events in withdraw_amount0.items():
        for amt0, amt1, block in events:
            withdraw_events.append((token_id, amt0, amt1, block))

    # ---------- 8) Fee-only Collect amounts (Collect - same-tx Decrease per tokenId) ----------
    fee_collect_events: list[tuple[int, int, int, int]] = []  # tokenId, fee0, fee1, block
    for tx_hash, collects in tx_collects.items():
        decrease_for_tx = tx_decrease.get(tx_hash, {})
        for token_id, amt0, amt1, block in collects:
            principal0, principal1 = decrease_for_tx.get(token_id, (0, 0))
            fee0 = amt0 - principal0 if amt0 >= principal0 else 0
            fee1 = amt1 - principal1 if amt1 >= principal1 else 0
            fee_collect_events.append((token_id, fee0, fee1, block))

    # ---------- 9) Resolve token0/token1 for each tokenId (cache) ----------
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

    # ---------- 10) USD values (DeFiLlama historical prices) ----------
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

    # ---------- 11) Gas costs for every tx that contains our NFPM or Gauge events ----------
    # (Bot/Router may be tx.from; we attribute gas to the LP for those txs.)
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
        gas_used = receipt.get("gasUsed") or 0
        if hasattr(gas_used, "to_wei"):
            gas_used = gas_used.to_wei()
        eff = receipt.get("effectiveGasPrice") or tx.get("gasPrice") or 0
        if hasattr(eff, "to_wei"):
            eff = eff.to_wei()
        gas_cost_wei += gas_used * eff

    eth_price = get_eth_price_usd(w3, to_block)
    gas_cost_usd = (Decimal(gas_cost_wei) / Decimal(10**18)) * eth_price

    # ---------- 12) Net profit and summary ----------
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
