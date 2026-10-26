#!/bin/bash
# Create the self-signed "Whispered Local Signing" code-signing certificate
# that build.py uses for local builds (see CLAUDE.md, "Standalone build
# gotchas"). With it every rebuild keeps one code identity, so macOS keeps
# the Keychain "Always Allow" for the stored YouTube login.
#
# Usage: tools/create_signing_cert.sh [keychain]   (default: login keychain)
# Run once; it refuses to add a second certificate with the same name.
set -euo pipefail

NAME="Whispered Local Signing"
KEYCHAIN="${1:-$HOME/Library/Keychains/login.keychain-db}"
OPENSSL=/usr/bin/openssl   # the system LibreSSL: its .p12 files import cleanly

if security find-identity -p codesigning "$KEYCHAIN" | grep -q "\"$NAME\""; then
    echo "✓ \"$NAME\" is already in $KEYCHAIN"
    exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cat > "$WORK/cert.cnf" <<EOF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $NAME
[ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
EOF

"$OPENSSL" req -x509 -newkey rsa:2048 -nodes -days 3650 -config "$WORK/cert.cnf" \
    -keyout "$WORK/key.pem" -out "$WORK/cert.pem" 2>/dev/null
PASS="$(uuidgen)"
"$OPENSSL" pkcs12 -export -inkey "$WORK/key.pem" -in "$WORK/cert.pem" \
    -name "$NAME" -out "$WORK/cert.p12" -passout "pass:$PASS"
# -T: codesign may use the private key without asking every build.
security import "$WORK/cert.p12" -k "$KEYCHAIN" -P "$PASS" -T /usr/bin/codesign

echo "✓ Created \"$NAME\" in $KEYCHAIN — rebuild with .venv/bin/python build.py"
