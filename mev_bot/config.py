"""
Central configuration for the MEV micro-farming bot.

Currently configured for **Base Sepolia** testnet (~2 s blocks, OP Stack)
to test the cross-block flashblock technique.

Supports native ETH transfers or any ERC-20 token — set TOKEN_ADDRESS
to switch between them.
"""

import os

from dotenv import load_dotenv

load_dotenv()


# ═════════════════════════════════════════════════════════════════════════════
# Network — Base Sepolia  (OP Stack L2 testnet, ~2 s blocks)
# ═════════════════════════════════════════════════════════════════════════════
CHAIN_ID = 84532  # Base Sepolia

# HTTP RPC — nonce, gas, send_raw_transaction
RPC_HTTP_URL = os.getenv("INFURA_BASE_HTTP_URL")

# WSS RPC — newHeads subscription (public Base Sepolia doesn't support WSS, use Infura)
RPC_WSS_URL = os.getenv("INFURA_BASE_WSS_URL")


# ═════════════════════════════════════════════════════════════════════════════
# Wallets
# ═════════════════════════════════════════════════════════════════════════════
PRIVATE_KEY = os.getenv("sender_private_key", "")
RECEIVER_ADDRESS = os.getenv("receiver_address", "")


# ═════════════════════════════════════════════════════════════════════════════
# Token  (leave empty for native ETH transfers)
# ═════════════════════════════════════════════════════════════════════════════
# Set to an ERC-20 contract address to transfer that token instead of ETH.
# Examples:
#   ""                                             → native ETH
#   "0x036CbD53842c5426634e7929541eC2318f3dCF7e"  → USDC on Base Sepolia
#   "0x4200000000000000000000000000000000000006"  → WETH on Base Sepolia
TOKEN_ADDRESS = os.getenv("TOKEN_ADDRESS", "")

# Number of token decimals (only used when TOKEN_ADDRESS is set).
# ETH / WETH = 18, USDC = 6, etc.
TOKEN_DECIMALS = int(os.getenv("TOKEN_DECIMALS", "18"))


# ═════════════════════════════════════════════════════════════════════════════
# Transfer amounts
# ═════════════════════════════════════════════════════════════════════════════
# When TOKEN_ADDRESS is empty  → amount in wei   (1000 wei ≈ nothing)
# When TOKEN_ADDRESS is set    → amount in the token's smallest unit
TX1_AMOUNT = int(os.getenv("TX1_AMOUNT", "1000"))
TX2_AMOUNT = int(os.getenv("TX2_AMOUNT", "1000"))


# ═════════════════════════════════════════════════════════════════════════════
# Gas strategy (EIP-1559)
# ═════════════════════════════════════════════════════════════════════════════
# TX 1: low tip → tail of current block
TX1_PRIORITY_FEE_GWEI = float(os.getenv("TX1_PRIORITY_FEE_GWEI", "0.001"))

# TX 2: boosted tip → top of next block
TX2_PRIORITY_FEE_GWEI = float(os.getenv("TX2_PRIORITY_FEE_GWEI", "0.1"))

# Safety multiplier over base_fee for maxFeePerGas ceiling
BASE_FEE_MULTIPLIER = 2

# Gas limit — 21 000 for native ETH, ~65 000 for ERC-20 transfer()
ETH_GAS_LIMIT = 21_000
TOKEN_GAS_LIMIT = 80_000


# ═════════════════════════════════════════════════════════════════════════════
# Execution control
# ═════════════════════════════════════════════════════════════════════════════
# Set to True to actually broadcast transactions. False = watch-only.
ARMED = bool(os.getenv("ARMED", "").strip().lower() in ("1", "true", "yes"))

# Only fire once per run (to avoid draining testnet funds in a loop)
FIRE_ONCE = True
