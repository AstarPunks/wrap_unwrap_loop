import os, time, random, argparse
from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account
from web3.exceptions import ContractLogicError

load_dotenv()
RPC_URL = os.environ["RPC_URL"]
PK = os.environ["PRIVATE_KEY"]
SENDER = Web3.to_checksum_address(os.environ["FROM_ADDRESS"])
CHAIN_ID = 1868  # Soneium mainnet
WETH_ADDR = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")

w3 = Web3(Web3.HTTPProvider(RPC_URL))
acct = Account.from_key(PK)

# 最小ABI
WETH_ABI = [
    {"type":"function","name":"deposit","stateMutability":"payable","inputs":[],"outputs":[]},
    {"type":"function","name":"withdraw","stateMutability":"nonpayable","inputs":[{"name":"wad","type":"uint256"}],"outputs":[]},
    {"type":"function","name":"balanceOf","stateMutability":"view","inputs":[{"name":"owner","type":"address"}],"outputs":[{"type":"uint256"}]},
]
weth = w3.eth.contract(address=WETH_ADDR, abi=WETH_ABI)

def _rget(obj, key, default=None):
    try:
        return obj[key]
    except Exception:
        return getattr(obj, key, default)

def suggest_fees():
    try:
        hist = w3.eth.fee_history(5, "latest", [50])
        base = hist["baseFeePerGas"][-1]
        tip = hist["reward"][-1][0]
        max_fee = int(base * 1.12) + int(tip)
        return max_fee, int(tip), base
    except Exception:
        base = w3.eth.gas_price
        tip = w3.to_wei(0.1, "gwei")
        return int(base * 1.12) + tip, tip, base

def _func_estimate_gas(func, tx_params, fallback=100_000):
    """v5/v6 両対応 + リバート時はフォールバック"""
    try:
        try:
            return func.estimate_gas(tx_params)  # v6
        except AttributeError:
            return func.estimateGas(tx_params)   # v5
    except (ContractLogicError, ValueError) as e:
        # ガス見積りがrevert等で失敗した場合は安全側の固定値にフォールバック
        return fallback

def _extract_raw_tx(signed):
    raw = getattr(signed, "rawTransaction", None)
    if raw is not None:
        return raw
    raw = getattr(signed, "raw_transaction", None)
    if raw is not None:
        return raw
    if isinstance(signed, dict):
        raw = signed.get("rawTransaction") or signed.get("raw_transaction")
        if raw is not None:
            return raw
    if isinstance(signed, (bytes, bytearray)):
        return signed
    try:
        if len(signed) > 0 and isinstance(signed[0], (bytes, bytearray)):
            return signed[0]
    except Exception:
        pass
    raise TypeError(f"Unsupported signed tx type: {type(signed)!r}")

def send_tx(tx):
    signed = acct.sign_transaction(tx)
    raw = _extract_raw_tx(signed)
    txh = w3.eth.send_raw_transaction(raw)
    rec = w3.eth.wait_for_transaction_receipt(txh, timeout=180)

    eff_price = _rget(rec, "effectiveGasPrice", None)
    if eff_price is None:
        full_tx = w3.eth.get_transaction(txh)
        eff_price = _rget(full_tx, "gasPrice", None) or _rget(full_tx, "maxFeePerGas", None) or w3.eth.gas_price

    gas_used = int(_rget(rec, "gasUsed"))
    fee_wei = gas_used * int(eff_price)

    return txh.hex(), int(_rget(rec, "status")), gas_used, int(eff_price), fee_wei

def wrap(amount_wei, nonce):
    max_fee, max_pri, _ = suggest_fees()
    func = weth.functions.deposit()
    # depositはpayable。関数オブジェクト経由でbuild_transaction
    est = _func_estimate_gas(func, {"from": SENDER, "value": amount_wei}, fallback=80_000)
    gas_limit = int(est * 1.2)

    tx = func.build_transaction({
        "from": SENDER,
        "nonce": nonce,
        "value": amount_wei,
        "gas": gas_limit,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": max_pri,
        "chainId": CHAIN_ID,
    })
    return send_tx(tx)

def unwrap(amount_wei, nonce):
    max_fee, max_pri, _ = suggest_fees()
    func = weth.functions.withdraw(amount_wei)
    # 一部RPCでestimate_gasがrevertしやすいのでフォールバックを入れる
    est = _func_estimate_gas(func, {"from": SENDER}, fallback=100_000)
    gas_limit = int(est * 1.2)

    tx = func.build_transaction({
        "from": SENDER,
        "nonce": nonce,
        "gas": gas_limit,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": max_pri,
        "chainId": CHAIN_ID,
        "value": 0
    })
    return send_tx(tx)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=50, help="何ラウンド回すか")
    parser.add_argument("--once", action="store_true", help="1ラウンドだけ実行")
    args = parser.parse_args()

    rounds = 1 if args.once else args.rounds
    per_round = w3.to_wei(0.01, "ether")

    # セーフティ
    assert w3.eth.chain_id == CHAIN_ID, f"Unexpected chain: {w3.eth.chain_id}"
    eth_bal = w3.eth.get_balance(SENDER)
    assert eth_bal > w3.to_wei(0.01, "ether"), "ETH残高不足（ガス+元本）"

    nonce = w3.eth.get_transaction_count(SENDER)
    total_fee = 0

    for i in range(rounds):
        # WRAP
        txh, ok, gas, price, fee = wrap(per_round, nonce); nonce += 1
        print(f"[{i+1}/{rounds}] WRAP   tx={txh} gas={gas} price={w3.from_wei(price,'gwei')} gwei fee={w3.from_wei(fee,'ether')} ETH")
        total_fee += fee

        # 反映安定のための短い待機（RPC実装によっては有効）
        time.sleep(0.5)

        # WETH残高確認（失敗時の保険）
        wbal = weth.functions.balanceOf(SENDER).call()
        if wbal == 0:
            print(f"[{i+1}/{rounds}] UNWRAP skip (WETH balance=0)")
            if not args.once:
                time.sleep(random.uniform(2, 5))
            continue

        amt = min(per_round, wbal)

        # UNWRAP
        txh, ok, gas, price, fee = unwrap(amt, nonce); nonce += 1
        print(f"[{i+1}/{rounds}] UNWRAP tx={txh} gas={gas} price={w3.from_wei(price,'gwei')} gwei fee={w3.from_wei(fee,'ether')} ETH")
        total_fee += fee

        if not args.once:
            time.sleep(random.uniform(2, 5))

    print("\n=== SUMMARY ===")
    print(f"Total fee: {w3.from_wei(total_fee,'ether')} ETH for {rounds} rounds")

if __name__ == "__main__":
    main()
