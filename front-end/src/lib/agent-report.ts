export type RecordData = Record<string, unknown>

export function object(value: unknown): RecordData {
  if (typeof value === "string") {
    try { return object(JSON.parse(value)) } catch { return {} }
  }
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as RecordData : {}
}

export function numeric(value: unknown): number | null {
  if ((typeof value !== "number" && typeof value !== "string") || (typeof value === "string" && !value.trim())) return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

export function timestamp(row: RecordData): string {
  return String(row.generated_at || row.created_at || row.evaluated_at || row.date || "")
}

export function newest(rows: RecordData[]): RecordData[] {
  return [...rows].sort((a, b) => (Date.parse(timestamp(b)) || 0) - (Date.parse(timestamp(a)) || 0))
}

export function cleanText(text: unknown): string {
  if (typeof text !== "string" || !text) return ""
  return text
    .replace(/phĂ¡n quyáº¿t/g, "phán quyết")
    .replace(/ph[n\?] quy\?t/gi, "phán quyết")
    .replace(/\[T\?NG/gi, "[TẦNG")
    .replace(/cho m\?\s+/g, "cho mã ")
    .replace(/Táº¦NG/g, "TẦNG")
    .replace(/Cháº¥p thuáº[\xad\u00ad]?n giáº£i ngĂ¢n tháº[\xad\u00ad]?n trá»[\x8d\u008d]?ng/g, "Chấp thuận giải ngân thận trọng")
    .replace(/mĂ£/g, "mã")
    .replace(/Ghi nháº[\xad\u00ad]?n cĂ¡c cáº£nh bĂ¡o thá»‹ trÆ°á»[\x9d\u009d]?ng\/Ä‘á»‹nh giĂ¡ tá»«/g, "Ghi nhận các cảnh báo thị trường/định giá từ")
    .replace(/Ă[\x81\u0081]?p trần tỷ trọng an toĂ[\xa0\u00a0]?n/g, "Áp trần tỷ trọng an toàn")
    .replace(/Ă[\x81\u0081]?p tráº§n tá»· trá»[\x8d\u008d]?ng an toĂ[\xa0\u00a0]?n/g, "Áp trần tỷ trọng an toàn")
    .replace(/v[aà\s\?]+[aá\s\?]+p d\?ng h\? s\? ph\?t/gi, "và áp dụng hệ số phạt")
    .replace(/vĂ\xa0 Ă¡p dá»¥ng há»\x87 sá»\x91 pháº¡t/g, "và áp dụng hệ số phạt")
    .replace(/vĂ  Ă¡p dá»¥ng há»‡ sá»‘ pháº¡t/g, "và áp dụng hệ số phạt")
    .replace(/PhĂª duyá»‡t toĂ n diá»‡n luáº­n Ä‘iá»ƒm Ä‘áº§u tÆ°/g, "Phê duyệt toàn diện luận điểm đầu tư")
    .replace(/Tá»· lá»‡ Risk\/Reward vÆ°á»£t trá»™i, rá»§i ro thÆ°Æ¡ng máº¡i á»Ÿ má»©c tháº¥p/g, "Tỷ lệ Risk/Reward vượt trội, rủi ro thương mại ở mức thấp")
    .replace(/Luáº­n Ä‘iá»ƒm/g, "Luận điểm")
    .replace(/phản biện/g, "phản biện")
    .replace(/quyáº¿t/g, "quyết")
    .replace(/phĂ¡n/g, "phán")
    .replace(/giáº£i ngĂ¢n/g, "giải ngân")
    .replace(/tháº­n trá» ng/g, "thận trọng")
}

// Each audit table stores a different shape; most do not have an `outputs` column.
export function logOutput(agent: string, row: RecordData): RecordData {
  const field: Record<string, string> = {
    market_surveillance: "outputs", universe_discovery: "filtered_counts",
    equity_research: "factor_raw_metrics", investment_thesis: "thesis_text",
    portfolio_risk: "garch_cash_trace", trade_execution: "orderbook_depth_snapshot",
    strategy_cio: "resolution_payload", system_governance: "audit_trail_verification",
  }
  return { ...row, ...object(row[field[agent]]), ...object(row.outputs) }
}

export function logSymbol(agent: string, row: RecordData): string {
  const out = logOutput(agent, row)
  const symbol = String(out.ticker || out.symbol || object(row.inputs).ticker || "")
  return ["ALL", "PORTFOLIO", "UNKNOWN", "N/A"].includes(symbol) ? "" : symbol
}

export function related(rows: RecordData[], thesis: RecordData): RecordData | undefined {
  // Never attach another thesis's verdict just because it has the same ticker.
  return thesis.thesis_id ? newest(rows).find(row => row.thesis_id === thesis.thesis_id) : undefined
}

export function decision(resolution?: RecordData) {
  const code = String(resolution?.final_resolution || "")
  if (["PROCEED_WITH_PENALTY", "APPROVE_CONDITIONAL"].includes(code)) {
    if (numeric(object(resolution?.verdict_payload).weight_cap) === 0)
      return { label: "Không phân bổ vốn", tone: "text-warning bg-warning/10", code }
    return { label: "Phê duyệt có điều kiện", tone: "text-gain bg-gain/10", code }
  }
  if (code === "FORCE_DOWNSIZE") return { label: "Yêu cầu hạ tỷ trọng", tone: "text-loss bg-loss/10", code }
  if (["CONFIRM_BLOCK", "UPHOLD_BLOCK", "DISCRETIONARY_BLOCK"].includes(code))
    return { label: "Chưa được chấp thuận đầu tư", tone: "text-loss bg-loss/10", code }
  if (code === "APPROVE")
    return { label: "Phê duyệt toàn diện", tone: "text-gain bg-gain/10", code }
  return { label: "Chưa có quyết định", tone: "text-secondary bg-soft", code }
}

export function allocationDecision(action: unknown) {
  const code = String(action || "")
  const label = ({ BUY: "Mua", SELL: "Bán", REDUCE: "Giảm vị thế", HOLD: "Giữ", SKIP: "Bỏ qua", REBALANCE: "Tái cân bằng" } as Record<string, string>)[code] || code || "Chưa có quyết định"
  const tone = code === "BUY" ? "text-gain bg-gain/10" : ["SELL", "REDUCE"].includes(code) ? "text-loss bg-loss/10" : "text-secondary bg-soft"
  return { label, tone, code }
}

/** Translate stored research into prose without changing its financial conclusions. */
export function investmentText(value: unknown): string {
  return cleanText(value)
    .replace(/\[(?:TẦNG|TIER)[^\]]*\]/gi, "")
    .replace(/\([^)]*(?:\bCSS\b|\bF[1-6]\s*[=:]|\bscore\b)[^)]*\)/gi, "")
    .replace(/\bBusiness Quality\s*\([\d.]+\)/gi, "chất lượng tài chính")
    .replace(/\bCSS\s*\([\d.]+\)/g, "đánh giá tổng hợp")
    .replace(/\bBusiness Quality\b/gi, "chất lượng tài chính")
    .replace(/\bCSS\b/g, "đánh giá tổng hợp")
    .replace(/\bCTS\s*[=:]?\s*\d+(?:\.\d+)?(?:\s*\/\s*100)?/gi, "kết quả thẩm định")
    .replace(/(?:áp dụng\s+)?hệ số phạt\s*(?:Kelly)?\s*(?:lambda|λ|\\lambda)?\s*[=:]?\s*0\.50?\b/gi, "quy mô giải ngân được giảm một nửa")
    .replace(/(?:áp dụng\s+)?hệ số phạt\s*(?:Kelly)?\s*(?:lambda|λ|\\lambda)?\s*[=:]?\s*0\.25\b/gi, "quy mô giải ngân còn một phần tư")
    .replace(/(?:áp dụng\s+)?hệ số phạt\s*(?:Kelly)?\s*(?:lambda|λ|\\lambda)?\s*[=:]?\s*[\d.]+/gi, "quy mô giải ngân đã được điều chỉnh")
    .replace(/\bGPM\b/g, "biên lợi nhuận gộp")
    .replace(/\bMA(\d+)\b/g, "đường giá trung bình $1 phiên")
    .replace(/\bVolume\b/gi, "khối lượng giao dịch")
    .replace(/(\d+(?:\.\d+)?)\s*bps\b/gi, (_, bps: string) => `${Number(bps) / 100} điểm phần trăm`)
    .replace(/\s*>\s*(?=\d)/g, " hơn ")
    .replace(/Thỏa mãn 100% tiêu chuẩn an toàn vốn thể chế\.?/gi, "Lệnh đáp ứng các bước kiểm soát rủi ro được ghi nhận.")
    .replace(/\s{2,}/g, " ")
    .trim()
}

export function thesisDetails(thesis?: RecordData) {
  const snapshot = object(thesis?.thesis_snapshot)
  const body = snapshot.thesis_id && snapshot.thesis_id !== thesis?.thesis_id ? {} : object(snapshot.thesis_body)
  const months = numeric(thesis?.timeline_months) ?? numeric(String(body.timeline || "").replace(/M$/i, ""))
  const entry = numeric(thesis?.entry_price_estimated)
  const target = numeric(thesis?.target_price) ?? numeric(object(body.price_target).base_case)
  return {
    body,
    months: months !== null && months > 0 ? months : null,
    entry: entry !== null && entry > 0 ? entry : null,
    target: target !== null && target > 0 ? target : null,
    whyStock: investmentText(body.why_this_stock ?? thesis?.why_this_stock),
    whyNow: investmentText(body.why_now ?? thesis?.why_now),
    catalyst: investmentText(object(body.catalyst).description ?? thesis?.catalyst_description ?? thesis?.thesis_statement),
    risks: Array.isArray(thesis?.invalidation_conditions) ? thesis.invalidation_conditions : Array.isArray(object(body.exit_conditions).invalidation_triggers) ? object(body.exit_conditions).invalidation_triggers as unknown[] : [],
  }
}

export const investmentHorizons = [
  { key: "swing", label: "Lướt sóng" },
  { key: "medium", label: "Trung hạn" },
  { key: "long", label: "Dài hạn" },
] as const

// Display groups only; the saved timeline, target and execution policy are unchanged.
export function horizonGroup(months: number | null) {
  return months === null || months <= 0 ? null : months <= 1 ? "swing" : months <= 6 ? "medium" : "long"
}

export function confirmationNarratives(value: unknown) {
  const signals = object(value)
  const financial = String(signals.signal_1_factor || "")
  const flow = String(signals.signal_2_surveillance || "")
  const macro = String(signals.signal_3_macro_hmm || "")
  const passed = (text: string) => /^PASS\b/i.test(text.trim())
  const earnings = numeric(financial.match(/F4(?:\s+score)?\s*=\s*([\d.]+)/i)?.[1])
  const momentum = numeric(flow.match(/F3\s+Momentum\s*=\s*([\d.]+)/i)?.[1])
  const moneyFlow = numeric(flow.match(/F5\s+Flow\s*=\s*([\d.]+)/i)?.[1])
  const regime = macro.match(/Regime\s*=\s*([A-Z_]+)/i)?.[1] || ""
  const market = /BEAR|CRISIS|CONTRACTION/i.test(regime) ? "đang bất lợi" : /BULL|EXPANSION/i.test(regime) ? "đang hỗ trợ" : "đáp ứng bộ lọc thị trường"
  return [
    { key: "business", label: "Cơ bản doanh nghiệp", text: !financial ? "Chưa có kết luận điều kiện cơ bản trong bản ghi này." : !passed(financial) ? "Cổ phiếu chưa đáp ứng điều kiện cơ bản để tiếp tục lập luận điểm." : earnings !== null && earnings >= 60 ? "Tăng trưởng lợi nhuận đạt ngưỡng lựa chọn của hệ thống. Chất lượng doanh nghiệp và động lực tăng trưởng được giải thích trong luận điểm bên trên." : "Đánh giá tổng hợp hoặc định giá đạt điều kiện để tiếp tục nghiên cứu. Kết quả này cần đọc cùng bằng chứng doanh nghiệp trong luận điểm." },
    { key: "macro", label: "Bối cảnh vĩ mô", text: !macro ? "Chưa có kết luận vĩ mô gắn với luận điểm này." : /IDIOSYNCRATIC_VETO/i.test(macro) ? "Thị trường đang bất lợi; cổ phiếu được tiếp tục xem xét nhờ chất lượng tài chính và động lượng riêng. Điều kiện này chưa xác nhận vĩ mô hỗ trợ." : passed(macro) ? `Bối cảnh thị trường ${market}, cho phép tiếp tục xem xét cơ hội đầu tư.` : "Bối cảnh thị trường bất lợi và chưa đáp ứng điều kiện xem xét giải ngân." },
    { key: "technical", label: "Dòng tiền và phân tích kỹ thuật", text: !flow ? "Chưa có xác nhận về dòng tiền, xu hướng giá hoặc điểm mua kỹ thuật." : !passed(flow) ? "Dòng tiền và động lượng giá chưa đạt điều kiện theo dõi cơ hội mua." : `Đã đạt điều kiện ${momentum !== null && momentum >= 60 && moneyFlow !== null && moneyFlow >= 60 ? "về dòng tiền và xu hướng giá" : momentum !== null && momentum >= 60 ? "về xu hướng giá" : moneyFlow !== null && moneyFlow >= 60 ? "về dòng tiền" : "theo dõi dòng tiền hoặc xu hướng giá"}. Bản ghi này chưa xác nhận một điểm mua kỹ thuật cụ thể.` },
  ]
}

export function monitoredRisks(values: unknown[]) {
  const groups = [
    { key: "business", label: "Câu chuyện doanh nghiệp", items: [] as string[] },
    { key: "macro", label: "Vĩ mô", items: [] as string[] },
    { key: "technical", label: "Phân tích kỹ thuật", items: [] as string[] },
  ]
  for (const value of values) {
    const text = investmentText(value)
    if (!text) continue
    const normalized = text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase().replace(/đ/g, "d")
    const group = /lai suat|ty gia|lam phat|tin dung|vi mo|macro|cau tieu thu|suy thoai/.test(normalized) ? groups[1] : /duong gia trung binh|ma\d|khoi luong|ky thuat|gia gay|gia thung|swing|rsi|macd/.test(normalized) ? groups[2] : groups[0]
    if (!group.items.includes(text)) group.items.push(text)
  }
  return groups
}

export function thesisReview(thesis?: RecordData, counter?: RecordData, resolution?: RecordData) {
  const same = (row?: RecordData) => !!thesis?.thesis_id && row?.thesis_id === thesis.thesis_id
  if (same(resolution)) {
    const result = decision(resolution)
    if (["PROCEED_WITH_PENALTY", "APPROVE_CONDITIONAL", "FORCE_DOWNSIZE", "CONFIRM_BLOCK", "UPHOLD_BLOCK", "DISCRETIONARY_BLOCK", "APPROVE"].includes(result.code))
      return { ...result, complete: true }
  }
  if (same(counter)) {
    const code = String(counter?.verdict || "")
    if (/BLOCK|REJECT/i.test(code)) return { label: "Chưa được chấp thuận đầu tư", tone: "text-loss bg-loss/10", code, complete: true }
    if (["PROCEED", "APPROVE", "CONDITIONAL"].includes(code))
      return { label: "Đã thẩm định · chờ kết luận đầu tư", tone: "text-warning bg-warning/10", code, complete: false }
  }
  return { label: "Chờ hoàn tất thẩm định", tone: "text-secondary bg-soft", code: "", complete: false }
}

/** Risk approval must identify this allocation, never another same-ticker order. */
export function approvedOrder(plan: RecordData | undefined, riskLogs: RecordData[]) {
  const riskEntry = plan?.decision_id ? newest(riskLogs).find(row => object(object(row.es_97_5_inputs).proposed_order).decision_id === plan.decision_id) : undefined
  const proposal = object(object(riskEntry?.es_97_5_inputs).proposed_order)
  const sourceSide = String(proposal.side || "")
  const side = String(plan?.action === "REBALANCE" ? sourceSide : plan?.action || "")
  const expectedSide = side === "REDUCE" ? "SELL" : side
  const conflictingSide = ["BUY", "SELL"].includes(sourceSide) && ["BUY", "SELL"].includes(expectedSide) && sourceSide !== expectedSide
  const riskOutput = object(riskEntry?.garch_cash_trace)
  const risk = object(riskOutput.decision)
  const rawShares = numeric(risk.approved_shares)
  const shares = rawShares !== null && rawShares >= 0 && Number.isInteger(rawShares) ? rawShares : null
  const price = numeric(risk.price ?? risk.target_price)
  const weight = numeric(risk.approved_weight_pct)
  const idle = ["HOLD", "SKIP"].includes(side)
  const blocked = risk.action === "BLOCK" || riskOutput.risk_status === "BLOCK" || shares === 0
  const ready = !!riskEntry && ["PASS", "REDUCE"].includes(String(risk.action || "")) && ["BUY", "SELL", "REDUCE"].includes(side) && shares !== null && !conflictingSide
  const result = !plan ? { label: "Chưa có quyết định", tone: "text-secondary bg-soft", code: "" }
    : idle ? allocationDecision(side)
    : blocked ? { label: side === "BUY" ? "Chưa mua · lệnh bị chặn" : ["SELL", "REDUCE"].includes(side) ? "Chưa bán · lệnh bị chặn" : "Không giao dịch · lệnh bị chặn", tone: "text-loss bg-loss/10", code: "BLOCK" }
    : ready ? allocationDecision(side)
    : { label: "Chờ quyết định cuối cùng", tone: "text-warning bg-warning/10", code: "PENDING" }
  return {
    ...result, riskEntry,
    shares: idle || blocked ? 0 : ready ? shares : null,
    price: ready && !blocked && price !== null && price > 0 ? price : null,
    weight: ready && !blocked && weight !== null && weight >= 0 && weight <= 100 ? weight : null,
    rationale: investmentText(ready || blocked ? risk.rationale : idle ? plan?.rationale : ""),
    approved: idle || blocked || ready,
  }
}

export function executionRecord(row: RecordData) {
  const execution = logOutput("trade_execution", row)
  const metrics = object(execution.execution_metrics)
  const sides = [execution.action, object(execution.order).direction, execution.side, object(execution.execution_plan).side]
    .filter((value): value is string => typeof value === "string" && ["BUY", "SELL"].includes(value))
  const side = new Set(sides).size === 1 ? sides[0] : ""
  const mode = String(execution.execution_mode || "")
  // Successful shadow fills retain the execution strategy's market regime.
  const simulated = ["SHADOW", "PAPER", "PAPER_TRADING", "NORMAL", "STRESS", "CRISIS"].includes(mode)
  const replay = ["REPLAY", "POSTGRES_REPLAY"].includes(mode) || execution.status === "FILLED_REPLAY"
  const status = String(execution.status || execution.execution_decision || "")
  const quantity = numeric(metrics.executed_quantity ?? execution.shares)
  const price = numeric(metrics.average_execution_price ?? execution.executed_price)
  const filled = ["EXECUTED", "PARTIALLY_EXECUTED", "FILLED", "FILLED_REPLAY", "PARTIALLY_FILLED"].includes(status) && quantity !== null && quantity > 0 && Number.isInteger(quantity) && price !== null && price > 0
  const pending = ["PENDING_SHADOW", "PENDING", "NEW", "EXECUTE"].includes(status)
  const stopped = /BLOCK|CANCEL|SKIP|REJECT/.test(status)
  return {
    id: String(execution.order_id || row.id || ""),
    side: allocationDecision(side),
    label: filled ? status.startsWith("PARTIALLY") ? "Khớp một phần" : "Đã ghi nhận khớp" : pending ? "Đang chờ khớp" : stopped ? "Chưa thực hiện" : "Chưa xác nhận khớp",
    shares: filled ? quantity : pending || stopped ? 0 : null,
    price: filled ? price : null,
    simulated,
    replay,
  }
}

export function logSummary(agent: string, row: RecordData): string {
  const out = logOutput(agent, row)
  for (const key of ["debate_summary", "executive_rationale", "rationale", "debate_challenge_text", "debate_synthesis", "thesis_statement", "reason", "message", "final_resolution", "verdict", "status", "action_type"])
    if (typeof out[key] === "string" && out[key]) return cleanText(out[key])
  if (agent === "position_monitoring" && numeric(out.pnl_pct) !== null) return `Lãi/lỗ của lượt giám sát: ${out.pnl_pct}% · có thể là trung bình nhiều vị thế`
  return "Bản ghi dữ liệu · mở để xem kết quả và các bước tính toán."
}
