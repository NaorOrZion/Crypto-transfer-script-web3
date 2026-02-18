from web3 import Web3

# 1. Connect to Base Mainnet
base_url = "https://mainnet.base.org"
w3 = Web3(Web3.HTTPProvider(base_url))

if not w3.is_connected():
    print("Failed to connect to Base")
    exit()

# 2. Setup Contract Info
contract_address = Web3.to_checksum_address("0xCF979E05C91450e1FB5d98139101F0EFcd934d07")

# 3. Query Transfer events WHERE contract is the recipient (to address)
# Transfer event signature: Transfer(address indexed from, address indexed to, uint256 value)
def _topic_hex(signature: str) -> str:
    return "0x" + Web3.keccak(text=signature).hex()

def _block_hex(n: int) -> str:
    return hex(n)

transfer_topic = _topic_hex("Transfer(address,address,uint256)")

# Convert contract address to topic format (padded to 32 bytes)
def address_to_topic(address: str) -> str:
    return "0x" + address.lower().replace("0x", "").zfill(64)

contract_topic = address_to_topic(contract_address)

# Get events from the last 100 blocks
latest_block = w3.eth.block_number
from_block = latest_block - 100
to_block = latest_block

print(f"Querying Transfer events TO contract {contract_address}")
print(f"Block range: {from_block} to {to_block}")
print()

try:
    # Query for Transfer events where contract is the "to" address
    # topics: [Transfer signature, None (any from), contract_address (to)]
    logs = w3.eth.get_logs({
        "fromBlock": _block_hex(from_block),
        "toBlock": _block_hex(to_block),
        "topics": [
            transfer_topic,  # Transfer event
            None,            # Any "from" address
            contract_topic   # Contract as "to" address
        ]
    })
    
    if not logs:
        print("No Transfer events found where contract is the recipient.")
    else:
        print(f"Found {len(logs)} Transfer event(s) TO the contract:\n")
        for i, log in enumerate(logs, 1):
            # Decode the log data
            # topic[1] = from address, topic[2] = to address, data = value
            from_addr = "0x" + log["topics"][1].hex()[-40:] if len(log["topics"]) > 1 else "N/A"
            to_addr = "0x" + log["topics"][2].hex()[-40:] if len(log["topics"]) > 2 else "N/A"
            value = int(log["data"].hex(), 16) if log["data"] else 0
            
            print(f"--- Event {i} ---")
            print(f"Token Contract: {log['address']}")
            print(f"Transaction Hash: {log['transactionHash'].hex()}")
            print(f"Block Number: {log['blockNumber']}")
            print(f"From: {from_addr}")
            print(f"To: {to_addr}")
            print(f"Value: {value:,} wei ({value / 10**18:.6f} tokens)")
            print()
            
except Exception as e:
    print(f"Error querying events: {e}")
    import traceback
    traceback.print_exc()
