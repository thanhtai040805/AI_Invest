"use client"

import { Page } from "@/components/Shell"
import {
  Button,
  Panel,
  PanelHead,
} from "@/components/ui"

export default function Help() {
  return (
    <Page
      title="Trợ giúp & Phương pháp luận"
      sub="Tài liệu hướng dẫn, thuật ngữ và cơ chế định lượng của AIInvest."
    >
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {[
          ["Phương pháp luận nghiên cứu", "Cách thức xây dựng luận điểm từ các dẫn chứng xác thực."],
          [
            "Phương pháp quản trị rủi ro",
            "Cơ chế tính toán tổn thất kỳ vọng (ES) và quy chế cắt giảm rủi ro Drawdown.",
          ],
          [
            "Phương pháp luận AI",
            "Cách thức các tín hiệu chuyển từ quan sát định lượng sang luận điểm đầu tư.",
          ],
          ["Thuật ngữ thị trường", "Giá trần, giá sàn, khối ngoại, thanh khoản và các chỉ số chuyên sâu."],
          ["Tài liệu hướng dẫn", "Bắt đầu sử dụng và làm chủ không gian làm việc AIInvest."],
          ["Liên hệ hỗ trợ", "Kết nối trực tiếp với đội ngũ phát triển AIInvest."],
        ].map(([t, d]) => (
          <Panel key={t}>
            <div className="text-[15px] font-semibold text-ink">{t}</div>
            <p className="text-[13px] text-muted mt-1.5 leading-relaxed">{d}</p>
          </Panel>
        ))}
      </div>
      <Panel className="mt-4">
        <PanelHead
          title="Gửi tin nhắn cho quản trị viên"
          sub="Gửi câu hỏi về tài khoản, nguồn dữ liệu hoặc quy trình giao dịch."
        />
        <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-3">
          <input
            aria-label="Tin nhắn gửi quản trị viên"
            placeholder="Mô tả nội dung bạn cần hỗ trợ…"
            className="h-10 rounded-[7px] border border-line bg-paper px-3 text-[13px] text-ink outline-none placeholder:text-muted focus:border-mineral"
          />
          <Button variant="primary">Gửi tin nhắn</Button>
        </div>
      </Panel>
    </Page>
  )
}
