# Address checklist for trace_address_aero_profit.py

Use this list to verify each address on [BaseScan](https://basescan.org). Update the values in `trace_address_aero_profit.py` if you find different canonical addresses.

---

## 1. ADDRESS (LP wallet)

| Variable    | Current value |
|------------|----------------|
| `ADDRESS`  | `0xCF979E05C91450e1FB5d98139101F0EFcd934d07` |

**What it is:** The wallet you are analyzing (LP / position owner).

**How to verify:** Your wallet address. No change needed unless you are analyzing a different wallet.

---

## 2. NFPM_ADDRESS (Non Fungible Position Manager)

| Variable       | Current value |
|----------------|----------------|
| `NFPM_ADDRESS` | `0x827922686190790b37229fd06084350e74485b72` |

**What it is:** The contract that **emits** IncreaseLiquidity, DecreaseLiquidity, Collect, and Transfer for Slipstream positions. It is also the ERC‑721 contract for position NFTs.

**How to verify:**
1. Open one of your Slipstream txs on BaseScan (e.g. the one that moved WETH/USDC and the position NFT).
2. In **Transaction Receipt Event Logs**, find a log named **IncreaseLiquidity** or **Collect** or **Transfer** for tokenId 49855649.
3. The **Address** of that log (first column) is the NFPM. It should be the same as the “Slipstream Position NFT” / “Non Fungible Position Manager” contract.

**BaseScan link:** https://basescan.org/address/0x827922686190790b37229fd06084350e74485b72

---

## 3. GAUGE_ADDRESSES (pool gauge(s))

| Variable          | Current value |
|-------------------|----------------|
| `GAUGE_ADDRESSES` | `["0xF33a96b5932D9E9B9A0eDA447AbD8C9d48d2e0c8"]` |

**What it is:** The gauge contract for your pool. It receives staked position NFTs and emits **ClaimRewards(user, amount)** when AERO is claimed.

**How to verify:**
1. On BaseScan, in the same tx where you staked the NFT, find the **Transfer** of the position NFT: “From … To **Aerodrome Finance: CL100-WETH/USDC Pool Gauge**”. The **To** address is the gauge.
2. Or find a **ClaimRewards** or **Deposit** event in the logs; the **Address** of that log is the gauge.

**BaseScan link:** https://basescan.org/address/0xF33a96b5932D9E9B9A0eDA447AbD8C9d48d2e0c8

You can add more gauges to the list if you have positions in multiple pools (one gauge per pool).

---

## 4. AERO_TOKEN_ADDRESS (AERO token on Base)

| Variable            | Current value |
|---------------------|----------------|
| `AERO_TOKEN_ADDRESS`| `0x940181a94A35A4569E4529A3CDfB74e38FD98631` |

**What it is:** The AERO ERC‑20 token contract on Base. Used only for **USD pricing** via DeFiLlama (historical AERO price).

**How to verify:** Search “Aerodrome AERO token Base” or open the token that appears in your ClaimRewards / AERO transfers on BaseScan and copy its contract address.

**BaseScan link:** https://basescan.org/address/0x940181a94A35A4569E4529A3CDfB74e38FD98631

---

## 5. WETH_BASE_ADDRESS (Wrapped ETH on Base)

| Variable           | Current value |
|--------------------|----------------|
| `WETH_BASE_ADDRESS`| `0x4200000000000000000000000000000000000006` |

**What it is:** Canonical Wrapped Ether on Base. Used only for **ETH → USD** price from DeFiLlama (and for valuing gas).

**How to verify:** Standard Base WETH; this is the usual address. If in doubt, check any WETH transfer in your tx — the token contract address is WETH.

**BaseScan link:** https://basescan.org/address/0x4200000000000000000000000000000000000006

---

## Quick copy‑paste (current script values)

```text
ADDRESS              = 0xCF979E05C91450e1FB5d98139101F0EFcd934d07
NFPM_ADDRESS         = 0x827922686190790b37229fd06084350e74485b72
GAUGE_ADDRESSES[0]    = 0xF33a96b5932D9E9B9A0eDA447AbD8C9d48d2e0c8
AERO_TOKEN_ADDRESS   = 0x940181a94A35A4569E4529A3CDfB74e38FD98631
WETH_BASE_ADDRESS    = 0x4200000000000000000000000000000000000006
```

---

## Where to edit in the script

- **Lines ~53–58:** `ADDRESS`, `NFPM_ADDRESS`, `GAUGE_ADDRESSES`
- **Lines ~139, ~153:** `AERO_TOKEN_ADDRESS`, `WETH_BASE_ADDRESS`

After changing any address, save and run again:

```bash
python .\Aerodrome\trace_address_aero_profit.py
```

---

## If you still get 0.00: run with debug

To see how many NFPM logs were found and how many passed the filter:

**Windows (PowerShell):**
```powershell
$env:TRACE_AERO_DEBUG="1"; python .\Aerodrome\trace_address_aero_profit.py
```

**Windows (CMD):**
```cmd
set TRACE_AERO_DEBUG=1
python .\Aerodrome\trace_address_aero_profit.py
```

Check the `[DEBUG]` lines:
- **NFPM logs in range: 0** → wrong `NFPM_ADDRESS` or wrong block range; fix the address from the checklist.
- **NFPM logs in range: N (N > 0)** but **Our NFPM logs: 0** → ownership or tx-involvement logic is filtering everything out; share the DEBUG output to debug further.
