"""
Pre-build and offline-sign two transfers for cross-block testing.

Supports both native ETH and ERC-20 token transfers — controlled by
config.TOKEN_ADDRESS.

TX 1 — low priority fee  → tail of current block.
TX 2 — high priority fee → top of next block.
"""

from eth_account import Account
from web3 import Web3

from . import config

ERC20_TRANSFER_ABI = [
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


def _is_token_mode() -> bool:
    return bool(config.TOKEN_ADDRESS and config.TOKEN_ADDRESS.strip())


def _build_tx(
    w3: Web3,
    account,
    receiver: str,
    amount: int,
    nonce: int,
    max_fee: int,
    priority_fee: int,
) -> dict:
    """Build a single transaction dict (native ETH or ERC-20)."""
    if _is_token_mode():
        token = w3.eth.contract(
            address=Web3.to_checksum_address(config.TOKEN_ADDRESS),
            abi=ERC20_TRANSFER_ABI,
        )
        return token.functions.transfer(receiver, amount).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "gas": config.TOKEN_GAS_LIMIT,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority_fee,
            "chainId": config.CHAIN_ID,
        })

    return {
        "to": receiver,
        "value": amount,
        "gas": config.ETH_GAS_LIMIT,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": priority_fee,
        "nonce": nonce,
        "chainId": config.CHAIN_ID,
        "type": 2,
    }


def prepare(w3: Web3) -> tuple[bytes, bytes]:
    """
    Build and sign two sequential transfers offline.

    TX 1: sender → receiver, low tip  (nonce N)
    TX 2: sender → receiver, high tip (nonce N+1)

    Returns (raw_tx1, raw_tx2) ready for send_raw_transaction.
    """
    account = Account.from_key(config.PRIVATE_KEY)
    receiver = Web3.to_checksum_address(config.RECEIVER_ADDRESS)

    nonce = w3.eth.get_transaction_count(account.address, "pending")
    base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
    max_fee = base_fee * config.BASE_FEE_MULTIPLIER

    tx1_tip = Web3.to_wei(config.TX1_PRIORITY_FEE_GWEI, "gwei")
    tx2_tip = Web3.to_wei(config.TX2_PRIORITY_FEE_GWEI, "gwei")

    tx1 = _build_tx(w3, account, receiver, config.TX1_AMOUNT, nonce, max_fee, tx1_tip)
    tx2 = _build_tx(w3, account, receiver, config.TX2_AMOUNT, nonce + 1, max_fee, tx2_tip)

    signed1 = w3.eth.account.sign_transaction(tx1, account.key)
    signed2 = w3.eth.account.sign_transaction(tx2, account.key)

    return signed1.raw_transaction, signed2.raw_transaction
