import argparse
import os
import sys
import time
import send_sepolia

from datetime import datetime
from collections import deque
from typing import Deque, Dict, Any, Optional
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

INFURA_PROJECT_ID = os.getenv("infura_project_id")
if not INFURA_PROJECT_ID:
    raise ValueError("Missing env var: infura_project_id")

w3 = Web3(Web3.HTTPProvider(f"https://sepolia.infura.io/v3/{INFURA_PROJECT_ID}"))
if not w3.is_connected():
    raise ConnectionError("Web3 provider not connected")


def listen_new_blocks_keep_recent(
    limit: int = 200,
    target_address: Optional[str] = None,
    expected_net_id: Optional[int] = None,
    target_block: Optional[int] = None
) -> None:
    """
    Listen for new blocks, keep the most recent `limit` block objects
    in memory, and print each as it arrives.
    
    If expected_net_id is set, verifies chain ID matches before starting.
    If target_address is set, prints only transactions to/from that address.
    """
    
    # 1. Verify Net ID if requested
    if expected_net_id is not None:
        chain_id = w3.eth.chain_id
        if chain_id != expected_net_id:
            print(f"ERROR: Expected network ID {expected_net_id}, but connected to {chain_id}")
            sys.exit(1)
        else:
            print(f"Verified network ID: {chain_id}")

    seen_hashes: set[str] = set()
    recent_blocks: Deque[Dict[str, Any]] = deque(maxlen=limit)
    block_filter = w3.eth.filter("latest")  # hashes of new blocks

    # If filtering by address, we usually need full transactions to inspect 'from'/'to'
    fetch_full_tx = bool(target_address)
    
    if target_address:
        print(f"Listening for new Sepolia blocks (keeping last {limit})...")
        print(f"Filtering for transactions involving: {target_address}")
    else:
        print(f"Listening for new Sepolia blocks (keeping last {limit})...")

    while True:
        # get_new_entries returns block hashes
        for block_hash in block_filter.get_new_entries():
            if block_hash in seen_hashes:
                continue  # skip duplicates in case of provider replay

            # fetch block details
            block = w3.eth.get_block(block_hash, full_transactions=fetch_full_tx)
            seen_hashes.add(block_hash)
            recent_blocks.append(block)

            if target_address:
                # Filter mode: iterate transactions
                count_found = 0
                # block.transactions is a list of Tx objects (if full_transactions=True)
                # or list of hashes (if False). We ensured True above if target_address is set.
                for tx in block.transactions:
                    # Case-insensitive comparison is safer for addresses
                    if (
                        target_address.lower() == tx["from"].lower()
                        or (tx["to"] and target_address.lower() == tx["to"].lower())
                    ):
                        if target_block:
                            if block.number != target_block:
                                print(f"Did not found in Block #{block.number} | Tx Hash: {tx['hash'].hex()}")
                            else:
                                print(f"!!!Found sent Block #{block.number} | Tx Hash: {tx['hash'].hex()}!!!")
                        else:
                            print(
                                f"!!!Found target address in Block #{block.number} | Tx Hash: {tx['hash'].hex()}!!!"
                            )
                        count_found += 1
                
                # Optional: print a heartbeat or summary if no match found, or just stay silent?
                # For now let's just print that we scanned the block clearly.
                if count_found > 0:
                    print(f"--- Block #{block.number} contained {count_found} matching txs ---")
                else:
                    # pure hearbeat
                    print(f"Block #{block.number} scanned, 0 matches.")
            else:
                # Original mode: summary only
                tx_count = len(block.transactions)
                if target_block:
                    if block.number != target_block:
                        print(f"Block #{block.number} | hash={block.hash.hex()} | txs={tx_count} | time={datetime.fromtimestamp(block.timestamp)}")
                    else:
                        print(f"###!!!Found transaction Block #{block.number} | Tx Hash: {tx['hash'].hex()}!!!###")
                else:
                    print(
                        f"Block #{block.number} | hash={block.hash.hex()} | txs={tx_count} | time={datetime.fromtimestamp(block.timestamp)}"
                    )
        time.sleep(1)


if __name__ == "__main__":
    amount_to_send = 0.05
    block_number_sent = send_sepolia.send_sepolia(amount_to_send)

    listen_new_blocks_keep_recent(
        limit=100,
        expected_net_id=11155111,
        target_block=block_number_sent
    )