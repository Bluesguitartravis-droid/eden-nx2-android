#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <aarch64-elf-or-provider-readable-binary> [address]" >&2
  echo "default address: 0x5af1c8 (SilverStar.nss crash-chain offset)" >&2
  exit 2
fi

BIN="$1"
ADDR="${2:-0x5af1c8}"
PROVIDER="${REA_PROVIDER:-ghidra}"
OUT="${REA_OUT:-rea-bananza-$(date +%Y%m%d-%H%M%S)}"

if ! command -v rea >/dev/null 2>&1; then
  echo "REA CLI not found. Install/setup from https://github.com/morluto/rea first." >&2
  exit 3
fi

mkdir -p "$OUT"

echo "Analyzing $BIN with provider=$PROVIDER at address=$ADDR"
rea inspect "$BIN" --provider "$PROVIDER" --json > "$OUT/inspect.json"
rea inspect-native-instruction "$BIN" "$ADDR" --provider "$PROVIDER" --json > "$OUT/instruction.json" || true
rea xrefs "$BIN" --address="$ADDR" --provider "$PROVIDER" --json > "$OUT/xrefs.json" || true
rea function "$BIN" --procedure="$ADDR" --provider "$PROVIDER" --json > "$OUT/function.json" || true
rea decompile "$BIN" --procedure="$ADDR" --provider "$PROVIDER" --json > "$OUT/decompile.json" || true
rea resolve-native-call-targets "$BIN" "$ADDR" --provider "$PROVIDER" --json > "$OUT/call-targets.json" || true

cat > "$OUT/README.txt" <<EOF
NX2 / Bananza REA capture
binary=$BIN
address=$ADDR
provider=$PROVIDER

Primary question:
- What instruction/call chain reaches SilverStar.nss +0x5AF1C8?
- Is the failing indirect target sourced from a null/missing IPC object or vtable?
- Which object initializer or service call owns that state?

Runtime evidence from the Android failure:
- guest eventually attempted execution at 0x8 / PC 0xC
- X8 was 0x8
- backtrace included SilverStar.nss +0x5AF1C8 -> +0x5AE400 -> +0x5AD7EC -> nnMain+0x5AC454
- older SX2A path lacked concrete aud:u / stpl:u object-return support
EOF

echo "REA evidence written to $OUT"
