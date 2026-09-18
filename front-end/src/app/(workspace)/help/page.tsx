"use client"

import { Page } from "@/components/Shell"
import {
  Button,
  Panel,
  PanelHead,
  SectionEyebrow,
} from "@/components/ui"

export function Notifications() {
  const groups = {
    "Thị trường": ["VN-Index đóng cửa +0.72%"],
    "Danh mục": ["Vị thế HPG tăng trưởng 16.1%"],
    "Tác tử AI": ["Tín hiệu tích lũy mới trên mã MBB"],
    "Cộng đồng": ["Nguyễn Minh Anh đã công bố luận điểm mới"],
    "Môi giới": ["Chuyên viên bạn theo dõi vừa đăng tín hiệu"],
  }
  return (
    <Page
      title="Thông báo"
      sub="Phân nhóm theo nguồn dữ liệu."
      actions={<Button variant="ghost">Đánh dấu tất cả đã đọc</Button>}
    >
      <div className="space-y-4">
        {Object.entries(groups).map(([g, items]) => (
          <Panel key={g}>
            <SectionEyebrow>{g}</SectionEyebrow>
            {items.map((n, i) => (
              <div key={i} className="flex items-center gap-3 py-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-mineral" />
                <span className="text-[13px] text-secondary">{n}</span>
                <span className="ml-auto text-[11px] text-muted">2h</span>
              </div>
            ))}
          </Panel>
        ))}
      </div>
    </Page>
  )
}

export function Activity() {
  const events = [
    "Xem phân tích AI về HPG",
    "Lưu luận điểm: Tăng trưởng tín dụng MBB",
    "Khớp lệnh mua · FPT",
    "Theo dõi Nguyễn Minh Anh",
    "Kích hoạt cảnh báo: Khối lượng HPG",
    "Xem nghiên cứu: Ngành Ngân hàng",
  ]
  return (
    <Page title="Nhật ký hoạt động" sub="Dòng thời gian hoạt động của bạn trên AIInvest.">
      <Panel>
        <div className="space-y-0">
          {events.map((e, i) => (
            <div
              key={i}
              className="flex gap-4 py-3 border-b border-line last:border-0"
            >
              <span className="tnum text-[11px] text-muted w-10">{i + 1}h</span>
              <span className="w-1.5 h-1.5 rounded-full bg-mineral mt-1.5" />
              <span className="text-[13px] text-secondary">{e}</span>
            </div>
          ))}
        </div>
      </Panel>
    </Page>
  )
}

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
