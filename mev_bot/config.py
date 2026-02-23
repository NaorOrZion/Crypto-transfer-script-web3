"""
Central configuration for the MEV micro-farming bot.

Edit this file to change network, pool, wallet, gas, and timing settings.
Everything the bot needs is configured here — no magic scattered across modules.
"""

import os

from dotenv import load_dotenv

load_dotenv()


# ═════════════════════════════════════════════════════════════════════════════
# Network
# ═════════════════════════════════════════════════════════════════════════════
CHAIN_ID = 8453  # Base mainnet

# HTTP RPC — used for contract calls (slot0, nonce, base_fee, send_raw_transaction)
RPC_HTTP_URL = os.getenv("QUICKNODE_BASE_ENDPOINT") or "https://mainnet.base.org"

# WSS RPC — used for newHeads subscription (real-time block notifications)
# Auto-derived from the HTTP URL for QuickNode; override with env var if needed.
_http = os.getenv("QUICKNODE_BASE_ENDPOINT", "")
_auto_wss = _http.replace("https://", "wss://").replace("http://", "ws://") if _http else ""
RPC_WSS_URL = os.getenv("QUICKNODE_BASE_WSS_ENDPOINT") or _auto_wss or "wss://mainnet.base.org"


# ═════════════════════════════════════════════════════════════════════════════
# Wallets
# ═════════════════════════════════════════════════════════════════════════════
# The private key of the wallet that signs and sends both transactions.
PRIVATE_KEY = os.getenv("sender_private_key", "")

# Second wallet — can be used as a recipient in your contract if needed.
RECEIVER_ADDRESS = os.getenv("receiver_address", "")


# ═════════════════════════════════════════════════════════════════════════════
# Pool  (Aerodrome Slipstream WETH/USDC on Base)
# ═════════════════════════════════════════════════════════════════════════════
POOL_ADDRESS = "0xb2cc224c1c9fee385f8ad6a55b4d94e92359dc59"

POOL_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"name": "sqrtPriceX96", "type": "uint160"},
            {"name": "tick", "type": "int24"},
            {"name": "observationIndex", "type": "uint16"},
            {"name": "observationCardinality", "type": "uint16"},
            {"name": "observationCardinalityNext", "type": "uint16"},
            {"name": "unlocked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]


# ═════════════════════════════════════════════════════════════════════════════
# Custom Solidity contract  (enterAndStake / unstakeAndExit)
# ═════════════════════════════════════════════════════════════════════════════
# Set this to your deployed contract address (or add MEV_CONTRACT_ADDRESS to .env)
CONTRACT_ADDRESS = os.getenv("MEV_CONTRACT_ADDRESS", "")

CONTRACT_ABI = [
    {
        "inputs": [
            {"name": "tickLower", "type": "int24"},
            {"name": "tickUpper", "type": "int24"},
            {"name": "amount0Desired", "type": "uint256"},
            {"name": "amount1Desired", "type": "uint256"},
            {"name": "amount0Min", "type": "uint256"},
            {"name": "amount1Min", "type": "uint256"},
            {"name": "deadline", "type": "uint256"},
            {"name": "gauge", "type": "address"},
        ],
        "name": "enterAndStake",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "tokenId", "type": "uint256"},
            {"name": "amount0Min", "type": "uint256"},
            {"name": "amount1Min", "type": "uint256"},
            {"name": "deadline", "type": "uint256"},
            {"name": "gauge", "type": "address"},
        ],
        "name": "unstakeAndExit",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


# ═════════════════════════════════════════════════════════════════════════════
# Gauge
# ═════════════════════════════════════════════════════════════════════════════
GAUGE_ADDRESS = "0xF33a96b5932D9E9B9A0eDA447AbD8C9d48d2e0c8"


# ═════════════════════════════════════════════════════════════════════════════
# Gas strategy  (EIP-1559 for Base sequencer)
# ═════════════════════════════════════════════════════════════════════════════
# TX 1 (enter): low tip → sequencer includes it naturally at the tail of the block
ENTER_PRIORITY_FEE_GWEI = 0.001

# TX 2 (exit): boosted tip → sequencer prioritises it at the top of the next block
EXIT_PRIORITY_FEE_GWEI = 0.1

# Safety multiplier over base_fee for maxFeePerGas ceiling
BASE_FEE_MULTIPLIER = 2

# Conservative gas limits to prevent out-of-gas reverts
ENTER_GAS_LIMIT = 600_000
EXIT_GAS_LIMIT = 500_000


# ═════════════════════════════════════════════════════════════════════════════
# Timing
# ═════════════════════════════════════════════════════════════════════════════
# Deadline for on-chain transactions (seconds from now)
DEADLINE_SECONDS = 60

# Dummy tokenId passed to unstakeAndExit — the contract uses its internal
# lastMintedTokenId instead of this value.
DUMMY_TOKEN_ID = 0
