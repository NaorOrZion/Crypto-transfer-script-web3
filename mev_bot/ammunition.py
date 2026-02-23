"""
Pre-build and offline-sign the enter/exit transaction pair.

Both transactions are signed before any block arrives, so broadcasting
is a single send_raw_transaction call with zero processing latency.

TX 1 (enterAndStake)  — low priority fee  → tail of current block.
TX 2 (unstakeAndExit) — high priority fee → top of next block.
"""

import time

from eth_account import Account
from web3 import Web3

from . import config


def prepare(
    w3: Web3,
    tick_lower: int,
    tick_upper: int,
    amount0: int,
    amount1: int,
) -> tuple[bytes, bytes]:
    """
    Build and sign the two interdependent transactions offline.

    Parameters
    ----------
    w3         : Connected Web3 instance (Base mainnet HTTP).
    tick_lower : Lower tick boundary for the CL position.
    tick_upper : Upper tick boundary for the CL position.
    amount0    : Desired amount of token0 (wei).
    amount1    : Desired amount of token1 (wei).

    Returns
    -------
    (raw_tx_enter, raw_tx_exit) : tuple[bytes, bytes]
        Ready-to-broadcast raw signed transaction bytes.
    """
    account = Account.from_key(config.PRIVATE_KEY)
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(config.CONTRACT_ADDRESS),
        abi=config.CONTRACT_ABI,
    )
    gauge = Web3.to_checksum_address(config.GAUGE_ADDRESS)

    nonce = w3.eth.get_transaction_count(account.address, "pending")
    base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
    max_fee = base_fee * config.BASE_FEE_MULTIPLIER
    deadline = int(time.time()) + config.DEADLINE_SECONDS

    enter_tip = Web3.to_wei(config.ENTER_PRIORITY_FEE_GWEI, "gwei")
    exit_tip = Web3.to_wei(config.EXIT_PRIORITY_FEE_GWEI, "gwei")

    # ── TX 1: enterAndStake ──────────────────────────────────────────────
    tx_enter = contract.functions.enterAndStake(
        tick_lower,
        tick_upper,
        amount0,
        amount1,
        0,         # amount0Min (slippage protection added later)
        0,         # amount1Min
        deadline,
        gauge,
    ).build_transaction({
        "from": account.address,
        "nonce": nonce,
        "gas": config.ENTER_GAS_LIMIT,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": enter_tip,
        "chainId": config.CHAIN_ID,
    })
    signed_enter = w3.eth.account.sign_transaction(tx_enter, account.key)

    # ── TX 2: unstakeAndExit ─────────────────────────────────────────────
    tx_exit = contract.functions.unstakeAndExit(
        config.DUMMY_TOKEN_ID,   # contract uses internal lastMintedTokenId
        0,                       # amount0Min
        0,                       # amount1Min
        deadline,
        gauge,
    ).build_transaction({
        "from": account.address,
        "nonce": nonce + 1,      # guarantees sequential ordering
        "gas": config.EXIT_GAS_LIMIT,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": exit_tip,
        "chainId": config.CHAIN_ID,
    })
    signed_exit = w3.eth.account.sign_transaction(tx_exit, account.key)

    return signed_enter.raw_transaction, signed_exit.raw_transaction
