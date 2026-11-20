import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createSseParser,
  normalizeCalculatorInputs,
  normalizeDeclaredBlindspots,
} from '../src/lib/api/researchChatHelpers.ts';

import {
  classifyAuditBadge,
  isSafeHttpUrl,
  parseMarkdownBlocks,
  tokenizeInline,
} from '../src/components/research-chat/markdownParser.ts';

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
