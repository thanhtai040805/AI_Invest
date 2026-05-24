#!/bin/bash
set -e

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  INTEGRATION VERIFICATION PIPELINE"
echo "════════════════════════════════════════════════════════════"
echo ""

# Step 1: API Contract Audit
echo "▶  Step 1/5: API Contract Audit"
echo "   node scripts/check-api-contracts.mjs"
node scripts/check-api-contracts.mjs
echo ""

# Step 2: Socket.IO Event Contract Audit
echo "▶  Step 2/5: Socket.IO Event Contract Audit"
echo "   node scripts/check-socket-events.mjs"
node scripts/check-socket-events.mjs
echo ""

# Step 3: TypeScript — front-end
echo "▶  Step 3/5: TypeScript — front-end"
echo "   cd front-end && npx tsc --noEmit"
cd "$(dirname "$0")/.."
npx tsc --noEmit 2>&1 && echo "   ✅  TypeScript: OK (front-end)" || echo "   ❌  TypeScript: FAILED (front-end)"
echo ""

# Step 4: TypeScript — back-end
echo "▶  Step 4/5: TypeScript — back-end"
echo "   cd ../back-end && npx tsc --noEmit"
cd ../back-end
npx tsc --noEmit 2>&1 && echo "   ✅  TypeScript: OK (back-end)" || echo "   ❌  TypeScript: FAILED (back-end)"
echo ""

# Step 5: Import graph check
echo "▶  Step 5/5: Basic import graph check"
echo "   node scripts/verify-imports.mjs (reserved)"
echo "   ⏳  Not yet implemented — checks every import resolves to a real file"
echo ""

echo "════════════════════════════════════════════════════════════"
echo "  Done."
echo "════════════════════════════════════════════════════════════"
