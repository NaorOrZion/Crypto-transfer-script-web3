"""
Pre-build and offline-sign two interdependent transactions for
cross-block MEV micro-farming on Base (OP Stack).

TX 1 (enterAndStake) — lands at the tail of the current block with low tip.
TX 2 (unstakeAndExit) — lands at the top of the next block with boosted tip.

Both are signed offline so broadcasting is a single send_raw_transaction call
with zero processing latency.
"""

import time

from web3 import Web3

# ── Base network ─────────────────────────────────────────────────────────────
BASE_CHAIN_ID = 8453

# ── Gas configuration ────────────────────────────────────────────────────────
# TX 1: low tip — settle naturally at the tail of the current block
ENTER_MAX_PRIORITY_FEE = Web3.to_wei(0.001, "gwei")
# TX 2: boosted tip — sequencer should include this at the top of the next block
EXIT_MAX_PRIORITY_FEE = Web3.to_wei(0.1, "gwei")
# Safety margin over base_fee so maxFeePerGas is never too low
BASE_FEE_MULTIPLIER = 2
# Conservative gas limits to prevent OOG reverts
ENTER_GAS_LIMIT = 600_000
EXIT_GAS_LIMIT = 500_000
# Deadline offset from current time (seconds)
DEADLINE_OFFSET = 60
# Dummy tokenId for TX 2 — the contract uses its internal lastMintedTokenId
DUMMY_TOKEN_ID = 0


def prepare_cross_block_ammunition(
    contract,
    account,
    w3: Web3,
    tick_lower: int,
    tick_upper: int,
    amount0: int,
    amount1: int,
    gauge_address: str,
) -> tuple[bytes, bytes]:
    """
    Pre-build and sign the enter+exit transaction pair offline.

    Parameters
    ----------
    contract   : web3 Contract instance of the custom Solidity helper
                 (must expose enterAndStake / unstakeAndExit).
    account    : eth_account.Account with .address and .key.
    w3         : Connected Web3 instance (Base mainnet).
    tick_lower : Lower tick boundary for the CL position.
    tick_upper : Upper tick boundary for the CL position.
    amount0    : Desired amount of token0 (wei).
    amount1    : Desired amount of token1 (wei).
    gauge_address : Gauge to stake the minted NFT into.

    Returns
    -------
    (raw_tx_enter, raw_tx_exit) : tuple[bytes, bytes]
        Ready-to-broadcast raw signed transaction bytes.
    """
    nonce = w3.eth.get_transaction_count(account.address, "pending")
    base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
    max_fee = base_fee * BASE_FEE_MULTIPLIER
    deadline = int(time.time()) + DEADLINE_OFFSET
    gauge = Web3.to_checksum_address(gauge_address)

    # ── TX 1: enterAndStake ──────────────────────────────────────────────
    enter_call = contract.functions.enterAndStake(
        tick_lower,
        tick_upper,
        amount0,
        amount1,
        0,              # amount0Min — zero for now (slippage added later)
        0,              # amount1Min
        deadline,
        gauge,
    )
    tx_enter = enter_call.build_transaction({
        "from": account.address,
        "nonce": nonce,
        "gas": ENTER_GAS_LIMIT,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": ENTER_MAX_PRIORITY_FEE,
        "chainId": BASE_CHAIN_ID,
    })
    signed_enter = w3.eth.account.sign_transaction(tx_enter, account.key)

    # ── TX 2: unstakeAndExit ─────────────────────────────────────────────
    exit_call = contract.functions.unstakeAndExit(
        DUMMY_TOKEN_ID,  # contract ignores this, uses lastMintedTokenId
        0,               # amount0Min
        0,               # amount1Min
        deadline,
        gauge,
    )
    tx_exit = exit_call.build_transaction({
        "from": account.address,
        "nonce": nonce + 1,
        "gas": EXIT_GAS_LIMIT,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": EXIT_MAX_PRIORITY_FEE,
        "chainId": BASE_CHAIN_ID,
    })
    signed_exit = w3.eth.account.sign_transaction(tx_exit, account.key)

    return signed_enter.raw_transaction, signed_exit.raw_transaction
