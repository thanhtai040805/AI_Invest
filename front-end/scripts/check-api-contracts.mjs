#!/usr/bin/env node
/**
 * API CONTRACT AUDIT
 *
 * Extracts route definitions from back-end/ and API calls from front-end/
 * then compares them to find:
 *  - Frontend calls to routes that don't exist in backend
 *  - Backend routes that no frontend code calls
 *
 * Usage: node scripts/check-api-contracts.mjs
 */

import { readFileSync, existsSync, readdirSync, statSync } from 'fs';
import { join, resolve } from 'path';
import { execSync } from 'child_process';

const ROOT = resolve(import.meta.dirname, '..');
const BACKEND = resolve(ROOT, '..', 'back-end', 'src');

const MODULE_PREFIXES = {
  auth: '/api/v1/auth',
  market: '/api/v1/market',
  stock: '/api/v1/stock',
  screener: '/api/v1/screener',
  portfolio: '/api/v1/portfolio',
  ai: '/api/v1/ai',
  community: '/api/v1/community',
};

// ── Extract backend routes ──────────────────────────────────────
function extractBackendRoutes(root) {
  const routes = [];

  // Find route files
  const routeFiles = [];
  function walk(dir) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith('.routes.ts')) routeFiles.push(full);
    }
  }
  walk(root);

  for (const file of routeFiles) {
    const content = readFileSync(file, 'utf-8');
    const lines = content.split('\n');

    // Determine prefix from file path
    const relPath = file.replace(root, '').replace(/\\/g, '/');
    const moduleMatch = relPath.match(/modules\/(\w+)\//);
    const prefix = moduleMatch ? (MODULE_PREFIXES[moduleMatch[1]] || '') : '';

    const methodRegex = /\.(get|post|put|delete|patch)\(['"`]([^'"`]+)['"`]/g;
    let match;
    while ((match = methodRegex.exec(content)) !== null) {
      const method = match[1].toUpperCase();
      const path = match[2];
      const fullPath = `${prefix}${path}`;
      routes.push({ method, path: fullPath, file: relPath, line: getLineNumber(lines, match.index) });
    }
  }

  return routes;
}

// ── Extract frontend API calls ─────────────────────────────────
function extractFrontendCalls(root) {
  const calls = [];
  const apiFiles = [];

  const servicesDir = join(root, 'src', 'services');
  const libDir = join(root, 'src', 'lib');
  if (existsSync(join(servicesDir, 'api.ts'))) apiFiles.push(join(servicesDir, 'api.ts'));
  if (existsSync(join(libDir, 'api.ts'))) apiFiles.push(join(libDir, 'api.ts'));

  for (const file of apiFiles) {
    const content = readFileSync(file, 'utf-8');
    const lines = content.split('\n');

    // HTTP method calls: axios.get(url), apiClient.get(url), fetch(url)
    const httpCallRegex = /\.(get|post|put|delete|patch)\([`'"]([^`'"]+)[`'"]/g;
    let match;
    while ((match = httpCallRegex.exec(content)) !== null) {
      const method = match[1].toUpperCase();
      let path = match[2];
      // Strip query params and template vars
      let normalized = path.replace(/\?.*$/, '').replace(/\$\{[^}]+\}/g, '{param}');
      // Add /api/v1 prefix for Express backend (services/api.ts uses baseURL)
      if (file.includes('services')) {
        normalized = '/api/v1' + (normalized.startsWith('/') ? '' : '/') + normalized;
      }
      calls.push({
        method,
        path: normalized,
        file: file.replace(root, '').replace('/..', ''),
        line: getLineNumber(lines, match.index),
      });
    }
  }

  return calls;
}

function getLineNumber(lines, index) {
  let charCount = 0;
  for (let i = 0; i < lines.length; i++) {
    charCount += lines[i].length + 1;
    if (charCount > index) return i + 1;
  }
  return lines.length;
}

// ── Normalize paths for comparison ─────────────────────────────
function normalizePath(p) {
  return p
    .replace(/:(\w+)/g, '{param}')
    .replace(/\/\//g, '/')
    .replace(/\/$/, '');
}

function pathMatches(frontendPath, backendPath) {
  const fn = normalizePath(frontendPath);
  const bn = normalizePath(backendPath);
  return fn === bn;
}

// ── Main ────────────────────────────────────────────────────────
function main() {
  console.log('═'.repeat(60));
  console.log('  API CONTRACT AUDIT');
  console.log('═'.repeat(60));

  const backendRoutes = extractBackendRoutes(BACKEND);
  const frontendCalls = extractFrontendCalls(ROOT);

  console.log(`\n  Backend routes found:  ${backendRoutes.length}`);
  console.log(`  Frontend API calls:    ${frontendCalls.length}`);

  // Check: frontend calls that might not exist in backend
  let mismatches = 0;
  console.log('\n  ── Potential mismatches ──\n');

  for (const fc of frontendCalls) {
    let callPath = fc.path;
    callPath = callPath.replace(/\$\{[^}]+\}/g, '{param}').split('?')[0];

    const match = backendRoutes.find(br => {
      const bp = normalizePath(br.path);
      const cp = normalizePath(callPath);
      return br.method === fc.method && (cp === bp || bp.endsWith(cp) || cp.endsWith(bp));
    });

    if (match) continue; // matched

    // Broader match: strip /api/v1 prefix and try again
    const stripped = callPath.replace(/^\/api\/v1/, '');
    const broadMatch = backendRoutes.find(br => {
      const bp = normalizePath(br.path);
      return br.method === fc.method && (bp.endsWith(stripped) || bp === stripped);
    });

    if (!broadMatch) {
      console.log(`  ⚠  ${fc.method} ${callPath}`);
      console.log(`     → ${fc.file}:${fc.line}`);
      mismatches++;
    }
  }

  // Check: backend routes not called by frontend
  console.log(`\n  ── Backend routes with no frontend consumer ──\n`);
  let orphans = 0;
  for (const br of backendRoutes) {
    if (br.path.includes('/admin') || br.path.includes('/sync') ||
        br.path.includes('/backfill') || br.path.includes('/health')) {
      continue; // admin/internal routes
    }
    const match = frontendCalls.find(fc =>
      fc.method === br.method && (pathMatches(fc.path, br.path) || fc.path.includes(br.path))
    );
    if (!match) {
      console.log(`  ?  ${br.method} ${br.path}`);
      console.log(`     → ${br.file}:${br.line}`);
      console.log(`     → Not called by any frontend API code`);
      orphans++;
    }
  }

  console.log(`\n  Results:`);
  console.log(`     Potential mismatches:   ${mismatches}`);
  console.log(`     Orphan backend routes:  ${orphans}`);
  console.log('═'.repeat(60));
}

main();
