#!/usr/bin/env bash
# setup-https.sh — Generate a trusted local TLS certificate for the media library
# so the phone camera works at https://<NUC-IP>:8443
#
# Requires: mkcert  (sudo apt install mkcert  OR  brew install mkcert)
# Run once on the NUC, then trust the CA on your phone (see instructions below).

set -e
cd "$(dirname "$0")/.."

# ── 1. Install mkcert if missing ────────────────────────────────────────────
if ! command -v mkcert &>/dev/null; then
  echo "Installing mkcert…"
  sudo apt-get update -qq && sudo apt-get install -y mkcert
fi

# ── 2. Install the local CA (trusted by this machine) ──────────────────────
mkcert -install
echo ""
echo "Local CA installed on this machine."

# ── 3. Detect LAN IP ───────────────────────────────────────────────────────
LAN_IP=$(ip -4 addr show scope global | grep -oP '(?<=inet )\d+\.\d+\.\d+\.\d+' | head -1)
if [ -z "$LAN_IP" ]; then
  echo "Could not auto-detect LAN IP. Edit CERT_HOSTS below and re-run."
  LAN_IP="192.168.1.100"
fi
echo "Detected LAN IP: $LAN_IP"

# ── 4. Generate certificate for localhost + LAN IP ─────────────────────────
mkdir -p certs
mkcert -key-file certs/key.pem -cert-file certs/cert.pem \
  localhost 127.0.0.1 "$LAN_IP" ::1

echo ""
echo "Certificate files written to:"
echo "  certs/cert.pem"
echo "  certs/key.pem"

# ── 5. Print the CA file path for phone setup ──────────────────────────────
CA_PATH=$(mkcert -CAROOT)/rootCA.pem
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "NEXT STEP — Trust the CA on your phone"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "CA certificate is at: $CA_PATH"
echo ""
echo "iPhone (Safari):"
echo "  1. Copy $CA_PATH to your phone (AirDrop, or serve it once):"
echo "     python3 -m http.server 9999 --directory \$(mkcert -CAROOT)"
echo "     Then open http://$LAN_IP:9999/rootCA.pem on Safari"
echo "  2. Settings → General → VPN & Device Management → install the profile"
echo "  3. Settings → General → About → Certificate Trust Settings → enable it"
echo ""
echo "Android (Chrome):"
echo "  1. Copy rootCA.pem to your phone"
echo "  2. Settings → Security → Install a certificate → CA certificate"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Start the server with HTTPS:"
echo "  uvicorn medialibrary.server:app \\"
echo "    --host 0.0.0.0 --port 8443 \\"
echo "    --ssl-certfile certs/cert.pem \\"
echo "    --ssl-keyfile  certs/key.pem"
echo ""
echo "Then open on your phone:  https://$LAN_IP:8443/scan"
echo ""
