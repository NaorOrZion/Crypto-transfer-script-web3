import asyncio
from web3 import AsyncWeb3, WebSocketProvider
from web3.exceptions import TransactionNotFound # חובה לייבא את זה
from dotenv import load_dotenv
import os
import send_sepolia

load_dotenv()
INFURA_PROJECT_ID = os.getenv("infura_project_id")
WSS_URL = f"wss://sepolia.infura.io/ws/v3/{INFURA_PROJECT_ID}"

async def watch_blocks_for_tx():
    target_tx = "0x..." # Replace with the transaction hash you want to monitor    
    is_tx_sent = False
    tx_block_number = None
    async with AsyncWeb3(WebSocketProvider(WSS_URL)) as w3:
        if await w3.is_connected(): 
            print("Connected via WebSocket!")
        else:
            print("Connection failed")
            return
        
        print(f"Listening for blocks to find tx: {target_tx}...")

        subscription_id = await w3.eth.subscribe("newHeads")

        async for payload in w3.socket.process_subscriptions():
            # payload מכיל את המידע הראשוני על הבלוק
            result = payload.get("result", {})
            block_number = int(result.get("number", 0))
            
            print(f"New Block Mined! Block Number: {block_number}")

            full_block = await w3.eth.get_block(block_number, full_transactions=True)
            
            found = False

            if block_number == tx_block_number:
                for tx in full_block.transactions:
                    if tx['hash'].hex().lower() == target_tx.hex().lower():
                        print("\n" + "="*40)
                        print(f"BINGO! Transaction found in block {block_number}")
                        print(f"From: {tx['from']}")
                        print(f"To: {tx['to']}")
                        print("="*40 + "\n")

            if not is_tx_sent:
                print("\n" + "="*40)
                print("Initiating transaction send...")
                print("="*40 + "\n")
                try:
                    tx_block_number, target_tx = send_sepolia.send_sepolia(0.01)
                    is_tx_sent = True
                except Exception as e:
                    print(f"Error sending transaction: {e}")
                    is_tx_sent = True


if __name__ == "__main__":
    asyncio.run(watch_blocks_for_tx())