from web3 import AsyncWeb3, WebSocketProvider, Web3
from dotenv import load_dotenv
import send_sepolia
import asyncio
import os
import time

load_dotenv()
INFURA_PROJECT_ID = os.getenv("infura_project_id")
WSS_URL = f"wss://sepolia.infura.io/ws/v3/{INFURA_PROJECT_ID}"

TARGET_ADDRESSES = [
    "0x3bFA4769FB09eefC5a80d6E87c3B9C650f7Ae48E", # Uniswap V3 SwapRouter02 (Sepolia)
    "0xC532a74256D3Db42D0Bf7a0400fEFDbad7694008", # Uniswap V2 Router (Sepolia Example)
]

w3 = Web3.HTTPProvider(os.getenv("QUICKNODE_BASE_ENDPOINT") or "https://mainnet.base.org")
if not w3.is_connected():
    print("Failed to connect to Base")
    exit()

POOL_ADDRESS = w3.to_checksum_address("0xb2cc224c1c9fee385f8ad6a55b4d94e92359dc59")

POOL_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]   

pool_contract = w3.eth.contract(address=POOL_ADDRESS, abi=POOL_ABI)

def fetch_current_tick():
    print(f"Fetching real-time data from pool: {POOL_ADDRESS}...")
    
    # 5. Call slot0()
    # This returns a tuple with multiple values. The tick is the 2nd item (index 1).
    slot0_data = pool_contract.functions.slot0().call()
    
    current_tick = slot0_data[1]
    
    print("-" * 40)
    print(f"Current Active Tick: {current_tick}")
    print("-" * 40)
    
    return current_tick

async def watch_signed_blocks():
    async with AsyncWeb3(WebSocketProvider(WSS_URL)) as w3:
        if await w3.is_connected():
            print("Connected via WebSocket!")
        else:
            print("Connection failed")
            return

        print("Listening for signed blocks...")
        last_ts = None
        last_receive = None

        new_heads_sub_id = await w3.eth.subscribe("newHeads")

        async for payload in w3.socket.process_subscriptions():
            subscription_id = payload.get("subscription")
            result = payload.get("result")

            if subscription_id == new_heads_sub_id:
                now = time.perf_counter()
                block_number = int(result.get("number", 0))
                # timestamp may be hex (e.g. "0x65f...") or int
                ts_raw = result.get("timestamp")
                if ts_raw is not None:
                    current_ts = int(ts_raw, 16) if isinstance(ts_raw, str) else int(ts_raw)
                else:
                    current_ts = None

                print("\n" + "=" * 40)
                print(f"NEW BLOCK MINED! Block: {block_number}")

                if last_receive is not None:
                    interval_ms = (now - last_receive) * 1000
                    print(f"Block-to-block interval: {interval_ms:.3f} ms")
                else:
                    print("Block-to-block interval: (first block, no previous)")
                last_receive = now

                if current_ts is not None:
                    if last_ts is not None:
                        chain_interval_sec = current_ts - last_ts
                        print(f"Chain timestamp delta: {chain_interval_sec} s")
                    last_ts = current_ts


if __name__ == "__main__":
    try:
        asyncio.run(watch_signed_blocks())
    except KeyboardInterrupt:
        print("Stopped listening.")