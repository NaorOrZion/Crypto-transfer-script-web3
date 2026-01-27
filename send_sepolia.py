from web3 import Web3
import os
from dotenv import load_dotenv

load_dotenv()

def send_sepolia_core(amount_to_send):
    infura_project_id = os.getenv("infura_project_id")
    infura_url = f"https://sepolia.infura.io/v3/{infura_project_id}"
    web3 = Web3(Web3.HTTPProvider(infura_url))

    if web3.is_connected():
        print("Succesfully connected to Sepolia network")
    else:
        print("Connection failed")
        exit()

    sender_private_key = os.getenv("sender_private_key")
    account = web3.eth.account.from_key(sender_private_key)
    sender_address = account.address
    receiver_address = os.getenv("receiver_address")

    amount_in_wei = web3.to_wei(amount_to_send, 'ether')
    current_gas_price = web3.eth.gas_price
    gas_limit = 21000
    nonce = web3.eth.get_transaction_count(sender_address)
    chainId = 11155111

    tx = {
        'nonce': nonce,
        'to': receiver_address,
        'value': amount_in_wei, 
        'gas': gas_limit,
        'gasPrice': current_gas_price,
        'chainId': chainId
    }

    if not pre_state_validation(sender_address, gas_limit, current_gas_price, amount_in_wei, tx, web3):
        exit()

    signed_tx = web3.eth.account.sign_transaction(tx, sender_private_key)
    print(f"Sending {amount_to_send} ETH to {receiver_address}...")
    tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)

    print(f"Transaction sent! Hash: {web3.to_hex(tx_hash)}")
    return web3, tx_hash

def send_sepolia(amount_to_send):
    web3, tx_hash = send_sepolia_core(amount_to_send)
    print("Waiting for confirmation...")

    tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

    print(f"Transaction confirmed! Block number: {tx_receipt.blockNumber}")
    print(f"View on Etherscan: https://sepolia.etherscan.io/tx/{web3.to_hex(tx_hash)}")

    return tx_receipt.blockNumber, tx_hash

def pre_state_validation(sender_address, gas_limit, current_gas_price, amount_in_wei, tx, web3):
    # Check Balance
    balance = web3.eth.get_balance(sender_address)
    total_cost = amount_in_wei + (gas_limit * current_gas_price)

    if balance < total_cost:
        print(f"!!!!!!! Validation Failed: Insufficient funds. !!!!!!!")
        print(f"Have: {web3.from_wei(balance, 'ether')} ETH")
        print(f"Need: {web3.from_wei(total_cost, 'ether')} ETH")
        return False
    else:
        print("- Balance Check: Passed")

    # Simulate transaction on infura - shows gas concluded
    try:
        simulated_gas = web3.eth.estimate_gas(tx)
        print(f"- Simulation (Pre-State) Passed! Estimated Gas: {simulated_gas}")
    except Exception as e:
        print(f"!!!!!!! Validation Failed: Transaction will revert on-chain. !!!!!!!")
        print(f"Error details: {e}")
        return False

    return True

    
    