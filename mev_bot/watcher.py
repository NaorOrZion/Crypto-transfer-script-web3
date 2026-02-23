"""
Real-time block watcher with millisecond timing and pool tick monitoring.

Connects to Base via WebSocket, subscribes to newHeads, and on every block:
  1. Measures receive-to-receive interval (ms precision via perf_counter).
  2. Reads the on-chain timestamp delta (second precision).
  3. Refreshes the pool's current tick from slot0.

The timing data is essential for the flashblock strategy — knowing the
sequencer's cadence lets us decide *when* to fire TX 1 (tail of block)
and TX 2 (top of next block).
"""

import asyncio
import time

from web3 import AsyncWeb3, Web3, WebSocketProvider

from . import config


def _parse_hex_or_int(value) -> int | None:
    """Safely convert a hex string or int from a block header field."""
    if value is None:
        return None
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    return int(value)


def _create_http_provider() -> Web3:
    w3 = Web3(Web3.HTTPProvider(config.RPC_HTTP_URL))
    if not w3.is_connected():
        raise RuntimeError(f"Cannot connect to HTTP RPC: {config.RPC_HTTP_URL}")
    return w3


async def run() -> None:
    """
    Main loop — watch blocks, measure timing, monitor tick.

    This function runs forever until interrupted (Ctrl+C).
    """
    # ── HTTP provider (contract calls, will also be used for sending txs) ─
    w3 = _create_http_provider()
    pool = w3.eth.contract(
        address=Web3.to_checksum_address(config.POOL_ADDRESS),
        abi=config.POOL_ABI,
    )

    # ── Initial state ────────────────────────────────────────────────────
    slot0 = pool.functions.slot0().call()
    current_tick = slot0[1]

    print("=" * 55)
    print("  MEV Micro-Farming Bot — Block Watcher")
    print("=" * 55)
    print(f"  Network:    Base (chain {config.CHAIN_ID})")
    print(f"  HTTP RPC:   {config.RPC_HTTP_URL[:50]}...")
    print(f"  WSS  RPC:   {config.RPC_WSS_URL[:50]}...")
    print(f"  Pool:       {config.POOL_ADDRESS}")
    print(f"  Tick (now):  {current_tick}")
    print("=" * 55)
    print()

    # ── WSS provider (block subscription) ────────────────────────────────
    last_receive: float | None = None
    last_chain_ts: int | None = None
    block_count = 0

    async with AsyncWeb3(WebSocketProvider(config.RPC_WSS_URL)) as w3_ws:
        if not await w3_ws.is_connected():
            raise RuntimeError(f"Cannot connect to WSS: {config.RPC_WSS_URL}")
        print("WebSocket connected — listening for new blocks...\n")

        sub_id = await w3_ws.eth.subscribe("newHeads")

        async for payload in w3_ws.socket.process_subscriptions():
            if payload.get("subscription") != sub_id:
                continue

            now = time.perf_counter()
            result = payload["result"]
            block_number = _parse_hex_or_int(result.get("number"))
            chain_ts = _parse_hex_or_int(result.get("timestamp"))
            block_count += 1

            # ── Timing ───────────────────────────────────────────────
            if last_receive is not None:
                interval_ms = (now - last_receive) * 1000
                interval_str = f"{interval_ms:,.3f} ms"
            else:
                interval_str = "—"

            if chain_ts is not None and last_chain_ts is not None:
                chain_delta = chain_ts - last_chain_ts
                chain_str = f"{chain_delta} s"
            else:
                chain_str = "—"

            # ── Pool tick ────────────────────────────────────────────
            slot0 = pool.functions.slot0().call()
            current_tick = slot0[1]

            # ── Print ────────────────────────────────────────────────
            print(
                f"Block {block_number}  |  "
                f"interval: {interval_str}  |  "
                f"chain delta: {chain_str}  |  "
                f"tick: {current_tick}"
            )

            # ── Update state ─────────────────────────────────────────
            last_receive = now
            if chain_ts is not None:
                last_chain_ts = chain_ts
