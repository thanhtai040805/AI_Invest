#!/usr/bin/env node
/**
 * SOCKET.IO EVENT CONTRACT AUDIT
 *
 * Extracts emit events from back-end/ and subscribe/listen events from front-end/
 * then compares them to find:
 *  - Backend events emitted but not subscribed by frontend
 *  - Frontend events subscribed but never emitted by backend
 *  - Orphan emit methods (defined but never called)
 *
 * Usage: node scripts/check-socket-events.mjs
 */

import { readFileSync, existsSync, readdirSync } from 'fs';
import { join, resolve } from 'path';
import { execSync } from 'child_process';

const ROOT = resolve(import.meta.dirname, '..');
const BACKEND_SRC = resolve(ROOT, '..', 'back-end', 'src');
const FRONTEND_SRC = resolve(ROOT, 'src');

function getLineNumber(lines, index) {
  let charCount = 0;
  for (let i = 0; i < lines.length; i++) {
    charCount += lines[i].length + 1;
    if (charCount > index) return i + 1;
  }
  return lines.length;
}

// ── Extract backend emit events ────────────────────────────────
function extractBackendEmits() {
  const emits = [];
  const emitMethods = [];

  const socketServicePath = join(BACKEND_SRC, 'services', 'socket.service.ts');
  if (!existsSync(socketServicePath)) {
    console.warn('  ⚠  Backend socket.service.ts not found at', socketServicePath);
    return { emits, emitMethods };
  }

  const content = readFileSync(socketServicePath, 'utf-8');
  const lines = content.split('\n');

  // Find emit method definitions — this.io.emit or socket.emit inside methods
  const emitPatterns = [
    /this\.io\.to\([^)]+\)\.emit\(['"`]([^'"`]+)['"`]/g,
    /socket\.emit\(['"`]([^'"`]+)['"`]/g,
    /this\.io\.emit\(['"`]([^'"`]+)['"`]/g,
  ];

  for (const regex of emitPatterns) {
    let m;
    while ((m = regex.exec(content)) !== null) {
      emits.push({
        event: m[1],
        line: getLineNumber(lines, m.index),
        file: 'services/socket.service.ts',
      });
    }
  }

  // Find emit method declarations
  const methodRegex = /emit(\w+)\(/g;
  let m2;
  while ((m2 = methodRegex.exec(content)) !== null) {
    const methodName = m2[1];
    const methodLine = getLineNumber(lines, m2.index);
    emitMethods.push({ name: methodName, line: methodLine });
  }

  return { emits, emitMethods };
}

// ── Find which emit methods are actually called ────────────────
function findCalledEmitMethods(emitMethods) {
  const called = new Set();

  try {
    const allTsFiles = execSync(
      `find "${BACKEND_SRC}" -name "*.ts" ! -path "*/node_modules/*"`,
      { encoding: 'utf-8' }
    ).trim().split('\n').filter(Boolean);

    for (const method of emitMethods) {
      const methodCallPattern = `socketService.emit${method.name}(`;
      for (const file of allTsFiles) {
        const content = readFileSync(file, 'utf-8');
        if (content.includes(methodCallPattern)) {
          called.add(method.name);
          break;
        }
      }
    }
  } catch (e) {
    console.warn('  ⚠  Could not search for emit callers:', e.message);
  }

  return called;
}

// ── Extract frontend subscriptions ─────────────────────────────
function extractFrontendSubscriptions() {
  const subs = [];

  // socket.ts — subscribe method definitions
  const socketPath = join(FRONTEND_SRC, 'services', 'socket.ts');
  if (existsSync(socketPath)) {
    const content = readFileSync(socketPath, 'utf-8');
    const lines = content.split('\n');

    // Find .on() calls inside the class
    const onRegex = /this\.socket\?\.on\(['"`]([^'"`]+)['"`]/g;
    let match;
    while ((match = onRegex.exec(content)) !== null) {
      if (!['connect', 'disconnect', 'connect_error', 'error'].includes(match[1])) {
        subs.push({
          event: match[1].replace(/\$\{[^}]+\}/g, '*'),
          line: getLineNumber(lines, match.index),
          file: 'services/socket.ts',
        });
      }
    }

    // Find subscribe* method calls (the channelMap)
    const channelRegex = /[`'"]stock:[^:]+:\${sym}[`'"]/g;
    if (channelRegex.test(content)) {
      subs.push({
        event: 'stock:*:*',
        line: 268,
        file: 'services/socket.ts',
        note: 'Per-symbol events via channelMap',
      });
    }
  }

  // hooks/useMarketData.ts — actual socketClient.subscribeStock calls
  const marketDataPath = join(FRONTEND_SRC, 'hooks', 'useMarketData.ts');
  if (existsSync(marketDataPath)) {
    const content = readFileSync(marketDataPath, 'utf-8');
    const onRegex = /on\w+:\s*\(/g;
    let match;
    while ((match = onRegex.exec(content)) !== null) {
      const callbackName = match[0].replace(':', '').trim();
      subs.push({
        event: `callback:${callbackName}`,
        line: getLineNumber(content.split('\n'), match.index),
        file: 'hooks/useMarketData.ts',
      });
    }
  }

  // providers/RealtimeProvider.tsx — socketClient.subscribe* calls
  const providerPath = join(FRONTEND_SRC, 'providers', 'RealtimeProvider.tsx');
  if (existsSync(providerPath)) {
    const content = readFileSync(providerPath, 'utf-8');
    const subRegex = /socketClient\.subscribe(\w+)\(/g;
    let match;
    while ((match = subRegex.exec(content)) !== null) {
    // Map subscribe method name to actual event name
    const subMap = {
      Indices: 'market:indices',
      Breadth: 'market:breadth',
      Snapshot: 'market:snapshot',
      Liquidity: 'market:liquidity',
      Heatmap: 'market:heatmap',
    };
    const eventName = subMap[match[1]] || `subscribe:${match[1]}`;
    subs.push({
      event: eventName,
      line: getLineNumber(content.split('\n'), match.index),
      file: 'providers/RealtimeProvider.tsx',
    });
    }
  }

  return subs;
}

// ── Main ────────────────────────────────────────────────────────
function main() {
  console.log('═'.repeat(60));
  console.log('  SOCKET.IO EVENT CONTRACT AUDIT');
  console.log('═'.repeat(60));

  const { emits, emitMethods } = extractBackendEmits();
  const calledMethods = findCalledEmitMethods(emitMethods);
  const frontendSubs = extractFrontendSubscriptions();

  console.log(`\n  Backend emit calls:      ${emits.length}`);
  console.log(`  Backend emit methods:    ${emitMethods.length}`);
  console.log(`  Frontend subscriptions:  ${frontendSubs.length}`);

  // Check orphan emit methods
  console.log(`\n  ── Orphan emit methods (defined but never called) ──\n`);
  let orphans = 0;
  for (const method of emitMethods) {
    if (!calledMethods.has(method.name)) {
      console.log(`  ?  emit${method.name}()  → services/socket.service.ts:${method.line}`);
      orphans++;
    }
  }
  if (orphans === 0) console.log('  ✅  All emit methods have callers');

  // Check emits not subscribed by frontend
  console.log(`\n  ── Backend events with no frontend listener ──\n`);
  let unsubscribed = 0;
  const frontendEvents = frontendSubs.map(s => s.event);
  for (const emit of emits) {
    const eventPattern = emit.event.replace(/\$\{[^}]+\}/g, '*');
    const hasMatch = frontendEvents.some(fe => {
      if (fe.includes('*')) {
        const feParts = fe.split(':');
        const epParts = eventPattern.split(':');
        return feParts.every((p, i) => p === '*' || p === epParts[i]);
      }
      return fe === eventPattern;
    });
    if (!hasMatch) {
      console.log(`  ⚠  ${eventPattern}  → ${emit.file}:${emit.line}`);
      unsubscribed++;
    }
  }
  if (unsubscribed === 0) console.log('  ✅  All backend events have frontend listeners');

  // Check frontend subscriptions with no backend emit
  console.log(`\n  ── Frontend subscriptions with no backend emitter ──\n`);
  let noEmitter = 0;
  for (const sub of frontendSubs) {
    if (sub.event.startsWith('callback:')) continue; // callback types, not direct events
    const eventPattern = sub.event.replace('*', '.*');
    const hasMatch = emits.some(e =>
      new RegExp(`^${eventPattern}$`).test(e.event) ||
      new RegExp(`^${e.event.replace(/\$\{[^}]+\}/g, '.*')}$`).test(sub.event)
    );
    if (!hasMatch) {
      console.log(`  ⚠  ${sub.event}  → ${sub.file}:${sub.line}`);
      noEmitter++;
    }
  }
  if (noEmitter === 0) console.log('  ✅  All frontend subscriptions have backend emitters');

  console.log(`\n  Results:`);
  console.log(`     Orphan emit methods:   ${orphans}`);
  console.log(`     Unsubscribed events:   ${unsubscribed}`);
  console.log(`     Unmatched subs:        ${noEmitter}`);
  console.log('═'.repeat(60));
}

main();
