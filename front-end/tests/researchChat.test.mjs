import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createSseParser,
  normalizeCalculatorInputs,
  normalizeDeclaredBlindspots,
  resolveActivityLogFromMetadata,
} from '../src/lib/api/researchChatHelpers.ts';

import {
  classifyAuditBadge,
  isSafeHttpUrl,
  parseMarkdownBlocks,
  tokenizeInline,
} from '../src/components/research-chat/markdownParser.ts';

import { badgeToInspectorFocus } from '../src/components/research-chat/types.ts';

test('createSseParser parses chunked SSE events across CRLF boundaries and flushes tail', () => {
  const parser = createSseParser();

  const part1 = 'event: stage\r\ndata: {"stage":"routing","status":"running"}\r\n\r\n';
  const part2 = ': keepalive\nevent: delta\ndata: {"text":"Xin "}\n\nevent: delta\ndata: {"text":"chào';
  const part3 = '"}\n\nevent: done\ndata: {"answer":"Xin chào","metadata":{"mode":"RESEARCH_CHAT_V2"}}';

  const frames1 = parser.push(part1);
  assert.equal(frames1.length, 1);
  assert.equal(frames1[0].event, 'stage');
  assert.deepEqual(JSON.parse(frames1[0].data), { stage: 'routing', status: 'running' });

  const frames2 = parser.push(part2);
  assert.equal(frames2.length, 1);
  assert.equal(frames2[0].event, 'delta');
  assert.deepEqual(JSON.parse(frames2[0].data), { text: 'Xin ' });

  const frames3 = parser.push(part3);
  assert.equal(frames3.length, 1);
  assert.equal(frames3[0].event, 'delta');
  assert.deepEqual(JSON.parse(frames3[0].data), { text: 'chào' });

  const flushed = parser.flush();
  assert.equal(flushed.length, 1);
  assert.equal(flushed[0].event, 'done');
  assert.equal(JSON.parse(flushed[0].data).answer, 'Xin chào');
});

test('classifyAuditBadge recognizes citations, tables, calculators, KG tiers, and unverified flags', () => {
  const citOk = classifyAuditBadge('S07·P168 ✓');
  assert.deepEqual(
    { kind: citOk.kind, slot: citOk.slot, page: citOk.page, verified: citOk.verified },
    { kind: 'citation', slot: 'S07', page: 168, verified: true },
  );

  const citBad = classifyAuditBadge('S07_REV_COGS_DETAIL·P42 ✗');
  assert.deepEqual(
    { kind: citBad.kind, slot: citBad.slot, page: citBad.page, verified: citBad.verified },
    { kind: 'citation', slot: 'S07_REV_COGS_DETAIL', page: 42, verified: false },
  );

  const tbl = classifyAuditBadge('Bảng #2');
  assert.deepEqual({ kind: tbl.kind, index: tbl.index }, { kind: 'table', index: 2 });

  const calc = classifyAuditBadge('Calc #1');
  assert.deepEqual({ kind: calc.kind, index: calc.index }, { kind: 'calculator', index: 1 });

  const calcAlt = classifyAuditBadge('calc_03');
  assert.deepEqual({ kind: calcAlt.kind, index: calcAlt.index }, { kind: 'calculator', index: 3 });

  const kg = classifyAuditBadge('KG: CONFIRMED_BOTH');
  assert.deepEqual({ kind: kg.kind, tier: kg.tier }, { kind: 'kg', tier: 'CONFIRMED_BOTH' });

  const unv = classifyAuditBadge('UNVERIFIED_CLAIM');
  assert.equal(unv.kind, 'unverified');
});

test('markdownParser strips unsafe javascript/data URLs and parses tables, lists, and inline badges', () => {
  assert.equal(isSafeHttpUrl('https://aiinvest.vn/doc.pdf'), true);
  assert.equal(isSafeHttpUrl('javascript:alert(1)'), false);
  assert.equal(isSafeHttpUrl('data:text/html,<script>alert(1)</script>'), false);

  const inline = tokenizeInline(
    'Xem **GPM** [click](javascript:alert(1)) tại [S07·P168 ✓] và [Bảng #1] [Calc #1] [KG: CONFIRMED_BOTH].',
  );
  assert.equal(inline.some((t) => t.type === 'link'), false);
  assert.equal(inline.some((t) => t.type === 'bold'), true);
  const badges = inline.filter((t) => t.type === 'badge');
  assert.equal(badges.length, 4);
  assert.deepEqual(
    badges.map((b) => b.kind),
    ['citation', 'table', 'calculator', 'kg'],
  );

  const md = [
    '## Tổng hợp MOAT',
    '',
    '| Chỉ tiêu | 2024 | 2025 |',
    '| --- | --- | --- |',
    '| Biên gộp | 18.2% | 21.48% [Calc #1] |',
    '',
    '- Điểm mạnh lõi [S01·P12 ✓]',
    '> Lưu ý kiểm toán [S05·P30 ✓]',
  ].join('\n');

  const blocks = parseMarkdownBlocks(md);
  assert.deepEqual(
    blocks.map((b) => b.type),
    ['heading', 'table', 'list', 'blockquote'],
  );
  assert.equal(blocks[1].rows.length, 1);
  assert.equal(blocks[1].rows[0][2][1].kind, 'calculator');
});

test('normalizeDeclaredBlindspots and normalizeCalculatorInputs support object and array formats', () => {
  const blindspots = normalizeDeclaredBlindspots([
    { code: 'OUT_OF_PIT_WINDOW', reason: 'Dữ liệu 2026 bị chặn bởi PIT 2025-06-30', scope: 'S13' },
    'Thiếu thuyết minh quý gần nhất',
  ]);
  assert.equal(blindspots.length, 2);
  assert.equal(blindspots[0].code, 'OUT_OF_PIT_WINDOW');
  assert.equal(blindspots[1].code, 'INSUFFICIENT_EVIDENCE');

  const inputsFromObj = normalizeCalculatorInputs({
    rev_2024: { value: 1000, table_id: 'tbl-1', row: 'Doanh thu thuần' },
  });
  assert.equal(inputsFromObj.length, 1);
  assert.equal(inputsFromObj[0].name, 'rev_2024');
  assert.equal(inputsFromObj[0].value, 1000);

  const inputsFromArr = normalizeCalculatorInputs([
    { name: 'rev_2025', value: 1200, source: 'sag_extracted_tables:tbl-1' },
  ]);
  assert.equal(inputsFromArr.length, 1);
  assert.equal(inputsFromArr[0].name, 'rev_2025');
});

test('classifyAuditBadge and tokenizeInline parse [MEM:M#], [PORT:<SYM>], [SESS:S#] and UNVERIFIED_CLAIM variants without breaking existing badges', () => {
  const memOk = classifyAuditBadge('MEM:M1');
  assert.deepEqual(
    { kind: memOk.kind, refId: memOk.refId, index: memOk.index, verified: memOk.verified, label: memOk.label },
    { kind: 'memory', refId: 'M1', index: 1, verified: true, label: 'MEM:M1' },
  );

  const memBad = classifyAuditBadge('MEM:M99 UNVERIFIED_CLAIM');
  assert.deepEqual(
    { kind: memBad.kind, refId: memBad.refId, verified: memBad.verified, label: memBad.label },
    { kind: 'memory', refId: 'M99', verified: false, label: 'MEM:M99 UNVERIFIED' },
  );

  const portOk = classifyAuditBadge('PORT:FPT');
  assert.deepEqual(
    { kind: portOk.kind, refId: portOk.refId, verified: portOk.verified, label: portOk.label },
    { kind: 'portfolio', refId: 'FPT', verified: true, label: 'PORT:FPT' },
  );

  const portBad = classifyAuditBadge('PORT:XYZ UNVERIFIED_CLAIM');
  assert.deepEqual(
    { kind: portBad.kind, refId: portBad.refId, verified: portBad.verified, label: portBad.label },
    { kind: 'portfolio', refId: 'XYZ', verified: false, label: 'PORT:XYZ UNVERIFIED' },
  );

  const sessOk = classifyAuditBadge('SESS:S2');
  assert.deepEqual(
    { kind: sessOk.kind, refId: sessOk.refId, index: sessOk.index, verified: sessOk.verified, label: sessOk.label },
    { kind: 'session', refId: 'S2', index: 2, verified: true, label: 'SESS:S2' },
  );

  const sessBad = classifyAuditBadge('SESS:S99 UNVERIFIED_CLAIM');
  assert.deepEqual(
    { kind: sessBad.kind, refId: sessBad.refId, verified: sessBad.verified, label: sessBad.label },
    { kind: 'session', refId: 'S99', verified: false, label: 'SESS:S99 UNVERIFIED' },
  );

  // Coexistence with all existing badges in one sentence
  const tokens = tokenizeInline(
    'Theo [MEM:M1] và danh mục [PORT:FPT], đối chiếu phiên [SESS:S1] cùng [C1] [TBL:tbl_1] [CALC-1] [SCR:FPT] [KG:TRUE_PEER] [DISC-1].',
  );
  const badges = tokens.filter((t) => t.type === 'badge');
  assert.deepEqual(
    badges.map((b) => [b.kind, b.refId ?? b.tier ?? b.label]),
    [
      ['memory', 'M1'],
      ['portfolio', 'FPT'],
      ['session', 'S1'],
      ['citation', 'C1'],
      ['table', 'tbl_1'],
      ['calculator', 'CALC-1'],
      ['table', 'SCR:FPT'],
      ['kg', 'TRUE_PEER'],
      ['unverified', 'DISC-1'],
    ],
  );

  // Inspector focus routing for memory, portfolio, and session badges
  assert.deepEqual(badgeToInspectorFocus(memOk, 100), {
    tab: 'memory',
    memoryBadgeId: 'M1',
    nonce: 100,
  });
  assert.deepEqual(badgeToInspectorFocus(portOk, 101), {
    tab: 'memory',
    portfolioSymbol: 'FPT',
    nonce: 101,
  });
  assert.deepEqual(badgeToInspectorFocus(sessOk, 102), {
    tab: 'memory',
    sessionBadgeId: 'S2',
    nonce: 102,
  });
});

test('tokenizeInline preserves badges immediately followed by parenthetical text while still stripping unsafe links', () => {
  const tokens = tokenizeInline(
    'Theo [MEM:M1](đã ghim) và [PORT:FPT](1.000 CP), đối chiếu [SESS:S1](phiên 10/05) [C1](tr. 168), xem [tài liệu](https://aiinvest.vn/doc) và [bad](javascript:alert(1)).',
  );
  const badges = tokens.filter((t) => t.type === 'badge');
  assert.deepEqual(
    badges.map((b) => [b.kind, b.refId]),
    [
      ['memory', 'M1'],
      ['portfolio', 'FPT'],
      ['session', 'S1'],
      ['citation', 'C1'],
    ],
  );
  const fullText = tokens
    .filter((t) => t.type === 'text')
    .map((t) => t.text)
    .join('');
  assert.match(fullText, /\(đã ghim\)/);
  assert.match(fullText, /\(1\.000 CP\)/);
  assert.match(fullText, /\(phiên 10\/05\)/);
  assert.match(fullText, /\(tr\. 168\)/);
  assert.doesNotMatch(fullText, /javascript:/);

  const links = tokens.filter((t) => t.type === 'link');
  assert.equal(links.length, 1);
  assert.equal(links[0].href, 'https://aiinvest.vn/doc');

  const wl = classifyAuditBadge('PORT:WATCHLIST');
  const presets = classifyAuditBadge('PORT:PRESETS');
  assert.deepEqual({ kind: wl.kind, refId: wl.refId }, { kind: 'portfolio', refId: 'WATCHLIST' });
  assert.deepEqual({ kind: presets.kind, refId: presets.refId }, { kind: 'portfolio', refId: 'PRESETS' });
});

test('createSseParser parses thought, tool_call, and tool_result frames and resolveActivityLogFromMetadata falls back cleanly', () => {
  const parser = createSseParser();
  const payload = [
    'event: thought\ndata: {"id":"t-1","type":"thought","kind":"plan","title":"Bước 1","text":"Đang lập kế hoạch"}\n\n',
    'event: tool_call\ndata: {"call_id":"c-1","type":"tool_call","tool":"search_corporate_filings","status":"running","tickers":["FPT"]}\n\n',
    'event: tool_result\ndata: {"call_id":"c-1","type":"tool_call","tool":"search_corporate_filings","status":"done","crag_grade":"CORRECT","selected_documents":[{"citation_id":"C1","ticker":"FPT","sub_doc_slot":"BCTC_AUDITED","page":8}]}\n\n',
  ].join('');
  const frames = parser.push(payload);
  assert.deepEqual(
    frames.map((f) => f.event),
    ['thought', 'tool_call', 'tool_result'],
  );
  const tr = JSON.parse(frames[2].data);
  assert.equal(tr.crag_grade, 'CORRECT');
  assert.equal(tr.selected_documents[0].citation_id, 'C1');

  // Persisted activity_log takes precedence
  const fromLog = resolveActivityLogFromMetadata({
    activity_log: [{ id: 'a1', type: 'thought', title: 'Suy luận', text: 'OK' }],
    tool_traces: [{ tool: 'search_corporate_filings', returned_count: 4 }],
  });
  assert.equal(fromLog.length, 1);
  assert.equal(fromLog[0].id, 'a1');

  // Legacy message without activity_log degrades gracefully to tool_traces + citations
  const fallback = resolveActivityLogFromMetadata({
    route_intent: 'fact_lookup',
    as_of_date: '2026-11-25',
    tool_traces: [{ tool: 'search_corporate_filings', recall_count: 20, returned_count: 6, crag_grade: 'CORRECT' }],
    citations: [{ citation_id: 'C1', sub_doc_slot: 'BCTC_AUDITED', page: 5, quote: 'q', verified: true }],
  });
  assert.equal(fallback.length, 3);
  assert.equal(fallback[0].type, 'routing');
  assert.equal(fallback[1].type, 'tool_call');
  assert.equal(fallback[1].crag_grade, 'CORRECT');
  assert.equal(fallback[2].type, 'verification');
});

test('resolveActivityLogFromMetadata deduplicates running+done tool pairs and coalesces appended reasoning chunks', () => {
  const resolved = resolveActivityLogFromMetadata({
    activity_log: [
      {
        id: 'brief-reasoning-1',
        type: 'thought',
        kind: 'reasoning',
        title: 'Phân tích câu hỏi',
        text: 'Bước 1: kiểm tra nợ ngắn hạn ',
        append: true,
        timestamp_ms: 120,
      },
      {
        id: 'brief-reasoning-1',
        type: 'thought',
        kind: 'reasoning',
        title: 'Phân tích câu hỏi',
        text: 'và tồn kho của FRT.',
        append: true,
        timestamp_ms: 240,
      },
      {
        id: 'doc-K1-1',
        call_id: 'doc-K1-1',
        type: 'tool_call',
        tool: 'search_disclosures',
        label: 'Tra cứu thuyết minh (K1)',
        status: 'running',
        tickers: ['FRT'],
        timestamp_ms: 300,
      },
      {
        id: 'doc-K1-1',
        call_id: 'doc-K1-1',
        type: 'tool_call',
        tool: 'search_disclosures',
        label: 'Tra cứu thuyết minh (K1)',
        status: 'done',
        duration_ms: 185,
        summary: 'Chọn lọc 4 đoạn cho K1',
        completed_at_ms: 485,
      },
      {
        id: 'card-K1',
        type: 'thought',
        kind: 'verdict_card',
        verdict_status: 'PARTIAL',
        title: 'Thẻ kết luận [K1] · PARTIAL',
        text: 'Sơ bộ',
      },
      {
        id: 'card-K1',
        type: 'thought',
        kind: 'verdict_card',
        verdict_status: 'CONFIRMED',
        escalated: true,
        title: 'Thẻ kết luận [K1] · CONFIRMED · Đã đào sâu vòng 2',
        text: 'Đã xác minh đầy đủ',
      },
    ],
  });

  assert.equal(resolved.length, 3);
  assert.equal(resolved[0].id, 'brief-reasoning-1');
  assert.equal(resolved[0].text, 'Bước 1: kiểm tra nợ ngắn hạn và tồn kho của FRT.');
  assert.equal(resolved[1].id, 'doc-K1-1');
  assert.equal(resolved[1].status, 'done');
  assert.deepEqual(resolved[1].tickers, ['FRT']);
  assert.equal(resolved[1].summary, 'Chọn lọc 4 đoạn cho K1');
  assert.equal(resolved[2].id, 'card-K1');
  assert.equal(resolved[2].verdict_status, 'CONFIRMED');
  assert.equal(resolved[2].escalated, true);
});

