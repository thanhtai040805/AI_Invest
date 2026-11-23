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
  buildPrompt: (ticker?: string) => string
}> = [
  {
    id: "gpm-pricing",
    title: "Biên lợi nhuận gộp & định giá sản phẩm",
    tag: "BCTN_CORE · BCTC_AUDITED · Calc",
    buildPrompt: (t) => {
      const target = t?.trim() ? t.trim().toUpperCase() : "doanh nghiệp mục tiêu"
      return `Phân tích động lực biên lợi nhuận gộp (GPM) và năng lực định giá sản phẩm cốt lõi của ${target} theo thuyết minh doanh thu và giá vốn (BCTC_AUDITED). Tách bạch mảng kinh doanh lõi với mảng biên mỏng bằng phép tính Python.`
    },
  },
  {
    id: "debt-liquidity",
    title: "Nợ vay & thanh khoản ngắn hạn",
    tag: "BCTC_AUDITED · QUARTER_NOTES · Nợ ẩn",
    buildPrompt: (t) => {
      const target = t?.trim() ? t.trim().toUpperCase() : "doanh nghiệp mục tiêu"
      return `Kiểm tra cấu trúc nợ vay, chi phí lãi vay, tài sản thế chấp và cam kết thuê hoạt động ngoại bảng của ${target} trong thuyết minh BCTC_AUDITED và QUARTER_NOTES. Thanh khoản ngắn hạn có rủi ro ẩn nào không?`
    },
  },
  {
    id: "plan-vs-actual",
    title: "Kế hoạch vs Thực hiện & Giải trình biến động",
    tag: "AGM_TARGETS · BCTN_PR · Đối chất",
    buildPrompt: (t) => {
      const target = t?.trim() ? t.trim().toUpperCase() : "doanh nghiệp mục tiêu"
      return `Đối chiếu Kế hoạch kinh doanh và CAPEX năm trước (AGM_TARGETS) với kết quả Thực hiện thực tế (BCTC_AUDITED) của ${target}. Báo cáo thường niên (BCTN_PR / BCTN_CORE) có điểm nào nói lạc quan lệch với số liệu kiểm toán không?`
    },
  },
  {
    id: "macro-value-chain",
    title: "Độ nhạy Vĩ mô & Rủi ro Chuỗi giá trị",
    tag: "KG 3 Tầng · BCTN_CORE",
    buildPrompt: (t) => {
      const target = t?.trim() ? t.trim().toUpperCase() : "doanh nghiệp mục tiêu"
      return `Đánh giá độ nhạy của ${target} trước biến động tỷ giá USD/VND, lãi suất và giá hàng hóa đầu vào (ưu tiên bằng chứng CONFIRMED_BOTH), cùng rủi ro tập trung nhà cung cấp/khách hàng trong chuỗi giá trị.`
    },
  },
  {
    id: "true-peer-compare",
    title: "So sánh True-Peer cùng ngành thực chất",
    tag: "TRUE_PEER · Đa mã",
    buildPrompt: (t) => {
      const target = t?.trim() ? t.trim().toUpperCase() : "doanh nghiệp mục tiêu"
      return `So sánh năng lực cạnh tranh (MOAT) và biên sinh lời của ${target} với cụm đối thủ thực chất (True-Peer) trong đồ thị 3 tầng: ${target} đang hưởng gió thuận chu kỳ chung hay có lợi thế cấu trúc vượt trội?`
    },
  },
  {
    id: "legal-related-party",
    title: "Rủi ro pháp lý / giao dịch bên liên quan",
    tag: "BCTC_AUDITED · AGM_TARGETS",
    buildPrompt: (t) => {
      const target = t?.trim() ? t.trim().toUpperCase() : "doanh nghiệp mục tiêu"
      return `Rà soát ý kiến kiểm toán độc lập, giao dịch bên liên quan, đầu tư tài chính/dự phòng (BCTC_AUDITED) và sự kiện sau khóa sổ hoặc pha loãng ESOP/tăng vốn (AGM_TARGETS) của ${target}. Nêu rõ các điểm chưa có dữ liệu xác minh.`
    },
  },
]

export const GENERAL_STARTER_PROMPTS: ReadonlyArray<{
  id: string
  title: string
  tag: string
  prompt: string
}> = [
  {
    id: "general-multi-hop-revenue",
    title: "Hỏi sâu đa bước một doanh nghiệp",
    tag: "Multi-Hop · BCTC + Thuyết minh",
    prompt: "Tóm tắt cơ cấu doanh thu, động lực tăng trưởng chính năm gần nhất của @FPT và kiểm tra thuyết minh chi tiết các mảng đóng góp lớn nhất.",
  },
  {
    id: "general-cross-ticker",
    title: "So sánh trực tiếp nhiều mã cổ phiếu",
    tag: "Đa mã · Bảng 2D",
    prompt: "So sánh cơ cấu tài sản, nợ vay và biên lợi nhuận gộp giữa @FPT và @MWG dựa trên báo cáo tài chính kiểm toán gần nhất.",
  },
  {
    id: "general-universe-risk",
    title: "Quét rủi ro vĩ mô toàn danh mục đã nạp",
    tag: "Universe · KG 3 Tầng",
    prompt: "Trong các doanh nghiệp đã nạp báo cáo, doanh nghiệp nào nhạy cảm nhất với rủi ro tỷ giá USD/VND và chi phí tài chính?",
  },
  {
    id: "general-plan-check",
    title: "Đối chiếu kế hoạch ĐHĐCĐ & thực tế",
    tag: "AGM_TARGETS · BCTC_AUDITED",
    prompt: "Kế hoạch doanh thu, lợi nhuận và đầu tư đội tàu của @PVT theo Nghị quyết ĐHĐCĐ và BCTN có khác biệt gì so với số thực hiện kiểm toán?",
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
