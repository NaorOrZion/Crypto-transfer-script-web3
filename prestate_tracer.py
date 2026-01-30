from web3 import Web3
import os
import json
from dotenv import load_dotenv
import requests

load_dotenv()

# Configuration
target_tx_hash = "0x671e475d5dc3c883a19c4aaf7e6b5da347f5a73ba6e0d2e3b27bff4f3dc82b4d"

# List of providers to try, in order
providers = []

# 1. Infura (from .env)
infura_id = os.getenv("infura_project_id")
if infura_id:
    providers.append({
        "name": "Infura",
        "url": f"https://sepolia.infura.io/v3/{infura_id}"
    })

# 2. Public RPCs (fallback)
providers.append({"name": "Sepolia.org", "url": "https://rpc.sepolia.org"})
providers.append({"name": "Bordel.wtf", "url": "https://rpc.bordel.wtf/sepolia"})
providers.append({"name": "Alchemy", "url": f"https://eth-sepolia.g.alchemy.com/v2/{os.getenv('alchemy_project_id')}"})


def get_prestate_data(tx_hash):
    print(f"Goal: Retrieve debug_traceTransaction with prestateTracer for {tx_hash}")
    
    for provider in providers:
        print(f"\n--- Trying Provider: {provider['name']} ---")
        w3 = Web3(Web3.HTTPProvider(provider['url']))
        
        if not w3.is_connected():
            print("  Connection failed.")
            continue
            
        print("  Connected. Sending debug_traceTransaction request...")
        
        try:
            # We use make_request as debug_ methods are not standard eth namespace
            response = w3.provider.make_request(
                "debug_traceTransaction", 
                [tx_hash, {"tracer": "prestateTracer"}]
            )
            
            if 'error' in response:
                print(f"  RPC Error: {response['error']}")
                if 'code' in response['error'] and response['error']['code'] == -32601:
                     print("  Method not supported by this node.")
            
            elif 'result' in response:
                prestate_data = response['result']
                print(f"  SUCCESS! Pre-state data retrieved from {provider['name']}.\n")
                
                # Print the data in the requested format
                print("Prestate Data Retrieved Successfully:")
                for address, state in prestate_data.items():
                    print(f"\nAddress: {address}")
                    if 'balance' in state:
                        print(f"  Balance: {state['balance']}")
                    if 'nonce' in state:
                        print(f"  Nonce: {state['nonce']}")
                    if 'code' in state:
                        print(f"  Code Length: {len(state['code'])}")
                    if 'storage' in state:
                        print(f"  Storage Slots Used: {len(state['storage'])}")
                        print(f"  Storage Data: {json.dumps(state['storage'], indent=2)}")
                
                return # Stop after success
                
            else:
                print(f"  Unknown response format: {response.keys()}")

        except Exception as e:
            print(f"  Client/Network Error: {e}")
            
    print("\n\nAll providers failed to return prestateTracer data.")
    print("Optimization Tip: Using 'eth_createAccessList' is a common fallback if no archive node is available.")

if __name__ == "__main__":
    get_prestate_data(target_tx_hash)