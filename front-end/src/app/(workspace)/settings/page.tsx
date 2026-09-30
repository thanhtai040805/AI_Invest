"use client"

import { useState } from "react"
import { Page } from "@/components/Shell"
import { Panel, PanelHead } from "@/components/ui"

const sections = [
  "Hồ sơ cá nhân",
  "Bảo mật",
  "Giao dịch",
  "Quản trị rủi ro",
  "Tùy chọn AI",
  "Thông báo",
  "Giao diện",
  "Ngôn ngữ & Vùng",
  "Quyền riêng tư",
  "Công ty chứng khoán",
  "Gói hội viên",
  "Nâng cao",
]

const currentFormats = [
  ["Ngôn ngữ", "Tiếng Việt"],
  ["Định dạng số và tiền", "vi-VN · VND"],
  ["Ngày tháng", "DD/MM/YYYY"],
  ["Múi giờ", "Asia/Ho_Chi_Minh (UTC+7)"],
]

export default function Settings() {
  const [active, setActive] = useState("Ngôn ngữ & Vùng")

  return (
    <Page title="Cài đặt" sub="Thông tin cài đặt hiện có trong không gian làm việc.">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[220px_1fr]">
        <Panel className="h-fit">
          <nav aria-label="Các mục cài đặt" className="space-y-0.5">
            {sections.map((section) => (
              <button
                key={section}
                type="button"
                aria-current={active === section ? "page" : undefined}
                onClick={() => setActive(section)}
                className={"h-8 w-full rounded-[6px] px-2.5 text-left text-[13px] " + (active === section ? "bg-soft font-medium text-ink" : "text-secondary hover:bg-soft/60")}
              >
                {section}
              </button>
            ))}
          </nav>
        </Panel>

        <Panel>
          <PanelHead title={active} />
          {active === "Ngôn ngữ & Vùng" ? (
            <>
              <dl className="divide-y divide-line">
                {currentFormats.map(([label, value]) => (
                  <div key={label} className="flex flex-wrap items-center justify-between gap-2 py-3 first:pt-0">
                    <dt className="text-[13px] text-secondary">{label}</dt>
                    <dd className="text-[13px] font-medium text-ink">{value}</dd>
                  </div>
                ))}
              </dl>
              <p className="mt-3 border-t border-line pt-3 text-[12px] leading-relaxed text-muted">
                Các giá trị trên đang được ứng dụng sử dụng; thay đổi tùy chọn chưa được hỗ trợ.
              </p>
            </>
          ) : (
            <p className="rounded-lg border border-dashed border-line px-4 py-6 text-[13px] leading-relaxed text-secondary">
              Mục này chưa có cài đặt được kết nối với hệ thống.
            </p>
          )}
        </Panel>
      </div>
    </Page>
  )
}
