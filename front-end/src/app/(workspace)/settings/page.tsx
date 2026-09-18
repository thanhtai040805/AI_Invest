"use client"

import { useState, useEffect } from "react"
import { Page } from "@/components/Shell"
import {
  Panel,
  PanelHead,
} from "@/components/ui"

export default function Settings() {
  const [active, setActive] = useState("Ngôn ngữ & Vùng")
  const [lang, setLang] = useState("vi")

  useEffect(() => {
    try {
      const saved = localStorage.getItem("aiinvest_lang")
      if (saved) setLang(saved)
    } catch {}
  }, [])

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
  return (
    <Page title="Cài đặt" sub="Cấu hình không gian làm việc của bạn.">
      <div className="grid grid-cols-1 lg:grid-cols-[220px_1fr] gap-4">
        <Panel className="h-fit">
          <div className="space-y-0.5">
            {sections.map((s) => (
              <button
                key={s}
                onClick={() => setActive(s)}
                className={`w-full text-left h-8 px-2.5 rounded-[6px] text-[13px] ${active === s ? "bg-soft text-ink font-medium" : "text-secondary hover:bg-soft/60"}`}
              >
                {s}
              </button>
            ))}
          </div>
        </Panel>
        <Panel>
          <PanelHead title={active} />
          {active === "Ngôn ngữ & Vùng" ? (
            <div className="space-y-5">
              {[
                ["Ngôn ngữ hiển thị", ["Tiếng Việt (Mặc định)", "English"]],
                ["Định dạng tiền tệ", ["Việt Nam đồng (₫)", "Quốc tế (VND)"]],
                ["Định dạng ngày tháng", ["DD/MM/YYYY", "YYYY-MM-DD"]],
                ["Múi giờ giao dịch", ["Hà Nội (UTC+7)", "UTC"]],
              ].map(([l, opts]) => (
                <div key={l as string}>
                  <div className="text-[13px] text-ink mb-2">{l as string}</div>
                  <div className="flex gap-1 bg-soft rounded-[8px] p-1 w-fit">
                    {(opts as string[]).map((o, i) => (
                      <button
                        key={o}
                        onClick={() => {
                          if (l === "Ngôn ngữ hiển thị") {
                            const newLang = i === 0 ? "vi" : "en"
                            setLang(newLang)
                            try { localStorage.setItem("aiinvest_lang", newLang) } catch {}
                          }
                        }}
                        className={`px-3 h-7 rounded-[6px] text-[12px] ${
                          (l === "Ngôn ngữ hiển thị" ? (lang === "vi" ? i === 0 : i === 1) : i === 0)
                            ? "bg-surface text-ink shadow-sm font-medium"
                            : "text-muted hover:text-ink"
                        }`}
                      >
                        {o}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              <p className="text-[12px] text-muted pt-2 border-t border-line">
                Cài đặt ngôn ngữ và định dạng số được lưu vào không gian làm việc của bạn.
              </p>
            </div>
          ) : active === "Tùy chọn AI" ? (
            <div className="space-y-5">
              {[
                ["Tần suất tín hiệu", ["Thấp", "Cân bằng", "Cao"]],
                ["Độ sâu giải thích", ["Ngắn gọn", "Tiêu chuẩn", "Chi tiết"]],
                [
                  "Độ nhạy rủi ro",
                  ["Thận trọng", "Vừa phải", "Tích cực"],
                ],
                ["Độ phức tạp phân tích", ["Cơ bản", "Tiêu chuẩn", "Chuyên sâu"]],
              ].map(([l, opts]) => (
                <div key={l as string}>
                  <div className="text-[13px] text-ink mb-2">{l as string}</div>
                  <div className="flex gap-1 bg-soft rounded-[8px] p-1 w-fit">
                    {(opts as string[]).map((o, i) => (
                      <button
                        key={o}
                        className={`px-3 h-7 rounded-[6px] text-[12px] ${i === 1 ? "bg-surface text-ink shadow-sm" : "text-muted"}`}
                      >
                        {o}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              <p className="text-[12px] text-muted pt-2 border-t border-line">
                Cài đặt AI giúp điều chỉnh cách hiển thị dữ liệu phân tích. Các thông số này không
                đảm bảo hiệu quả đầu tư.
              </p>
            </div>
          ) : (
            <p className="text-[13.5px] text-secondary">
              Quản lý cài đặt {active.toLowerCase()} tại đây.
            </p>
          )}
        </Panel>
      </div>
    </Page>
  )
}
