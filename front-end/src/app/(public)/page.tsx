"use client"

import { useState, useEffect } from "react"
import { useRouter } from "@/lib/router"
import { pipeline, citations } from "@/lib/agent-system"
import {
  Button,
  MarketLineChart,
  metricTone,
  PercentChange,
  Pill,
  Tabs,
} from "@/components/ui"

function Logo({ onClick }: { onClick?: () => void }) {
  return (
    <button onClick={onClick} className="flex items-center gap-2.5">
      <span className="grid h-8 w-8 place-items-center rounded-[8px] bg-ink font-serif text-[17px] leading-none text-paper">
        A
      </span>
      <span className="text-[17px] font-semibold tracking-tight text-ink">
        AIInvest
      </span>
    </button>
  )
}

import { marketApi, stockApi, portfolioApi } from "@/lib/api"
import type { ApiMarketIndex, ApiMarketStock, ApiNewsItem } from "@/types"

function PortfolioRiskFrame() {
  const [navText, setNavText] = useState("1.00B")
  const [returnText, setReturnText] = useState("+0.00%")
  const [tier, setTier] = useState<"NORMAL" | "CAUTION" | "PROTECTION">("NORMAL")

  useEffect(() => {
    portfolioApi.summary().then((s) => {
      if (s && s.nav) {
        setNavText((s.nav / 1e9).toFixed(2) + "B")
        const ret = s.total_return ?? 0
        setReturnText(ret >= 0 ? `+${ret.toFixed(2)}%` : `${ret.toFixed(2)}%`)
        if (s.drawdown_tier) setTier(s.drawdown_tier)
      }
    }).catch(() => {})
  }, [])

  return (
    <div className="rounded-[12px] border border-line bg-surface p-6">
      <div className="text-[10px] font-semibold tracking-[.14em] text-muted">
        KHUNG QUẢN TRỊ DANH MỤC / RỦI RO
      </div>
      <div className="mt-5 grid grid-cols-2 gap-4">
        {[
          ["NAV", navText, "text-ink"],
          ["Lợi nhuận", returnText, returnText.startsWith("-") ? "text-loss" : "text-gain"],
          ["Tổn thất kỳ vọng", "−4.8%", "text-ink"],
          ["Ngân sách rủi ro", tier === "NORMAL" ? "100%" : "62%", "text-teal"],
        ].map(([l, v, c]) => (
          <div key={l} className="border-b border-line pb-3">
            <div className="text-[11px] text-muted">{l}</div>
            <div className={`mt-1 font-mono text-[18px] font-semibold ${c}`}>
              {v}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-5 flex items-center gap-2 text-[12px] text-secondary">
        <span className={`h-2 flex-1 rounded-full ${tier === "NORMAL" ? "bg-teal" : "bg-line-strong"}`} />
        <span className={`h-2 flex-1 rounded-full ${tier === "CAUTION" ? "bg-warning" : "bg-line-strong"}`} />
        <span className={`h-2 flex-1 rounded-full ${tier === "PROTECTION" ? "bg-loss" : "bg-line-strong"}`} />
      </div>
      <div className="mt-2 flex justify-between text-[10px] text-muted">
        <span>Bình thường</span>
        <span>Thận trọng</span>
        <span>Phòng vệ</span>
      </div>
    </div>
  )
}

function MarketPulse() {
  const [indexData, setIndexData] = useState<{ value: number; change_pct: number; date: string } | null>(null)
  const [historySeries, setHistorySeries] = useState<number[]>([])
  const [liquidity, setLiquidity] = useState<string>("—")
  const [foreignFlow, setForeignFlow] = useState<string>("—")
  const [advDec, setAdvDec] = useState<string>("—")

  useEffect(() => {
    let mounted = true

    Promise.allSettled([marketApi.indices(), marketApi.snapshot()]).then(([indRes, snapRes]) => {
      if (!mounted) return

      if (indRes.status === "fulfilled" && indRes.value?.indices) {
        const indices = indRes.value.indices as ApiMarketIndex[]
        const vn = indices.find((x) => x.symbol === "VNINDEX" || x.symbol === "VN-INDEX") || indices[0]
        if (vn) {
          setIndexData({
            value: Number(vn.value) || 1830.44,
            change_pct: Number(vn.change_pct) || 0,
            date: vn.date ? new Date(vn.date).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }).toUpperCase() : "LIVE",
          })
        }
        if (indRes.value.history?.VNINDEX && Array.isArray(indRes.value.history.VNINDEX) && indRes.value.history.VNINDEX.length > 0) {
          setHistorySeries(indRes.value.history.VNINDEX)
        }
      }

      if (snapRes.status === "fulfilled" && snapRes.value?.stocks) {
        const stocks = snapRes.value.stocks as ApiMarketStock[]
        const adv = stocks.filter((s) => (s.change_pct ?? 0) > 0).length
        const dec = stocks.filter((s) => (s.change_pct ?? 0) < 0).length
        setAdvDec(`${adv} / ${dec}`)

        let totalLiq = 0
        let totalForeign = 0
        for (const s of stocks) {
          totalLiq += (s.price || 0) * (s.volume || 0)
          totalForeign += (s.foreign_flow || 0)
        }
        if (totalLiq > 0) {
          setLiquidity((totalLiq / 1e12).toFixed(1) + "T")
        }
        setForeignFlow(totalForeign >= 0 ? `+${totalForeign.toFixed(0)}B` : `${totalForeign.toFixed(0)}B`)
      }
    })

    return () => { mounted = false }
  }, [])

  const defaultSeries = [1810, 1815, 1818, 1822, 1825, 1820, 1828, 1830.44]
  const displaySeries = historySeries.length > 0 ? historySeries : defaultSeries

  return (
    <div className="overflow-hidden rounded-[14px] border border-line-strong bg-surface shadow-[0_24px_60px_rgba(24,32,29,.12)]">
      <div className="flex h-11 items-center border-b border-line px-4">
        <span className="font-mono text-[11px] text-muted">
          THỊ TRƯỜNG / TỔNG QUAN
        </span>
        <span className="ml-auto flex items-center gap-1.5 text-[10px] text-gain">
          <i className="h-1.5 w-1.5 rounded-full bg-gain animate-pulse" />
          TRỰC TIẾP · DỮ LIỆU SÀN
        </span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-[1.35fr_.9fr]">
        <div className="p-5">
          <div className="flex items-end justify-between border-b border-line pb-4">
            <div>
              <div className="text-[10px] font-semibold tracking-[.14em] text-muted">
                VN-INDEX
              </div>
              <div className="mt-1 flex items-baseline gap-2">
                <span className="font-mono text-[29px] font-semibold tracking-tight text-ink">
                  {indexData?.value ? indexData.value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "1,830.44"}
                </span>
                <PercentChange
                  value={indexData?.change_pct ?? 0.53}
                  arrow={false}
                  className="text-[13px]"
                />
              </div>
            </div>
            <div className="text-right text-[10px] text-muted">
              {indexData?.date || "08 SEP 2026"}
              <br />
              <span className="text-teal">Xu hướng tăng</span>
            </div>
          </div>
          <div className="pt-4">
            <MarketLineChart
              height={154}
              series={[
                {
                  label: "VN-Index",
                  data: displaySeries,
                  color: "var(--color-mineral)",
                },
              ]}
            />
          </div>
        </div>
        <div className="border-t border-line bg-paper p-4 sm:border-l sm:border-t-0">
          <div className="text-[10px] font-semibold tracking-[.14em] text-muted">
            CHẾ ĐỘ THỊ TRƯỜNG
          </div>
          <div className="mt-2 text-[18px] font-semibold tracking-tight text-ink">
            Tích cực
          </div>
          <p className="mt-1 text-[12px] leading-relaxed text-secondary">
            Độ rộng và thanh khoản thị trường củng cố đà tăng dẫn dắt bởi nhóm ngân hàng.
          </p>
          <div className="mt-5 space-y-2.5">
            {[
              ["Thanh khoản", liquidity !== "—" ? liquidity : "18.7T", "text-ink"],
              ["Dòng tiền ngoại", foreignFlow !== "—" ? foreignFlow : "+182B", foreignFlow.startsWith("-") ? "text-loss" : "text-gain"],
              ["Số mã tăng / giảm", advDec !== "—" ? advDec : "58 / 42", "text-ink"],
              ["Trạng thái thị trường", "Phiên bình thường", "text-teal"],
            ].map(([l, v, t]) => (
              <div
                key={l}
                className="flex justify-between border-b border-line pb-2 text-[11px]"
              >
                <span className="text-muted">{l}</span>
                <span className={`font-mono ${t}`}>{v}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function AskDemo() {
  const [query, setQuery] = useState(
    "Tại sao khối ngoại mua ròng mạnh HPG hôm nay?",
  )
  const [asked, setAsked] = useState(true)
  const answers: Record<string, string> = {
    "Tại sao khối ngoại mua ròng mạnh HPG hôm nay?":
      "Khối ngoại mua ròng 182 tỷ VNĐ tại HPG khi khối lượng giao dịch đạt 2.1× trung bình 20 phiên. Động lực được hỗ trợ bởi sự hồi phục biên lợi nhuận Q2, song vẫn cần theo dõi rủi ro từ thị trường xuất khẩu.",
    "So sánh xung lực giữa MBB và TCB":
      "MBB dẫn trước TCB về dòng tiền (+143 tỷ so với +88 tỷ) và điểm phần trăm xung lực (74 so với 66). Cả hai đều hưởng lợi từ độ rộng ngành ngân hàng; MBB có tín hiệu xác nhận trong phiên rõ ràng hơn.",
    "Nhóm ngành Bất động sản có biến động gì?":
      "Độ rộng ngành Bất động sản suy yếu khi VHM giảm 2.05% và dòng tiền ngoại đảo chiều bán ròng. Đây là sự phân kỳ với thị trường chung, chưa phải đảo chiều toàn ngành.",
  }
  return (
    <div className="rounded-[12px] border border-line-strong bg-surface shadow-sm">
      <div className="border-b border-line px-5 py-4">
        <div className="flex items-center gap-2">
          <span className="grid h-6 w-6 place-items-center rounded-[6px] bg-ink font-serif text-[13px] text-paper">
            A
          </span>
          <span className="text-[13px] font-semibold text-ink">
            Hỏi đáp AIInvest
          </span>
          <Pill tone="teal">Chế độ dẫn chứng</Pill>
        </div>
      </div>
      <div className="p-5">
        <div className="rounded-[8px] border border-line bg-paper p-3 text-[13px] text-ink">
          {query}
        </div>
        {asked && (
          <div className="mt-4 border-l-2 border-teal pl-4">
            <div className="text-[10px] font-semibold uppercase tracking-[.14em] text-teal">
              Câu trả lời · 14:27 ICT
            </div>
            <p className="mt-2 text-[13px] leading-relaxed text-secondary">
              {answers[query]}
            </p>
            <div className="mt-4 grid grid-cols-3 gap-2">
              {[
                ["Dòng tiền", "+182B", "gain"],
                ["Khối lượng", "2.1×", "mineral"],
                ["Rủi ro", "Xuất khẩu thép", "warning"],
              ].map(([l, v, t]) => (
                <div key={l} className="rounded-[6px] bg-soft px-2.5 py-2">
                  <div className="text-[9px] uppercase tracking-wide text-muted">
                    {l}
                  </div>
                  <div
                    className={`mt-0.5 text-[11px] font-medium ${t === "gain" ? "text-gain" : t === "warning" ? "text-warning" : "text-ink"}`}
                  >
                    {v}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
        <div className="mt-5 flex flex-wrap gap-2">
          {Object.keys(answers).map((item) => (
            <button
              key={item}
              onClick={() => {
                setQuery(item)
                setAsked(true)
              }}
              className="rounded-full border border-line px-3 py-1.5 text-left text-[11px] text-secondary transition-colors hover:border-mineral hover:text-ink"
            >
              {item}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

function Pipeline() {
  const [active, setActive] = useState(3)
  const [showDetail, setShowDetail] = useState(false)
  const step = pipeline[active]
  const statusOf = (i: number) =>
    i < 6 ? "Completed" : i === 6 ? "Working" : "Waiting"
  return (
    <div className="grid grid-cols-1 overflow-hidden rounded-[12px] border border-line-strong bg-surface lg:grid-cols-[1fr_320px]">
      <div className="max-h-[520px] divide-y divide-line overflow-y-auto">
        {pipeline.map((p, i) => {
          const status = statusOf(i)
          return (
            <button
              onClick={() => {
                setActive(i)
                setShowDetail(false)
              }}
              key={p.id}
              className={`flex w-full items-center gap-3.5 px-5 py-3.5 text-left transition-colors ${active === i ? "bg-soft" : "hover:bg-paper"}`}
            >
              <span className="w-6 shrink-0 font-mono text-[11px] text-muted">
                {p.id}
              </span>
              <span
                className={`h-2 w-2 shrink-0 rounded-full ${status === "Completed" ? "bg-teal" : status === "Working" ? "bg-mineral animate-pulse" : "bg-line-strong"}`}
              />
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">
                  {p.name}
                </span>
                <span className="block text-[11px] text-secondary">{p.vn}</span>
              </span>
              <span className="shrink-0 text-[10px] uppercase tracking-wide text-muted">
                {p.phase}
              </span>
            </button>
          )
        })}
      </div>
      <div className="border-t border-line bg-paper p-5 lg:border-l lg:border-t-0">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[11px] text-muted">{step.id}</span>
          <Pill tone="neutral">{step.phase}</Pill>
        </div>
        <div className="mt-3 text-[17px] font-semibold text-ink">
          {step.name}
        </div>
        <p className="mt-2 text-[13px] leading-relaxed text-secondary">
          {step.does}
        </p>
        <p className="mt-2 text-[11px] italic leading-snug text-muted">
          → {step.handoff}
        </p>
        <div className="mt-4 text-[10px] font-semibold uppercase tracking-[.14em] text-muted">
          Kết quả nổi bật
        </div>
        <div className="mt-2 space-y-1.5">
          {step.headline.map((m) => (
            <div
              key={m.label}
              className="flex items-baseline justify-between gap-3 text-[12px]"
            >
              <span className="text-secondary">{m.label}</span>
              <span
                className={`tnum font-mono font-medium ${metricTone[m.tone ?? "neutral"]}`}
              >
                {m.value}
              </span>
            </div>
          ))}
        </div>
        <button
          onClick={() => setShowDetail((v) => !v)}
          className="mt-4 text-[11px] font-medium text-mineral hover:underline"
        >
          {showDetail ? "Ẩn chi tiết ↑" : "Xem chi tiết ↓"}
        </button>
        {showDetail && (
          <ul className="mt-3 space-y-1.5 border-t border-line pt-3 text-[11.5px] leading-snug text-secondary">
            {step.detail.map((d) => (
              <li key={d} className="flex gap-1.5">
                <span className="text-muted">·</span>
                {d}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function ResearchCase() {
  const [tab, setTab] = useState("Luận điểm")
  const [quote, setQuote] = useState<{ price: number; change_pct: number } | null>(null)

  useEffect(() => {
    stockApi.quote("HPG").then((q) => {
      if (q && q.price) setQuote({ price: q.price, change_pct: q.change_pct ?? 0 })
    }).catch(() => {})
  }, [])

  const currentPrice = quote?.price || 21850
  const changePct = quote?.change_pct ?? 1.39

  return (
    <div className="rounded-[13px] border border-line-strong bg-surface shadow-sm">
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-4">
        <span className="font-mono text-[17px] font-semibold text-ink">
          HPG
        </span>
        <span className="text-[13px] text-secondary">Hòa Phát Group</span>
        <span className="ml-auto font-mono text-[14px] text-ink">{currentPrice.toLocaleString()}đ</span>
        <PercentChange value={changePct} arrow={false} className="text-[13px]" />
      </div>
      <div className="px-5 pt-2">
        <Tabs
          tabs={["Luận điểm", "Tài chính", "Lợi thế MOAT", "Định giá", "Rủi ro"]}
          active={tab === "Thesis" ? "Luận điểm" : tab === "Financials" ? "Tài chính" : tab === "MOAT" ? "Lợi thế MOAT" : tab === "Valuation" ? "Định giá" : tab === "Risk" ? "Rủi ro" : tab}
          onChange={setTab}
        />
      </div>
      <div className="grid min-h-[230px] grid-cols-1 gap-6 p-5 md:grid-cols-[1.2fr_.8fr]">
        {(tab === "Luận điểm" || tab === "Thesis") ? (
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-[.14em] text-muted">
              Đánh giá hiện tại
            </div>
            <h3 className="mt-2 font-serif text-[24px] leading-tight text-ink">
              Xu hướng tích lũy gia tăng khi biên lợi nhuận ngành thép hồi phục.
            </h3>
            <p className="mt-3 text-[13px] leading-relaxed text-secondary">
              Nhu cầu xây dựng cải thiện, tiến độ dự án Dung Quất 2 đúng kế hoạch và
              sản lượng tiêu thụ nội địa phục hồi tạo nên luận điểm đầu tư vững chắc.
            </p>
            <div className="mt-4 flex gap-2">
              <Pill tone="teal">Xu hướng tích cực</Pill>
              <Pill tone="gold">Độ tin cậy trung bình</Pill>
            </div>
          </div>
        ) : (
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-[.14em] text-muted">
              Phân tích {tab}
            </div>
            <h3 className="mt-2 text-[19px] font-semibold text-ink">
              {(tab === "Tài chính" || tab === "Financials")
                ? "Biên lợi nhuận gộp Q2 đạt 13.2%"
                : (tab === "Lợi thế MOAT" || tab === "MOAT")
                  ? "Năng lực sản xuất thép tích hợp quy mô hàng đầu"
                  : (tab === "Định giá" || tab === "Valuation")
                    ? "Vùng giá mục tiêu: 25,000–27,500"
                    : "Đóng nến dưới 20,500 sẽ vi phạm luận điểm"}
            </h3>
            <p className="mt-3 text-[13px] leading-relaxed text-secondary">
              Mọi kết luận đều được gắn chặt với báo cáo tài chính công bố, dữ liệu thị trường có thể quan sát hoặc điều kiện rủi ro cụ thể.
            </p>
          </div>
        )}
        <div className="border-l-0 border-line md:border-l md:pl-6">
          <div className="text-[10px] font-semibold uppercase tracking-[.14em] text-muted">
            Khung ra quyết định
          </div>
          {[
            ["Chất xúc tác", "Đưa lò cao Dung Quất 2 vào vận hành"],
            ["Mục tiêu", "25,000–27,500"],
            ["Điều kiện vi phạm", "< 20,500"],
            ["Thời gian nắm giữ", "3–6 tháng"],
          ].map(([l, v]) => (
            <div
              key={l}
              className="flex justify-between border-b border-line py-3 text-[12px]"
            >
              <span className="text-secondary">{l}</span>
              <span className="font-mono text-ink">{v}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function EvidenceExplorer() {
  const [source, setSource] = useState(0)
  const [docs, setDocs] = useState<Array<{ source: string; detail: string; date: string; type: string }>>([])

  useEffect(() => {
    stockApi.news("HPG").then((items) => {
      if (Array.isArray(items) && items.length > 0) {
        const mapped = items.slice(0, 4).map((d: ApiNewsItem) => ({
          source: d.title || "Tài liệu công bố HPG",
          detail: d.ai_summary || d.article_content?.slice(0, 200) || "Công bố thông tin chính thức của doanh nghiệp trên Sở GDCK TP.HCM.",
          date: d.published_date ? new Date(d.published_date).toLocaleDateString("vi-VN") : "Gần đây",
          type: d.doc_type || d.source || "Filing",
        }))
        setDocs(mapped)
      }
    }).catch(() => {})
  }, [])

  const items = docs.length > 0 ? docs : citations
  const current = items[source] || items[0]

  return (
    <div className="rounded-[12px] border border-line-strong bg-surface">
      <div className="grid grid-cols-1 md:grid-cols-[240px_1fr]">
        <div className="border-b border-line bg-paper p-3 md:border-b-0 md:border-r">
          {items.slice(0, 4).map((item, i) => (
            <button
              key={item.source + i}
              onClick={() => setSource(i)}
              className={`mb-1 w-full rounded-[7px] p-3 text-left transition-colors ${source === i ? "bg-surface shadow-sm" : "hover:bg-surface/70"}`}
            >
              <span className="text-[10px] font-medium text-mineral uppercase">
                {item.type}
              </span>
              <span className="mt-1 block text-[12px] font-medium leading-snug text-ink line-clamp-2">
                {item.source}
              </span>
            </button>
          ))}
        </div>
        <div className="p-6">
          <div className="flex items-center gap-2">
            <Pill tone="mineral">{current.type}</Pill>
            <span className="font-mono text-[10px] text-muted">
              {current.date}
            </span>
          </div>
          <h3 className="mt-4 text-[18px] font-semibold tracking-tight text-ink">
            {current.source}
          </h3>
          <p className="mt-3 max-w-xl font-serif text-[16px] leading-relaxed text-ink">
            “{current.detail}”
          </p>
          <div className="mt-6 border-t border-line pt-4 text-[12px] leading-relaxed text-secondary">
            <span className="font-semibold text-ink">Traceability:</span> Nguồn từ cơ sở dữ liệu 48,285 tài liệu BCTC & Báo cáo quản trị doanh nghiệp HOSE.
          </div>
        </div>
      </div>
    </div>
  )
}

export default function Landing() {
  const { navigate } = useRouter()
  const enter = () => navigate("/signup")
  return (
    <div className="min-h-full bg-paper text-ink">
      <header className="mx-auto flex h-16 max-w-[1280px] items-center px-6 lg:px-10">
        <Logo onClick={() => navigate("/")} />
        <nav className="ml-10 hidden items-center gap-7 text-[13px] text-secondary md:flex">
          <a href="#intelligence" className="hover:text-ink">
            Trí tuệ nhân tạo
          </a>
          <a href="#research" className="hover:text-ink">
            Nghiên cứu
          </a>
          <a href="#evidence" className="hover:text-ink">
            Dẫn chứng
          </a>
        </nav>
        <div className="ml-auto flex gap-2">
          <Button variant="ghost" onClick={() => navigate("/login")}>
            Đăng nhập
          </Button>
          <Button variant="primary" onClick={() => navigate("/signup")}>
            Đăng ký tài khoản
          </Button>
        </div>
      </header>
      <main>
        <section className="relative overflow-hidden border-y border-line">
          <div
            aria-hidden
            className="absolute inset-0 opacity-50"
            style={{
              backgroundImage:
                "linear-gradient(var(--color-line) 1px,transparent 1px),linear-gradient(90deg,var(--color-line) 1px,transparent 1px)",
              backgroundSize: "58px 58px",
              maskImage:
                "radial-gradient(ellipse 80% 70% at 60% 40%,black,transparent)",
            }}
          />
          <div className="relative mx-auto grid max-w-[1280px] grid-cols-1 items-center gap-12 px-6 py-16 lg:grid-cols-[.9fr_1.1fr] lg:px-10 lg:py-24">
            <div>
              <Pill tone="mineral">Trí tuệ nhân tạo đầu tư chứng khoán · Việt Nam</Pill>
              <h1 className="mt-5 font-serif text-[clamp(44px,6vw,72px)] leading-[.98] tracking-[-.035em] text-ink">
                Thị trường biến động liên tục.
                <br />
                <span className="text-teal">Tư duy đầu tư cũng cần thích ứng.</span>
              </h1>
              <p className="mt-6 max-w-lg text-[16px] leading-relaxed text-secondary">
                AIInvest là môi trường nghiên cứu chuyên sâu cho thị trường chứng khoán Việt Nam: bối cảnh vĩ mô, dữ liệu cổ phiếu chuẩn hóa, phân tích từ hệ thống tác tử và bằng chứng xác thực cho từng quyết định.
              </p>
              <div className="mt-8 flex flex-wrap gap-3">
                <Button
                  variant="primary"
                  className="h-11 px-5 text-[14px]"
                  onClick={enter}
                >
                  Khám phá không gian làm việc
                </Button>
                <button
                  onClick={() =>
                    document
                      .querySelector("#intelligence")
                      ?.scrollIntoView({ behavior: "smooth" })
                  }
                  className="h-11 px-3 text-[13px] font-medium text-secondary hover:text-ink"
                >
                  Xem quy trình phân tích ↓
                </button>
              </div>
              <div className="mt-9 flex gap-6 text-[11px] text-muted">
                <span className="flex items-center gap-1.5">
                  <i className="h-1.5 w-1.5 rounded-full bg-gain animate-pulse" />
                  Dữ liệu sàn trực tiếp
                </span>
                <span>12 tác tử chuyên biệt</span>
                <span>Nguồn gốc minh bạch</span>
              </div>
            </div>
            <MarketPulse />
          </div>
        </section>
        <section
          id="intelligence"
          className="mx-auto max-w-[1280px] px-6 py-24 lg:px-10"
        >
          <div className="grid grid-cols-1 gap-14 lg:grid-cols-[.75fr_1.25fr]">
            <div className="lg:pt-10">
              <div className="text-[11px] font-semibold uppercase tracking-[.16em] text-muted">
                01 — Vấn đáp thị trường
              </div>
              <h2 className="mt-4 font-serif text-[38px] leading-[1.06] tracking-tight text-ink">
                Từ một câu hỏi đến góc nhìn đầu tư chuẩn mực.
              </h2>
              <p className="mt-5 text-[15px] leading-relaxed text-secondary">
                Đặt câu hỏi theo ngôn ngữ của nhà đầu tư. AIInvest liên kết câu trả lời với biến động giá, ngành nghề, báo cáo tài chính, dòng tiền và rủi ro — không phải một điểm số hộp đen.
              </p>
              <div className="mt-7 border-l border-gold pl-4 text-[13px] leading-relaxed text-secondary">
                “Điều quan trọng không phải câu trả lời nhanh hơn, mà là câu trả lời bạn có thể kiểm chứng được.”
              </div>
            </div>
            <AskDemo />
          </div>
        </section>
        <section className="border-y border-line bg-surface">
          <div className="mx-auto grid max-w-[1280px] grid-cols-1 gap-12 px-6 py-24 lg:grid-cols-[1.05fr_.95fr] lg:px-10">
            <Pipeline />
            <div className="flex flex-col justify-center">
              <div className="text-[11px] font-semibold uppercase tracking-[.16em] text-muted">
                02 — Nghiên cứu đa tác tử
              </div>
              <h2 className="mt-4 font-serif text-[38px] leading-[1.06] tracking-tight text-ink">
                Thị trường → Ngành nghề → Cổ phiếu. Không bỏ sót bất kỳ mắt xích nào.
              </h2>
              <p className="mt-5 text-[15px] leading-relaxed text-secondary">
                Hồ sơ nghiên cứu luân chuyển qua các tác tử chuyên trách với sự chuyển giao rõ ràng. Chế độ thị trường thành bối cảnh ngành; bối cảnh ngành thành đánh giá cổ phiếu; đánh giá được kiểm định sức ép rủi ro trước khi đưa ra đề xuất.
              </p>
              <div className="mt-7 grid grid-cols-3 gap-4 border-t border-line pt-5">
                {[
                  ["12", "tác tử chuyên biệt"],
                  ["04", "lớp bằng chứng"],
                  ["01", "khung rủi ro thống nhất"],
                ].map(([n, l]) => (
                  <div key={l}>
                    <div className="font-mono text-[22px] font-semibold text-ink">
                      {n}
                    </div>
                    <div className="mt-1 text-[11px] text-muted">{l}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>
        <section
          id="research"
          className="mx-auto max-w-[1280px] px-6 py-24 lg:px-10"
        >
          <div className="mb-10 flex max-w-2xl flex-col gap-3">
            <div className="text-[11px] font-semibold uppercase tracking-[.16em] text-muted">
              03 — Nghiên cứu mang tính quyết định
            </div>
            <h2 className="font-serif text-[40px] leading-[1.05] tracking-tight text-ink">
              Nắm trọn vẹn toàn bộ luận điểm đầu tư.
            </h2>
            <p className="text-[15px] leading-relaxed text-secondary">
              Chất lượng tài chính, lợi thế cạnh tranh, định giá và điều kiện vi phạm được hợp nhất trong một đối tượng nghiên cứu sống động — không còn phân mảnh qua nhiều tab hay file PDF.
            </p>
          </div>
          <ResearchCase />
        </section>
        <section id="evidence" className="border-y border-line bg-[#edf1ef]">
          <div className="mx-auto grid max-w-[1280px] grid-cols-1 gap-14 px-6 py-24 lg:grid-cols-[.72fr_1.28fr] lg:px-10">
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[.16em] text-muted">
                04 — Bằng chứng là trọng tâm hàng đầu
              </div>
              <h2 className="mt-4 font-serif text-[38px] leading-[1.06] tracking-tight text-ink">
                Mọi kết luận đều có xuất xứ và dẫn chứng rõ ràng.
              </h2>
              <p className="mt-5 text-[15px] leading-relaxed text-secondary">
                Mở tài liệu gốc đằng sau luận điểm, theo dõi quá trình chuyển giao qua các tác tử, và thấy chính xác quan sát nào đã làm thay đổi góc nhìn đầu tư. Tính minh bạch là giá trị cốt lõi.
              </p>
              <div className="mt-7 flex flex-wrap gap-2">
                {[
                  "Công bố thông tin SGDCK",
                  "Báo cáo tài chính",
                  "Dòng tiền ngoại",
                  "Dữ liệu vĩ mô",
                ].map((x) => (
                  <Pill key={x} tone="neutral">
                    {x}
                  </Pill>
                ))}
              </div>
            </div>
            <EvidenceExplorer />
          </div>
        </section>
        <section className="mx-auto max-w-[1280px] px-6 py-24 lg:px-10">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-[1.15fr_.85fr]">
            <div className="rounded-[12px] bg-ink p-8 text-paper">
              <div className="text-[11px] font-semibold uppercase tracking-[.16em] text-paper/55">
                05 — Quản trị danh mục thông minh
              </div>
              <h2 className="mt-4 max-w-lg font-serif text-[36px] leading-[1.06]">
                Nghiên cứu không dừng lại khi bạn đã sở hữu cổ phiếu.
              </h2>
              <p className="mt-4 max-w-md text-[14px] leading-relaxed text-paper/70">
                Chuyển hóa mức độ tự tin thành tỷ trọng phân bổ, giám sát độ tương quan rủi ro và xác định trước các điều kiện sẽ khiến bạn thay đổi quyết định.
              </p>
              <Button
                variant="secondary"
                className="mt-7 border-white/25 bg-transparent text-paper hover:bg-white/10"
              >
                Xem quy trình danh mục
              </Button>
            </div>
            <PortfolioRiskFrame />
          </div>
        </section>
        <section className="border-t border-line bg-surface">
          <div className="mx-auto max-w-[1280px] px-6 py-20 text-center lg:px-10">
            <div className="text-[11px] font-semibold uppercase tracking-[.16em] text-muted">
              Mạng lưới nghiên cứu chuyên nghiệp
            </div>
            <h2 className="mx-auto mt-4 max-w-2xl font-serif text-[40px] leading-[1.06] tracking-tight text-ink">
              Khoảng lặng lý tính giữa tín hiệu và quyết định.
            </h2>
            <p className="mx-auto mt-5 max-w-xl text-[15px] leading-relaxed text-secondary">
              Được xây dựng cho những nhà đầu tư tìm kiếm sự chuẩn mực, kỷ luật với thị trường chứng khoán Việt Nam — làm chủ hoàn toàn quyết định của chính mình.
            </p>
            <div className="mt-8 flex justify-center">
              <Button
                variant="primary"
                className="h-11 px-6 text-[14px]"
                onClick={enter}
              >
                Tham gia cộng đồng
              </Button>
            </div>
          </div>
        </section>
      </main>
      <footer className="mx-auto flex max-w-[1280px] flex-wrap items-center gap-4 px-6 py-8 text-[11px] text-muted lg:px-10">
        <Logo onClick={() => navigate("/")} />
        <span className="ml-auto">
          Thị trường chứng khoán Việt Nam · Công cụ hỗ trợ nghiên cứu, không phải khuyến nghị đầu tư.
        </span>
      </footer>
    </div>
  )
}
