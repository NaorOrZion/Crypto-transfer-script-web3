# Crypto Transfer Script (Web3)

A Python script for transferring Ethereum on the Sepolia test network using Web3.py and the Infura API.

## Features
- Connect to Sepolia testnet via Infura
- Send ETH transactions with proper nonce and gas management
- Wait for transaction confirmation
- Display transaction hash and Etherscan link

## Prerequisites
- Python 3.8+
- Virtual environment (venv)
- Infura API key
- Ethereum wallet with Sepolia testnet ETH

## Installation

1. **Clone the repository**
```bash
git clone https://github.com/NaorOrZion/Crypto-transfer-script-web3.git
cd "Sepolia Crypro Transfer"
```

2. **Create and activate virtual environment**
```bash
python -m venv venv
.\venv\Scripts\Activate.ps1
```

3. **Install dependencies**
```bash
pip install web3 python-dotenv
```

## Configuration

1. **Create a `.env` file** in the project root:
```
infura_project_id=your_infura_project_id
sender_private_key=your_private_key_here
receiver_address=0x...recipient_address
```

2. **Never commit `.env` to version control** - add it to `.gitignore`:
```
echo .env >> .gitignore
git add .gitignore
git commit -m "Add .env to gitignore"
```

## Usage

Run the script:
```bash
python main.py
```

The script will:
1. Connect to Sepolia testnet
2. Create a transaction
3. Sign and send it
4. Wait for confirmation
5. Display transaction details and Etherscan link

## Security ⚠️
- **Never share your private key**
- **Never commit private keys to git**
- Always use environment variables for sensitive data
- Use testnet (Sepolia) for testing before mainnet

## Transaction Details
- **Network**: Sepolia (chainId: 11155111)
- **Amount**: 0.15 ETH (adjustable in code)
- **Gas Limit**: 21000 (standard for ETH transfers)
- **Gas Price**: Dynamic (current network rate)

## Troubleshooting

**Connection failed:**
- Verify Infura API key is correct and active
- Check internet connection

**Invalid private key:**
- Ensure private key is in correct format (without `0x` prefix)
- Check for extra spaces in `.env`

**Insufficient funds:**
- Get Sepolia testnet ETH from a faucet:
  - https://sepoliafaucet.com
  - https://www.infura.io/faucet/sepolia

## View Transactions
After running the script, view your transaction on Etherscan:
```
https://sepolia.etherscan.io/tx/[transaction_hash]
```

## License
MIT
