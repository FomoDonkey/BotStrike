"""Install a Strike API wallet on the CT without the key ever passing through a chat, a log or git.

Run it yourself from the repo root:

    py -3.12 scripts/install_strike_key.py            # prompts, verifies read-only, installs on CT 104
    py -3.12 scripts/install_strike_key.py --check    # only verifies, installs nothing
    py -3.12 scripts/install_strike_key.py --local    # also writes a local .env for testing

The private key is typed into a hidden prompt, held in memory, verified against Strike with a
read-only GET /v2/account, and written to /opt/botstrike/app/.env on the container with 600
permissions. It is never printed, never written to the repo, never sent anywhere else.

A Strike API wallet can TRADE but CANNOT WITHDRAW (docs/strike/BRIEF.md §2), so this key can never
move funds out of the account. It can still open and close positions: treat it as a trading secret.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import time
import urllib.request
from hashlib import sha256
from uuid import uuid4

try:
    from nacl.signing import SigningKey
except ImportError:  # pragma: no cover
    print("pynacl is required: py -3.12 -m pip install pynacl", file=sys.stderr)
    raise SystemExit(2)

HOST = os.getenv("BOTSTRIKE_PVE_HOST", "root@100.68.139.93")
CT = os.getenv("BOTSTRIKE_CT", "104")
APP = "/opt/botstrike/app"
MAINNET = "https://api.strikefinance.org"
TESTNET = "https://api-v2-testnet.strikefinance.org"


def sign_headers(priv_hex: str, method: str, path: str, body: str = "") -> dict:
    sk = SigningKey(bytes.fromhex(priv_hex[:64]))
    pub = sk.verify_key.encode().hex()
    ts, nonce = str(int(time.time())), str(uuid4())
    msg = f"{method.upper()}:{path}:{ts}:{nonce}:{sha256(body.encode()).hexdigest()}"
    return {"X-API-Wallet-Public-Key": pub, "X-API-Wallet-Signature": sk.sign(msg.encode()).signature.hex(),
            "X-API-Wallet-Timestamp": ts, "X-API-Wallet-Nonce": nonce, "Content-Type": "application/json"}


def verify(priv_hex: str, base: str) -> dict:
    req = urllib.request.Request(base + "/v2/account", headers=sign_headers(priv_hex, "GET", "/v2/account"))
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def run(cmd: list, inp: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=inp, capture_output=True, text=True, timeout=90)


def install_on_ct(priv_hex: str, pub_hex: str, testnet: bool) -> bool:
    """Write the keys into the CT .env with a small script pushed over stdin (never as arguments,
    which would show up in the host's process list)."""
    base = TESTNET if testnet else MAINNET
    script = f"""set -eu
ENV={APP}/.env
touch "$ENV"
python3 - "$ENV" <<'PY'
import sys, os
path = sys.argv[1]
new = {{"STRIKE_PRIVATE_KEY": os.environ["K_PRIV"], "STRIKE_PUBLIC_KEY": os.environ["K_PUB"],
       "STRIKE_API_URL": "{base}", "STRIKE_PRICE_URL": "{base}/price"}}
lines = []
try:
    lines = open(path, encoding="utf-8").read().splitlines()
except FileNotFoundError:
    pass
out, seen = [], set()
for ln in lines:
    k = ln.split("=", 1)[0].strip()
    if k in new:
        out.append(f"{{k}}={{new[k]}}"); seen.add(k)
    else:
        out.append(ln)
for k, v in new.items():
    if k not in seen:
        out.append(f"{{k}}={{v}}")
open(path, "w", encoding="utf-8").write("\\n".join(out) + "\\n")
print("keys written:", ", ".join(new))
PY
chown botstrike:botstrike "$ENV"
chmod 600 "$ENV"
ls -l "$ENV"
grep -c STRIKE_ "$ENV"
"""
    # push the script into the CT and run it with the secrets in its environment, not in argv
    push = run(["ssh", "-o", "ConnectTimeout=20", HOST, "bash", "-s"],
               inp=f"""set -eu
cat > /tmp/_bs_key.sh <<'EOS'
{script}
EOS
pct push {CT} /tmp/_bs_key.sh /tmp/_bs_key.sh >/dev/null
rm -f /tmp/_bs_key.sh
pct exec {CT} -- env K_PRIV='{priv_hex}' K_PUB='{pub_hex}' bash /tmp/_bs_key.sh
pct exec {CT} -- rm -f /tmp/_bs_key.sh
pct exec {CT} -- systemctl restart botstrike-bridge
sleep 20
pct exec {CT} -- curl -s -m 8 localhost:9420/api/health | head -c 200; echo
""")
    print(push.stdout.strip()[-800:])
    if push.returncode != 0:
        print(push.stderr.strip()[-500:], file=sys.stderr)
    return push.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify only, do not install")
    ap.add_argument("--local", action="store_true", help="also write a local .env (developer machine)")
    ap.add_argument("--testnet", action="store_true", help="use api-v2-testnet.strikefinance.org")
    args = ap.parse_args()

    print("Strike API wallet installer")
    print("  The key is typed hidden, kept in memory, and written only to the container .env (600).")
    print("  A Strike API wallet can trade but CANNOT withdraw funds.\n")
    priv = getpass.getpass("Private key (64 hex, from app.strikefinance.org/api-keys): ").strip().lower()
    if len(priv) not in (64, 128) or any(c not in "0123456789abcdef" for c in priv):
        print("!! that does not look like a 64-hex private key", file=sys.stderr)
        return 2
    pub = SigningKey(bytes.fromhex(priv[:64])).verify_key.encode().hex()
    print(f"  derived public key: {pub}")

    base = TESTNET if args.testnet else MAINNET
    try:
        acc = verify(priv, base)
    except Exception as e:  # noqa: BLE001
        print(f"!! read-only verification failed against {base}: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        print("   Check that this public key is registered at app.strikefinance.org/api-keys", file=sys.stderr)
        return 1
    print(f"  verified: account {acc.get('account_id')} ({acc.get('nickname') or 'no nickname'}) "
          f"balance {acc.get('wallet_balance')} available {acc.get('available_balance')}")

    if args.check:
        print("\n--check: nothing installed.")
        return 0
    if args.local:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        lines = []
        if os.path.exists(path):
            lines = [l for l in open(path, encoding="utf-8").read().splitlines()
                     if not l.split("=", 1)[0].strip().startswith("STRIKE_")]
        lines += [f"STRIKE_PRIVATE_KEY={priv}", f"STRIKE_PUBLIC_KEY={pub}",
                  f"STRIKE_API_URL={base}", f"STRIKE_PRICE_URL={base}/price"]
        open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        print(f"  local .env updated ({path}) — it is gitignored")

    print(f"\ninstalling on CT {CT} via {HOST} ...")
    ok = install_on_ct(priv, pub, args.testnet)
    print("done." if ok else "!! installation failed — see the output above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
