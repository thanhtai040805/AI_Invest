import type { AgentStep } from "@/types"

export interface Citation {
  source: string
  detail: string
  date: string
  type: string
}

export const citations: Citation[] = [
  { source: "BCTC HPG Q2", detail: "Biên lợi nhuận gộp thép hồi phục lên 13.2% từ mức đáy 9.8%", date: "Hôm nay", type: "Filing" },
  { source: "HOSE Foreign Net Disclosure", detail: "Khối ngoại mua ròng +182 tỷ VND liên tiếp 5 phiên", date: "Hôm nay", type: "Exchange" },
  { source: "Tổng cục Thống kê GSO", detail: "Chỉ số PMI xây dựng & công nghiệp tiếp tục mở rộng", date: "Tháng này", type: "Macro" },
  { source: "Nghị quyết ĐHCĐ HPG", detail: "Tiến độ lò cao Dung Quất 2 bám sát kế hoạch vận hành thử nghiệm", date: "Gần đây", type: "Disclosure" },
]

export const pipeline: AgentStep[] = [
  {
    order: 1, id: "01", name: "Market Surveillance", vn: "Giám sát thị trường", phase: "Detection",
    does: "Quét toàn bộ sàn HOSE tìm dịch chuyển chế độ, độ rộng, dòng tiền và bất thường tape.",
    handoff: "Đẩy market_pulse sang Reinforcement Learning",
    headline: [{ label: "Regime", value: "BULL", tone: "gain" }, { label: "Breadth >MA50", value: "62%", tone: "teal" }, { label: "A/D ratio", value: "2.1", tone: "gain" }, { label: "Herding", value: "NORMAL", tone: "neutral" }],
    detail: ["Phiên khớp lệnh liên tục HOSE · bối cảnh Bình thường", "VIX-VN analog 17.4 · tỷ trọng cash mục tiêu 12%", "Số mã sàn 3 · Trần 11 · Dị thường ATC: 0", "Điểm CSAD 0.82 · không có hoảng loạn/hưng phấn cực đoan", "Không có mã bị ngắt quãng · mức cảnh báo INFO"],
  },
  {
    order: 2, id: "10", name: "Reinforcement Learning", vn: "Tự học · hiệu chuẩn", phase: "Detection",
    does: "Hiệu chuẩn lại trọng số yếu tố F1–F6 và sizing Kelly cho regime hiện tại từ P&L thực tế.",
    handoff: "Chuyển trọng số tối ưu sang Universe Discovery",
    headline: [{ label: "Rolling IC 20d", value: "0.08", tone: "teal" }, { label: "Momentum wt", value: "+4%", tone: "gain" }, { label: "CDC decay", value: "OK", tone: "gain" }],
    detail: ["IC theo factor — F3 0.11, F5 0.09, F1 0.04", "Suy giảm IC 6% · chẩn đoán: ổn định", "Ma trận Kelly: A+ 0.30 · A 0.25 · B 0.15", "Hiệu chuẩn Moat: rủi ro hallucination THẤP", "Đề xuất Governance: Sharpe 1.34 · DD 8.1%"],
  },
  {
    order: 3, id: "02", name: "Universe Discovery", vn: "Lọc universe", phase: "Detection",
    does: "Quét HOSE, loại bỏ cổ phiếu vi phạm bộ lọc cứng và phân nhóm A/B/C/Sandbox.",
    handoff: "Bàn giao danh sách ngắn hợp lệ cho Equity Research",
    headline: [{ label: "Đã quét", value: "406" }, { label: "Hợp lệ", value: "87", tone: "teal" }, { label: "Bị loại", value: "319", tone: "warning" }],
    detail: ["Phân nhóm — A 24 · B 41 · C 22 · Sandbox 8", "Loại: ADTV20 < 15 tỷ (146), Beneish FAIL (37)", "Loại: Rủi ro tài chính cao, kiểm toán có ý kiến ngoại trừ", "Trung vị M-Score −2.4 · tất cả nhóm hợp lệ ĐẠT", "Quy tắc chế độ: BULL — toàn bộ universe kích hoạt"],
  },
  {
    order: 4, id: "03", name: "Equity Research", vn: "Chấm điểm cổ phiếu", phase: "Analysis",
    does: "Chấm điểm 6 yếu tố định lượng kèm hào kinh tế thành điểm tổng hợp CSS và phân hạng A/B/C.",
    handoff: "Gửi điểm CSS + Conviction sang Investment Thesis",
    headline: [{ label: "CSS", value: "74", tone: "teal" }, { label: "Conviction", value: "A", tone: "gain" }, { label: "Moat", value: "62" }],
    detail: ["Các yếu tố — Value 58 · Quality 74 · Momentum 78", "Growth 66 · Dòng tiền 71 · Kỹ thuật 69", "Hào kinh tế: Lợi thế chi phí 84 · Quy mô 88 · Thương hiệu 62", "Trọng số áp dụng được tinh chỉnh theo regime (thiên về Momentum)", "Chất lượng dữ liệu: ĐẠT · đủ điều kiện lập luận điểm"],
  },
  {
    order: 5, id: "04", name: "Investment Thesis", vn: "Luận điểm mua", phase: "Analysis",
    does: "Xây dựng luận điểm chiều mua: tại sao mua, thời điểm vào lệnh và điều kiện vi phạm.",
    handoff: "Đệ trình luận điểm sang Counter Thesis để phản biện",
    headline: [{ label: "Mục tiêu", value: "133–138k", tone: "gain" }, { label: "Upside", value: "+9%", tone: "gain" }, { label: "Tín hiệu", value: "3", tone: "teal" }],
    detail: ["Xúc tác: Tiến độ Dung Quất 2 đúng kế hoạch", "Giá vào lệnh tham chiếu · khung thời gian 3–6 tháng", "3 tín hiệu độc lập xác nhận (dòng tiền, biên lợi nhuận, vĩ mô)", "Phân tích rủi ro xấu: xuất khẩu thép chậm lại, tỷ giá biến động", "Điều kiện vi phạm: đóng nến dưới ngưỡng cắt lỗ kỹ thuật"],
  },
  {
    order: 6, id: "05", name: "Devil's Advocate", vn: "Phản biện · counter", phase: "Analysis",
    does: "Tấn công luận điểm, tính điểm phản biện CTS và ra phán quyết độc lập.",
    handoff: "Chuyển phán quyết lên Strategy CIO để phân xử",
    headline: [{ label: "CTS", value: "41", tone: "warning" }, { label: "Phán quyết", value: "CONDITIONAL", tone: "warning" }, { label: "Lỗ hổng", value: "2" }],
    detail: ["Điểm CTS cơ sở 38 · hệ số tương tác ×1.05 · chế độ ×1.0", "Phán quyết CONDITIONAL: giảm quy mô giải ngân, siết chặt trailing stop", "Lỗ hổng: độ nhạy giá thép thế giới, chi phí vốn lưu động", "Quy tắc kiểm chứng độc lập: ĐẠT", "Không phải cú hồi kỹ thuật sau bán tháo"],
  },
  {
    order: 7, id: "12", name: "Strategy CIO", vn: "Trọng tài tối cao", phase: "Decision",
    does: "Phân xử xung đột luận điểm và ban hành chỉ thị điều hành danh mục vĩ mô.",
    handoff: "Cho phép các ý tưởng đã duyệt chuyển sang Portfolio Allocation",
    headline: [{ label: "Chế độ", value: "NORMAL", tone: "mineral" }, { label: "Tiền mặt", value: "20%", tone: "teal" }, { label: "Thiên lệch", value: "Bank/Thép OW", tone: "gain" }],
    detail: ["Nghị quyết: PROCEED_WITH_PENALTY · hệ số phạt 0.85", "Cấp độ kiểm soát T3 bình thường · trần tỷ trọng mã 15%", "Khẩu vị rủi ro: TRUNG TÍNH", "Thiên lệch ngành: Ngân hàng OW · Thép OW · Bất động sản UW", "Mã xác thực quyết định: SHA256 cryptographic proof"],
  },
  {
    order: 8, id: "07", name: "Portfolio Allocation", vn: "Chia tiền · Quarter-Kelly", phase: "Decision",
    does: "Tính toán quy mô vị thế theo mô hình Quarter-Kelly và lên phiếu lệnh dự thảo.",
    handoff: "Gửi lệnh dự thảo sang Portfolio Risk kiểm tra",
    headline: [{ label: "Lệnh", value: "BUY", tone: "gain" }, { label: "Khối lượng", value: "12,000" }, { label: "Tỷ trọng", value: "4.2%", tone: "teal" }, { label: "Giá trị", value: "₫1.5B" }],
    detail: ["Hệ số Kelly 0.25 · xác suất thắng 0.58 · tỷ lệ payoff 1.9", "Khối lượng thực thi 12,000 cp · dải chết 2%", "Số dư tiền mặt sau giải ngân: đảm bảo ngưỡng an toàn", "Phân loại thanh khoản: CAO · thời gian gom 3 phiên", "Cơ sở: Conviction A, chế độ thị trường hỗ trợ"],
  },
  {
    order: 9, id: "06", name: "Portfolio Risk", vn: "Cổng rủi ro · Sovereign Gate", phase: "Decision",
    does: "Quyền phủ quyết tối cao qua 5 lớp an toàn: PASS, REDUCE hoặc BLOCK.",
    handoff: "Phát hành khối lượng được phê duyệt cho Trade Execution",
    headline: [{ label: "Trạng thái", value: "REDUCE", tone: "warning" }, { label: "Đã duyệt", value: "10,800" }, { label: "Hạn mức", value: "GREEN", tone: "gain" }],
    detail: ["Luật cứng: tỷ trọng đơn lẻ 4.2% ✓ · ngành 18% ✓", "T+2.5 vốn kẹt trong giới hạn NAV an toàn", "Bất thường lệnh tape: không phát hiện", "Tail Expected Shortfall 97.5% −4.8% · Chấp nhận được", "Điều chỉnh khối lượng giảm 10% để giữ bộ đệm thanh khoản"],
  },
  {
    order: 10, id: "08", name: "Trade Execution", vn: "Khớp lệnh HOSE", phase: "Action",
    does: "Chia nhỏ lệnh theo thuật toán VWAP tuân thủ quy tắc khớp lệnh của sàn.",
    handoff: "Bàn giao vị thế mở cho Position Monitoring",
    headline: [{ label: "Khớp lệnh", value: "EXECUTED", tone: "gain" }, { label: "Trượt giá", value: "18 bps", tone: "teal" }, { label: "Khớp", value: "100%", tone: "gain" }],
    detail: ["Chế độ NORMAL · giá kích hoạt theo dải tham chiếu", "Giá khớp bình quân tối ưu · chất lượng khớp TỐT", "Chia 6 lát lệnh · lô 100 · bước giá 50đ", "Thanh khoản CAO · không chạm kill-switch phiên ATC", "Số dư tiền mặt được cập nhật chính xác"],
  },
  {
    order: 11, id: "09", name: "Position Monitoring", vn: "Gác vị thế · realtime 5'", phase: "Action",
    does: "Bảo vệ vị thế mở qua 6 lớp stop-loss và cam kết xử lý vi phạm luận điểm trước 14:00.",
    handoff: "Báo cáo kết quả đóng/mở vị thế cho System Governance",
    headline: [{ label: "Sức khỏe", value: "HEALTHY", tone: "gain" }, { label: "PnL", value: "+6.2%", tone: "gain" }, { label: "Khoảng cắt", value: "−2%", tone: "warning" }],
    detail: ["Cổ phiếu khả dụng · trạng thái nắm giữ ổn định", "Hard stop −2% NAV · trailing stop vũ trang ở mức +10%", "Cấu trúc đáy gần nhất được giữ vững", "Cam kết kiểm tra luận điểm trước 14:00 phiên chiều", "Nhật ký lệnh khẩn cấp: Không có vi phạm"],
  },
  {
    order: 12, id: "11", name: "System Governance", vn: "Tòa tối cao · Audit", phase: "Action",
    does: "Cổng tiền giao dịch, chuỗi băm kiểm toán SHA-256 và chốt an toàn chống lỗi broker.",
    handoff: "Cung cấp trạng thái tuân thủ cho chu kỳ giám sát tiếp theo",
    headline: [{ label: "Kiểm toán", value: "PASS", tone: "gain" }, { label: "Chuỗi Hash", value: "VALID", tone: "teal" }, { label: "Chốt chặn", value: "OFF", tone: "gain" }],
    detail: ["Mã token kiểm toán: GOV_AUDIT_PASS", "Vi phạm quy tắc: 0 · hệ thống TUÂN THỦ 100%", "Tính toàn vẹn chuỗi SHA-256: HỢP LỆ", "Độ trễ kết nối broker: 180ms (< 1500ms ✓)", "Chu kỳ khép kín hoàn tất"],
  },
]

export interface CaseItem {
  symbol: string
  status: "New" | "Updated" | "Confirmed" | "Watching" | "Flagged"
  note: string
}

export interface RunItem {
  id: string
  label: string
  time: string
  triggered: string
  cases: CaseItem[]
  agentsRun: number
}

export const defaultRuns: RunItem[] = [
  {
    id: "run-live",
    label: "Phiên chiều (Tự hành)",
    time: "14:15",
    triggered: "Hệ thống AI Engine",
    agentsRun: 12,
    cases: [
      { symbol: "HPG", status: "Confirmed", note: "Biên lợi nhuận và dòng tiền nước ngoài duy trì tốt" },
      { symbol: "MBB", status: "Confirmed", note: "Khối ngoại mua ròng tiếp diễn ở nhóm ngân hàng" },
      { symbol: "FPT", status: "Updated", note: "Đà tăng trưởng công nghệ duy trì ở vùng đỉnh" },
      { symbol: "SSI", status: "Watching", note: "Thanh khoản mở rộng, theo dõi hấp thụ cung" },
    ],
  },
  {
    id: "run-midday",
    label: "Tổng hợp giữa phiên",
    time: "11:30",
    triggered: "Định kỳ trưa",
    agentsRun: 12,
    cases: [
      { symbol: "HPG", status: "Updated", note: "Hấp thụ tốt lực bán phiên sáng" },
      { symbol: "FPT", status: "Confirmed", note: "Sức mạnh giá tương đối RS vượt trội" },
    ],
  },
  {
    id: "run-morning",
    label: "Quét đầu phiên sáng",
    time: "09:15",
    triggered: "Biến động thanh khoản",
    agentsRun: 11,
    cases: [
      { symbol: "SSI", status: "New", note: "Đột biến khối lượng mở phiên" },
      { symbol: "HPG", status: "New", note: "Khối lượng đạt 2.1x trung bình 20 phiên" },
    ],
  },
]

export interface StockCaseDetail {
  symbol: string
  bias: string
  conviction: "Strong" | "Moderate" | "Weak" | "Conflicting"
  thesis: string
  catalysts: string[]
  targetLow: number
  targetHigh: number
  invalidation: number
  counter: string[]
  reasoning: Record<string, string>
}

export const defaultStockCases: Record<string, StockCaseDetail> = {
  HPG: {
    symbol: "HPG", bias: "Tích lũy · Vùng mua", conviction: "Moderate",
    thesis: "Nhu cầu thép xây dựng phục hồi trong nửa cuối năm, biên lợi nhuận mở rộng khỏi vùng đáy chu kỳ. Khối ngoại chuyển sang mua ròng và thanh khoản tích lũy tích cực trên mức trung bình.",
    catalysts: ["Vận hành lò cao Dung Quất 2", "Chỉ số PMI ngành xây dựng mở rộng", "Biên lợi nhuận gộp hồi phục lên 13.2%"],
    targetLow: 28000, targetHigh: 34000, invalidation: 25000,
    counter: ["Áp lực cạnh tranh thép giá rẻ nhập khẩu", "Thị trường bất động sản phục hồi chậm làm chậm tiêu thụ", "Định giá đã phản ánh một phần kỳ vọng"],
    reasoning: {
      observation: "Thanh khoản HPG mở rộng gấp 2.1 lần trung bình 20 phiên trong khi giá giữ vững vùng hỗ trợ.",
      change: "Khối ngoại chuyển sang mua ròng liên tục 5 phiên gần nhất.",
      evidence: "Biên lợi nhuận gộp quý 2 đạt 13.2%, tiến độ Dung Quất 2 đúng lịch trình.",
      interpretation: "Lực tích lũy của các tổ chức đang gia tăng khi triển vọng phục hồi ngành rõ ràng hơn.",
      risk: "Biến động giá nguyên liệu đầu vào và tốc độ giải ngân đầu tư công.",
      invalidation: "Nếu giá đóng cửa thủng vùng đáy tích lũy gần nhất kèm khối lượng lớn sẽ kích hoạt cắt lỗ.",
    },
  },
  MBB: {
    symbol: "MBB", bias: "Tích lũy · Tăng trưởng", conviction: "Strong",
    thesis: "Tăng trưởng tín dụng vượt trội mặt bằng chung ngành, chất lượng tài sản lành mạnh. Khối ngoại gia tăng tỷ trọng và định giá P/B vẫn ở mức hấp dẫn so với tỷ suất sinh lời ROE.",
    catalysts: ["Tăng trưởng tín dụng bán lẻ", "Dòng tiền mua ròng của khối ngoại", "NIM duy trì ổn định"],
    targetLow: 24000, targetHigh: 28500, invalidation: 21500,
    counter: ["Áp lực nợ xấu từ nhóm khách hàng bất động sản", "Chi phí trích lập dự phòng có thể gia tăng", "Biến động lãi suất huy động"],
    reasoning: {
      observation: "Khối lượng MBB duy trì trên mức trung bình khi dòng tiền khối ngoại tập trung mua ròng.",
      change: "Dòng tiền lớn đổ mạnh vào nhóm cổ phiếu ngân hàng tư nhân đầu ngành.",
      evidence: "Tăng trưởng tín dụng thuộc top đầu, tỷ lệ CASA tiếp tục duy trì vị thế dẫn đầu.",
      interpretation: "Dòng tiền tổ chức quay lại tích lũy đón đầu chu kỳ hồi phục kinh tế.",
      risk: "Nợ xấu tiềm ẩn và rủi ro trái phiếu doanh nghiệp.",
      invalidation: "Thủng vùng giá 21,500 sẽ vi phạm cấu trúc sóng tăng ngắn hạn.",
    },
  },
  FPT: {
    symbol: "FPT", bias: "Tăng trưởng bền vững", conviction: "Strong",
    thesis: "Doanh thu dịch vụ IT toàn cầu tiếp tục tăng trưởng 2 con số với biên lợi nhuận mở rộng. Sức mạnh giá nằm trong top đầu thị trường và chất lượng lợi nhuận cao.",
    catalysts: ["Hợp đồng chuyển đổi số và AI toàn cầu", "Mở rộng thị trường Nhật Bản và APAC", "Tăng trưởng doanh thu khối công nghệ"],
    targetLow: 130000, targetHigh: 155000, invalidation: 118000,
    counter: ["Chi tiêu CNTT toàn cầu có dấu hiệu thận trọng", "Rủi ro biến động tỷ giá JPY/USD", "Định giá P/E ở vùng cao lịch sử"],
    reasoning: {
      observation: "FPT giữ vững sức mạnh tương đối trong các nhịp điều chỉnh của thị trường chung.",
      change: "Chỉ số động lượng duy trì vùng trên 70, xu hướng tăng giá tiếp diễn.",
      evidence: "Lượng hợp đồng ký mới tăng trưởng tốt, biên EBITDA khối viễn thông và công nghệ ổn định.",
      interpretation: "Luận điểm tăng trưởng dài hạn vững chắc, dòng tiền dài hạn ủng hộ.",
      risk: "Suy thoái kinh tế thế giới làm chậm quyết định đầu tư công nghệ của khách hàng lớn.",
      invalidation: "Đóng nến gãy đường xu hướng trung hạn MA50 với thanh khoản cao.",
    },
  },
}

export function agentOutput(id: string, ctx: { runId: string; symbol: string }) {
  void ctx
  const step = pipeline.find((p) => p.id === id)
  if (!step) {
    return {
      headline: [{ label: "Status", value: "ACTIVE", tone: "teal" as const }],
      detail: ["Agent hoạt động bình thường trong quy trình AI War Room."],
    }
  }
  return {
    headline: step.headline,
    detail: step.detail,
  }
}
