#!/bin/bash
# Alif-specific pre-deploy checks
# Called by ~/src/expo/scripts/deploy.sh before the common deploy steps
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Check for known Expo Router incompatibilities
# href + tabBarButton on same screen crashes Expo Router
if python3 -c "
import re
text = open('$ROOT/frontend/app/_layout.tsx').read()
blocks = re.findall(r'options=\{\{(.*?)\}\}', text, re.DOTALL)
bad = [b for b in blocks if 'href' in b and 'tabBarButton' in b]
exit(1 if bad else 0)
"; then
    echo "  Layout config OK"
else
    echo "FAIL: _layout.tsx has both href and tabBarButton on the same screen"
    exit 1
fi
