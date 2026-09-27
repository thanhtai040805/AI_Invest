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
      return { label: "Bỏ qua / Chặn tỷ trọng", tone: "text-warning bg-warning/10", code }
    return { label: "Phê duyệt có điều kiện", tone: "text-gain bg-gain/10", code }
  }
  if (code === "FORCE_DOWNSIZE") return { label: "Yêu cầu hạ tỷ trọng", tone: "text-loss bg-loss/10", code }
  if (["CONFIRM_BLOCK", "UPHOLD_BLOCK", "DISCRETIONARY_BLOCK"].includes(code))
    return { label: "Bác bỏ / Veto giải ngân", tone: "text-loss bg-loss/10", code }
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

export const conditionLabels: Record<string, { label: string; desc: string; tone: string }> = {
  APPLY_RISK_PENALTY_0_5: { label: "Hệ số phạt Kelly λ = 0.50", desc: "Giảm 50% quy mô vốn theo khuyến nghị phản biện", tone: "border-warning/30 bg-warning/5 text-warning" },
  APPLY_RISK_PENALTY_0_25: { label: "Hệ số phạt Kelly λ = 0.25", desc: "Giảm 75% quy mô vốn", tone: "border-loss/30 bg-loss/5 text-loss" },
  MAX_POSITION_WEIGHT_CAP_8PCT: { label: "Trần tỷ trọng 8.0% NAV", desc: "Giới hạn an toàn tối đa 8% tổng tài sản", tone: "border-mineral/30 bg-mineral/5 text-mineral" },
  MAX_POSITION_WEIGHT_CAP_5PCT: { label: "Trần tỷ trọng 5.0% NAV", desc: "Giới hạn giải ngân tối đa 5% tổng tài sản", tone: "border-mineral/30 bg-mineral/5 text-mineral" },
  MAX_POSITION_WEIGHT_CAP_15PCT: { label: "Trần tỷ trọng 15.0% NAV", desc: "Mức trần chuẩn danh mục", tone: "border-teal/30 bg-teal/5 text-teal" },
  TIGHT_TRAILING_STOP_LOSS: { label: "Siết chặt Trailing Stop-Loss", desc: "Nâng mức cắt lỗ linh hoạt bảo toàn vốn", tone: "border-loss/30 bg-loss/5 text-loss" },
  STANDARD_QUARTER_KELLY_SIZING: { label: "Định cỡ Quarter-Kelly chuẩn", desc: "Tối ưu hóa lợi nhuận theo rủi ro tiêu chuẩn", tone: "border-teal/30 bg-teal/5 text-teal" },
  ROUTINE_MONITORING: { label: "Giám sát tự hành định kỳ", desc: "Theo dõi rủi ro và biến động qua hệ thống", tone: "border-line bg-soft text-secondary" },
  RETURN_TO_RESEARCH_QUEUE: { label: "Trả về hàng đợi nghiên cứu", desc: "Yêu cầu bổ sung dữ liệu luận điểm", tone: "border-warning/30 bg-warning/5 text-warning" },
  SUSPEND_PURCHASE_UNTIL_AUDITED: { label: "Đình chỉ mua đến khi kiểm toán", desc: "Tạm dừng giải ngân cho đến khi kiểm toán hoàn tất", tone: "border-loss/30 bg-loss/5 text-loss" },
}

export function logSummary(agent: string, row: RecordData): string {
  const out = logOutput(agent, row)
  for (const key of ["debate_summary", "executive_rationale", "rationale", "debate_challenge_text", "debate_synthesis", "thesis_statement", "reason", "message", "final_resolution", "verdict", "status", "action_type"])
    if (typeof out[key] === "string" && out[key]) return cleanText(out[key])
  if (agent === "position_monitoring" && numeric(out.pnl_pct) !== null) return `Lãi/lỗ của lượt giám sát: ${out.pnl_pct}% · có thể là trung bình nhiều vị thế`
  return "Bản ghi dữ liệu · mở để xem kết quả và các bước tính toán."
}
