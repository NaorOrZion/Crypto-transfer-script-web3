"""
Real-time block watcher with millisecond timing and cross-block TX execution.

Connects via WebSocket, subscribes to newHeads, and on every block:
  1. Measures receive-to-receive interval (ms precision via perf_counter).
  2. Reads the on-chain timestamp delta (second precision).
  3. When ARMED: broadcasts TX 1, then TX 2 on the next block.
"""

import time

from eth_account import Account
from web3 import AsyncWeb3, Web3, WebSocketProvider

from . import config
from .ammunition import prepare, _is_token_mode


def _parse_hex_or_int(value) -> int | None:
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
    Main loop — watch blocks, measure timing, fire transactions when armed.
    """
    w3 = _create_http_provider()
    account = Account.from_key(config.PRIVATE_KEY)
    sender = account.address
    balance = w3.eth.get_balance(sender)

    token_mode = _is_token_mode()
    if token_mode:
        asset_label = f"ERC-20 ({config.TOKEN_ADDRESS[:10]}...)"
    else:
        asset_label = "Native ETH"

    print("=" * 60)
    print("  Cross-Block Flashblock Tester")
    print("=" * 60)
    print(f"  Chain:      Base Sepolia ({config.CHAIN_ID})")
    print(f"  HTTP RPC:   {config.RPC_HTTP_URL[:55]}")
    print(f"  WSS  RPC:   {config.RPC_WSS_URL[:55]}")
    print(f"  Sender:     {sender}")
    print(f"  Receiver:   {config.RECEIVER_ADDRESS}")
    print(f"  Balance:    {Web3.from_wei(balance, 'ether'):.6f} ETH")
    print(f"  Asset:      {asset_label}")
    print(f"  TX amounts: {config.TX1_AMOUNT} / {config.TX2_AMOUNT}")
    print(f"  Armed:      {config.ARMED}")
    print("=" * 60)
    print()

    last_receive: float | None = None
    last_chain_ts: int | None = None
    block_count = 0
    fired = False
    pending_tx2: bytes | None = None

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
                interval_ms = 0.0
                interval_str = "—"

            if chain_ts is not None and last_chain_ts is not None:
                chain_delta = chain_ts - last_chain_ts
                chain_str = f"{chain_delta} s"
            else:
                chain_str = "—"

            print(
                f"Block {block_number}  |  "
                f"interval: {interval_str}  |  "
                f"chain delta: {chain_str}"
            )

            # ── Fire TX 2 if it was queued from the previous block ───
            if pending_tx2 is not None:
                try:
                    tx2_hash = w3.eth.send_raw_transaction(pending_tx2)
                    print(f"  >> TX 2 SENT (top of block): {tx2_hash.hex()}")
                except Exception as e:
                    print(f"  >> TX 2 FAILED: {e}")
                pending_tx2 = None

            # ── Fire TX 1 + queue TX 2 ───────────────────────────────
            should_fire = (
                config.ARMED
                and not (config.FIRE_ONCE and fired)
                and block_count >= 3
            )

            if should_fire:
                try:
                    raw_tx1, raw_tx2 = prepare(w3)
                    tx1_hash = w3.eth.send_raw_transaction(raw_tx1)
                    print(f"  >> TX 1 SENT (tail of block): {tx1_hash.hex()}")
                    pending_tx2 = raw_tx2
                    fired = True
                except Exception as e:
                    print(f"  >> TX 1 FAILED: {e}")

            # ── Update state ─────────────────────────────────────────
            last_receive = now
            if chain_ts is not None:
                last_chain_ts = chain_ts
