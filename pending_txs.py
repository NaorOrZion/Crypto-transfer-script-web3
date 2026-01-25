from web3 import AsyncWeb3, WebSocketProvider
from dotenv import load_dotenv
import send_sepolia
import asyncio
import os

load_dotenv()
INFURA_PROJECT_ID = os.getenv("infura_project_id")
WSS_URL = f"wss://sepolia.infura.io/ws/v3/{INFURA_PROJECT_ID}"

async def watch_pending_txs():
    async with AsyncWeb3(WebSocketProvider(WSS_URL)) as w3:
        if await w3.is_connected():
            print("Connected via WebSocket!")
        else:
            print("Connection failed")
            return

        print(f"Listening for pending transactions and blocks...")
        
        # Subscribe to new pending transactions
        pending_tx_sub_id = await w3.eth.subscribe("newPendingTransactions")
        
        # Subscribe to new blocks (newHeads)
        new_heads_sub_id = await w3.eth.subscribe("newHeads")
        
        count_pending_txs = 0
        _, tx_hash = send_sepolia(0.01)

        async for payload in w3.socket.process_subscriptions():
            subscription_id = payload.get("subscription")
            result = payload.get("result")
            
            if subscription_id == pending_tx_sub_id:
                # It's a new pending transaction
                count_pending_txs += 1
                text_user_tx = "============== THIS IS YOUR TRANSACTION WAITING =============="
                if tx_hash.hex().lower() == result.hex().lower():
                    print(f"{text_user_tx}\nNew Pending Tx: {result.hex().lower()} (Total: {count_pending_txs})\n{text_user_tx}")
                else:
                    print(f"New Pending Tx: {result.hex().lower()} (Total: {count_pending_txs})")
                
            elif subscription_id == new_heads_sub_id:
                # It's a new block
                block_number = int(result.get("number", 0))
                print("\n" + "="*40)
                print(f"NEW BLOCK MINED! Block: {block_number}")
                print(f"Total pending transactions detected before this block: {count_pending_txs}")
                print("="*40 + "\n")
                
                # Reset the counter
                count_pending_txs = 0

if __name__ == "__main__":
    try:
        asyncio.run(watch_pending_txs())
    except KeyboardInterrupt:
        print("Stopped listening.")
