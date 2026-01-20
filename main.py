from web3 import Web3
import os
from dotenv import load_dotenv
import os

load_dotenv()

infura_project_id = os.getenv("infura_project_id")
infura_url = f"https://sepolia.infura.io/v3/{infura_project_id}"
web3 = Web3(Web3.HTTPProvider(infura_url))

if web3.is_connected():
    print("Succesfully connected to Sepolia network")
else:
    print("Connection failed")
    exit()

sender_private_key = os.getenv("sender_private_key")

sender_address = web3.eth.account.from_key(sender_private_key).address

receiver_address = os.getenv("receiver_address")
amount_to_send = 0.15

nonce = web3.eth.get_transaction_count(sender_address)

tx = {
    'nonce': nonce,
    'to': receiver_address,
    'value': web3.to_wei(amount_to_send, 'ether'), 
    'gas': 21000, # Standard gas price, less than that and the tx will be canceled
    'gasPrice': web3.eth.gas_price,
    'chainId': 11155111 # sepolia test net ID
}

signed_tx = web3.eth.account.sign_transaction(tx, sender_private_key)
print(f"Sending {amount_to_send} ETH to {receiver_address}...")
tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)

print(f"Transaction sent! Hash: {web3.to_hex(tx_hash)}")
print("Waiting for confirmation...")

tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

print(f"Transaction confirmed! Block number: {tx_receipt.blockNumber}")
print(f"View on Etherscan: https://sepolia.etherscan.io/tx/{web3.to_hex(tx_hash)}")