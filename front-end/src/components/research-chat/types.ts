import type { AuditBadgeToken } from "./markdownParser"
import type { SseStageName, SseStageStatus } from "@/lib/api/researchChat"

export type InspectorTabId = "quotes" | "tables" | "graph" | "trace" | "eval"

export interface InspectorFocus {
  tab: InspectorTabId
  citationId?: string
  citationIndex?: number
  slot?: string
  page?: number
  tableIndex?: number
  tableId?: string
  calcIndex?: number
  calcId?: string
  kgTier?: string
  kgIndex?: number
  nonce: number
}

export interface StageProgressItem {
  stage: SseStageName
  label: string
  status: SseStageStatus
  detail?: Record<string, unknown>
}

export const SSE_STAGE_ORDER: ReadonlyArray<{ stage: SseStageName; label: string }> = [
  { stage: "routing", label: "Định tuyến" },
  { stage: "retrieval", label: "Truy xuất PIT" },
  { stage: "graph_hop", label: "Nhảy đồ thị" },
  { stage: "table_hydration", label: "Nạp bảng 2D" },
  { stage: "calculator", label: "Tính Python" },
  { stage: "synthesis", label: "Tổng hợp" },
  { stage: "verification", label: "Kiểm chứng hash" },
]

export const PILOT_TICKERS = [
  "VCB",
  "CTG",
  "HPG",
  "FPT",
  "MWG",
  "DCM",
  "PVT",
  "VCI",
  "NT2",
  "GAS",
  "SSI",
  "VHM",
  "DGC",
] as const

export const MOAT_QUICK_PROMPTS: ReadonlyArray<{
  id: string
  title: string
  tag: string
  buildPrompt: (ticker: string) => string
}> = [
  {
    id: "gpm-pricing",
    title: "Biên lợi nhuận gộp & định giá sản phẩm",
    tag: "S01 · S07 · Calc",
    buildPrompt: (t) =>
      `Phân tích động lực biên lợi nhuận gộp (GPM) và năng lực định giá sản phẩm cốt lõi của ${t} theo thuyết minh doanh thu/giá vốn (S07). Tách bạch mảng kinh doanh lõi với mảng thương mại biên mỏng bằng phép tính Python.`,
  },
  {
    id: "debt-liquidity",
    title: "Nợ vay & thanh khoản ngắn hạn",
    tag: "S10 · S08 · Nợ ẩn",
    buildPrompt: (t) =>
      `Kiểm tra cấu trúc nợ vay, chi phí lãi vay, tài sản thế chấp và cam kết thuê hoạt động ngoại bảng của ${t} trong thuyết minh (S08, S10). Thanh khoản ngắn hạn có rủi ro ẩn nào không?`,
  },
  {
    id: "plan-vs-actual",
    title: "Kế hoạch vs Thực hiện & Giải trình biến động",
    tag: "S03 · S12 · Đối chất",
    buildPrompt: (t) =>
      `Đối chiếu Kế hoạch kinh doanh và CAPEX năm trước với kết quả Thực hiện thực tế của ${t} (S03, S12). Báo cáo thường niên có điểm nào nói lạc quan lệch với số liệu kiểm toán không?`,
  },
  {
    id: "macro-value-chain",
    title: "Độ nhạy Vĩ mô & Rủi ro Chuỗi giá trị",
    tag: "KG 3 Tầng · S10",
    buildPrompt: (t) =>
      `Đánh giá độ nhạy của ${t} trước biến động tỷ giá USD/VND, lãi suất và giá hàng hóa đầu vào (ưu tiên bằng chứng CONFIRMED_BOTH), cùng rủi ro tập trung nhà cung cấp/khách hàng trong chuỗi giá trị.`,
  },
  {
    id: "true-peer-compare",
    title: "So sánh True-Peer cùng ngành thực chất",
    tag: "TRUE_PEER · Đa mã",
    buildPrompt: (t) =>
      `So sánh năng lực cạnh tranh (MOAT) và biên sinh lời của ${t} với cụm đối thủ thực chất (True-Peer) trong đồ thị 3 tầng: ${t} đang hưởng gió thuận chu kỳ chung hay có lợi thế cấu trúc vượt trội?`,
  },
  {
    id: "legal-related-party",
    title: "Rủi ro pháp lý / giao dịch bên liên quan",
    tag: "S05 · S09 · S11",
    buildPrompt: (t) =>
      `Rà soát ý kiến kiểm toán độc lập (S05), giao dịch bên liên quan, đầu tư tài chính/dự phòng (S09) và sự kiện sau khóa sổ hoặc pha loãng ESOP/tăng vốn (S11) của ${t}. Nêu rõ các điểm chưa có dữ liệu xác minh.`,
  },
]

export function badgeToInspectorFocus(badge: AuditBadgeToken, nonce: number): InspectorFocus | null {
  if (badge.kind === "citation") {
    return {
      tab: "quotes",
      citationId: badge.refId,
      citationIndex: badge.index,
      slot: badge.slot,
      page: badge.page,
      nonce,
    }
  }
  if (badge.kind === "table") {
    return {
      tab: "tables",
      tableIndex: badge.index,
      nonce,
    }
  }
  if (badge.kind === "calculator") {
    return {
      tab: "tables",
      calcIndex: badge.index,
      calcId: badge.refId,
      nonce,
    }
  }
  if (badge.kind === "kg") {
    return {
      tab: "graph",
      kgTier: badge.tier,
      kgIndex: badge.index,
      nonce,
    }
  }
  return null
}
