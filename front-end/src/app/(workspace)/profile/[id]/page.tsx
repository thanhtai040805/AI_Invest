"use client"

import { useState, useMemo } from "react"
import { useParams } from "next/navigation"
import { Page } from "@/components/Shell"
import { Link } from "@/lib/router"
import { Button, Panel, Pill, PercentChange, fmt } from "@/components/ui"
import { communityApi, marketApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import type { Stock } from "@/types"

function BrokerProfile({ id }: { id: string }) {
  const expertsRes = useResource(() => communityApi.experts().catch(() => []), [])
  const snapshotRes = useResource(() => marketApi.snapshot().catch(() => ({ items: [] })), [])

  const experts = (expertsRes.data as any[]) || []
  const currentExpert = experts.find((x) => String(x.id) === id) || experts[0] || {
    id: "exp-1",
    displayName: "Nguyễn Tuấn Anh",
    winRate: 68.5,
    reactionCount: 142,
    rank: "Elite",
  }

  const modelStocks = useMemo(() => {
    const raw = snapshotRes.data as any
    const items = raw?.stocks || raw?.items || []
    return items.slice(0, 5)
  }, [snapshotRes.data])

  const [tab, setTab] = useState("Danh mục mẫu")

  const name = currentExpert.displayName || "Chuyên gia AIInvest"
  const initials = name.split(" ").slice(-2).map((w: string) => w[0]).join("")
  const winRate = Number(currentExpert.winRate || 65)

  return (
    <Page title={name} sub={`Chiến lược gia định lượng · Chuyên gia phân tích`} actions={<><Button variant="secondary">Nhắn tin</Button><Button variant="secondary">Đặt lịch hẹn</Button><Button variant="primary">Theo dõi</Button></>}>
      <Panel className="mb-4">
        <div className="flex flex-wrap items-center gap-6">
          <div className="w-16 h-16 rounded-full bg-teal/12 text-teal grid place-items-center text-[20px] font-semibold border border-teal/25">{initials}</div>
          <div>
            <div className="flex items-center gap-2"><span className="text-[18px] font-semibold text-ink">{name}</span><Pill tone="gold">{currentExpert.rank || "Elite"}</Pill><span className="text-mineral" title="Chứng chỉ đã xác thực">✓</span></div>
            <div className="text-[13px] text-muted mt-0.5">AIInvest Community · Licensed advisor · Thành viên phân tích</div>
          </div>
          <div className="ml-auto grid grid-cols-4 gap-6">
            {[["Lợi nhuận 1 năm", "+28.4%", "text-gain"], ["Tỷ lệ thắng", `${winRate.toFixed(1)}%`, "text-ink"], ["Chỉ số Sharpe", "1.82", "text-ink"], ["Lượt tương tác", String(currentExpert.reactionCount || 120), "text-ink"]].map(([l, v, c]) => (
              <div key={l}><div className="text-[11px] uppercase text-muted">{l}</div><div className={`tnum font-mono text-[20px] mt-0.5 ${c}`}>{v}</div></div>
            ))}
          </div>
        </div>
      </Panel>

      <Panel flush>
        <div className="px-5 pt-4">
          <div className="flex gap-1 border-b border-line">
            {["Danh mục mẫu", "Luận điểm đã đăng", "Góc nhìn thị trường", "Chứng chỉ & Hồ sơ", "Hoạt động"].map((t) => (
              <button key={t} onClick={() => setTab(t)} className={`relative px-3 h-9 text-[13px] font-medium ${tab === t ? "text-ink" : "text-muted hover:text-secondary"}`}>{t}{tab === t && <span className="absolute left-2 right-2 -bottom-px h-[2px] bg-ink rounded-full" />}</button>
            ))}
          </div>
        </div>
        <div className="p-5">
          {(tab === "Danh mục mẫu" || tab === "Model Portfolio") && (
            <table className="w-full text-[13px]">
              <thead><tr className="text-[11px] uppercase text-muted border-b border-line"><th className="text-left font-medium py-2">Mã CP</th><th className="text-right font-medium">Tỷ trọng</th><th className="text-right font-medium">Thị giá</th><th className="text-right font-medium">Biến động</th></tr></thead>
              <tbody className="divide-y divide-line">
                {modelStocks.map((s, i) => (
                  <tr key={s.symbol}><td className="py-2.5"><Link to={`/stock/${s.symbol}`} className="font-mono text-ink hover:underline">{s.symbol}</Link></td><td className="text-right tnum font-mono">{[25, 20, 20, 20, 15][i] ?? 20}%</td><td className="text-right tnum font-mono text-secondary">{fmt(s.price || s.ref)}</td><td className="text-right"><PercentChange value={s.changePct} arrow={false} /></td></tr>
                ))}
              </tbody>
            </table>
          )}
          {(tab === "Luận điểm đã đăng" || tab === "Published Theses") && <div className="space-y-3">{["HPG — Tích lũy biên lợi nhuận thép Dung Quất", "MBB — Tăng trưởng tín dụng bán lẻ", "FPT — Chuyển đổi số & AI toàn cầu"].map((t) => <div key={t} className="border border-line rounded-[8px] p-3 hover:border-ink/25 transition-colors"><div className="text-[14px] font-medium text-ink">{t}</div><div className="text-[12px] text-muted mt-0.5">Đang hoạt động · Cập nhật từ AIInvest</div></div>)}</div>}
          {(tab === "Góc nhìn thị trường" || tab === "Market Views") && <p className="text-[14px] text-secondary leading-relaxed max-w-2xl font-serif">Xu hướng dòng tiền tập trung vào các mã cơ bản có vốn hóa lớn và hỗ trợ từ dòng tiền khối ngoại. Khuyến nghị duy trì tỷ trọng danh mục cân bằng, tuân thủ kỷ luật rủi ro của hệ thống.</p>}
          {(tab === "Chứng chỉ & Hồ sơ" || tab === "Credentials") && <div className="space-y-2 text-[13px]">{["Chứng chỉ hành nghề quản lý danh mục — UBCKNN", "CFA Charterholder", "Chuyên gia phân tích dữ liệu thị trường"].map((c) => <div key={c} className="flex items-center gap-2 text-secondary"><span className="text-mineral">✓</span>{c}</div>)}</div>}
          {(tab === "Hoạt động" || tab === "Activity") && <div className="space-y-2 text-[13px] text-secondary">{["Xuất bản bài phân tích dòng tiền", "Cập nhật tỷ trọng danh mục mẫu", "Đánh giá tín hiệu AI Engine"].map((a, i) => <div key={i} className="flex gap-3"><span className="text-muted tnum text-[11px] w-12">{i + 1}d</span>{a}</div>)}</div>}
        </div>
      </Panel>
    </Page>
  )
}

export default function BrokerProfilePage() {
  const { id } = useParams<{ id: string }>()
  return <BrokerProfile id={id} />
}
