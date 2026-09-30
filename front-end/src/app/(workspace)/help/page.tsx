"use client"

import { Page } from "@/components/Shell"
import { Link } from "@/lib/router"
import { Panel, PanelHead } from "@/components/ui"

const guides = [
  {
    title: "Luận điểm đầu tư",
    description: "Xem luận điểm và lịch sử phân tích được lưu từ AI Engine.",
    href: "/research",
    action: "Mở nghiên cứu",
  },
  {
    title: "Quản trị rủi ro",
    description: "Đối chiếu trạng thái rủi ro và các quyết định có trong log hệ thống.",
    href: "/agent",
    action: "Mở War Room",
  },
  {
    title: "Mô hình AI",
    description: "Xem báo cáo và dữ liệu của kênh ML tự hành.",
    href: "/ml-fund",
    action: "Mở báo cáo ML",
  },
  {
    title: "Thuật ngữ thị trường",
    description: "Đối chiếu giá, biến động và thanh khoản từ bảng dữ liệu thị trường.",
    href: "/markets",
    action: "Mở bảng giá",
  },
  {
    title: "Bắt đầu sử dụng",
    description: "Mở trang tổng quan để xem trạng thái dữ liệu và các khu vực làm việc.",
    href: "/dashboard",
    action: "Mở tổng quan",
  },
]

export default function Help() {
  return (
    <Page
      title="Trợ giúp & Phương pháp luận"
      sub="Mở trực tiếp các khu vực có dữ liệu và thao tác đang được hỗ trợ."
    >
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {guides.map((guide) => (
          <Link key={guide.href} to={guide.href} className="group">
            <Panel className="h-full transition-colors group-hover:border-ink/30">
              <div className="text-[15px] font-semibold text-ink">{guide.title}</div>
              <p className="mt-1.5 text-[13px] leading-relaxed text-muted">{guide.description}</p>
              <span className="mt-4 inline-block text-[12px] font-medium text-mineral">{guide.action} →</span>
            </Panel>
          </Link>
        ))}
      </div>

      <Panel className="mt-4">
        <PanelHead title="Liên hệ hỗ trợ" />
        <p className="text-[13px] leading-relaxed text-secondary">
          Kênh gửi yêu cầu hỗ trợ chưa được kết nối trong ứng dụng.
        </p>
      </Panel>
    </Page>
  )
}
