"use client"

import { useState, useMemo } from "react"
import { Page } from "@/components/Shell"
import { Button, Panel, PanelHead, Pill } from "@/components/ui"
import { Link } from "@/lib/router"
import { workspaceApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"

interface KnowledgeDoc {
  id: number
  symbol: string | null
  title: string
  published_date: string | null
  source: string | null
  doc_type: string | null
  url: string | null
  ai_summary: string | null
}

interface InvestmentThesis {
  thesis_id: string
  ticker: string
  catalyst_type: string
  target_price_range: [number, number] | null
  confirming_signals: Record<string, string> | null
  invalidation_conditions: string[] | null
  status: string
  created_at: string
}

export default function ResearchPage() {
  const resource = useResource(() => workspaceApi.research().catch(() => ({ documents: [], theses: [] })), [])
  const [activeTab, setActiveTab] = useState<"docs" | "theses">("docs")
  const [selectedTag, setSelectedTag] = useState("ALL")
  const [search, setSearch] = useState("")

  const raw = resource.data as { documents?: KnowledgeDoc[]; theses?: InvestmentThesis[] } | null
  const documents = raw?.documents ?? []
  const theses = raw?.theses ?? []

  const categories = useMemo(() => {
    const types = new Set<string>()
    documents.forEach((d) => {
      if (d.doc_type) types.add(d.doc_type)
    })
    return ["ALL", ...Array.from(types)]
  }, [documents])

  const filteredDocs = useMemo(() => {
    return documents.filter((d) => {
      const matchTag = selectedTag === "ALL" || d.doc_type === selectedTag
      const matchSearch = !search ||
        (d.title?.toLowerCase().includes(search.toLowerCase())) ||
        (d.symbol?.toLowerCase().includes(search.toLowerCase()))
      return matchTag && matchSearch
    })
  }, [documents, selectedTag, search])

  return (
    <Page
      title="Thư viện nghiên cứu"
      sub="Thư viện nghiên cứu và luận điểm đầu tư tự hành từ AI Engine & BCTC sàn HOSE."
      actions={
        <div className="flex gap-2">
          <Button
            variant={activeTab === "docs" ? "primary" : "secondary"}
            onClick={() => setActiveTab("docs")}
          >
            Tài liệu & BCTC ({documents.length})
          </Button>
          <Button
            variant={activeTab === "theses" ? "primary" : "secondary"}
            onClick={() => setActiveTab("theses")}
          >
            Luận điểm đầu tư ({theses.length})
          </Button>
        </div>
      }
    >
      {activeTab === "docs" ? (
        <div>
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <div className="flex gap-1.5 flex-wrap">
              {categories.slice(0, 8).map((c) => (
                <button
                  key={c}
                  onClick={() => setSelectedTag(c)}
                  className={`text-[12px] px-3 py-1 rounded-full border transition-colors ${
                    selectedTag === c
                      ? "border-ink bg-ink text-paper font-medium"
                      : "border-line bg-surface text-secondary hover:border-ink/30"
                  }`}
                >
                  {c === "ALL" ? "Tất cả" : c}
                </button>
              ))}
            </div>
            <input
              type="text"
              placeholder="Tìm kiếm mã CP hoặc tiêu đề..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="h-8 px-3 rounded-[6px] border border-line bg-surface text-[12px] outline-none focus:border-mineral w-64"
            />
          </div>

          <div className="space-y-3">
            {filteredDocs.length === 0 ? (
              <Panel>
                <div className="py-8 text-center text-muted text-[13px]">
                  {resource.loading ? "Đang tải tài liệu nghiên cứu từ database..." : "Không tìm thấy tài liệu phù hợp."}
                </div>
              </Panel>
            ) : (
              filteredDocs.slice(0, 30).map((d) => (
                <Panel key={d.id}>
                  <div className="flex items-start gap-3.5">
                    {d.symbol ? (
                      <Link to={`/stock/${d.symbol}`}>
                        <span className="font-mono font-bold hover:underline cursor-pointer">
                          <Pill tone="teal">{d.symbol}</Pill>
                        </span>
                      </Link>
                    ) : (
                      <Pill tone="mineral">{d.doc_type || "Vĩ mô"}</Pill>
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="text-[14px] font-medium text-ink leading-snug">
                        {d.title}
                      </div>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-muted mt-1.5 font-mono">
                        <span>Nguồn: {d.source || "Công bố thông tin"}</span>
                        <span>·</span>
                        <span>{d.published_date ? new Date(d.published_date).toLocaleDateString("vi-VN") : "Gần đây"}</span>
                        {d.doc_type && (
                          <>
                            <span>·</span>
                            <span className="capitalize">{d.doc_type.replace(/_/g, " ")}</span>
                          </>
                        )}
                      </div>
                    </div>
                    {d.url && (
                      <a
                        href={d.url}
                        target="_blank"
                        rel="noreferrer"
                        className="shrink-0"
                      >
                        <Button variant="quiet">Tải / Xem file ↗</Button>
                      </a>
                    )}
                  </div>
                </Panel>
              ))
            )}
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <PanelHead
            title="Luận điểm đầu tư đang hoạt động"
            sub="Luận điểm định lượng được phát sinh tự hành bởi Agent 04 & duyệt bởi CIO"
          />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {theses.length === 0 ? (
              <Panel className="col-span-full text-center py-8 text-muted text-[13px]">
                {resource.loading ? "Đang tải luận điểm đầu tư..." : "Chưa có luận điểm đầu tư nào được ghi nhận."}
              </Panel>
            ) : (
              theses.map((t) => {
                const signals = t.confirming_signals ? Object.values(t.confirming_signals) : []
                const invalidations = t.invalidation_conditions || []
                return (
                  <Panel key={t.thesis_id}>
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <Link to={`/stock/${t.ticker}`}>
                          <span className="font-mono text-[16px] font-bold text-ink hover:underline cursor-pointer">
                            {t.ticker}
                          </span>
                        </Link>
                        <Pill tone="gold">{t.catalyst_type || "Cơ bản"}</Pill>
                      </div>
                      <Pill tone={t.status?.includes("APPROVED") ? "gain" : "neutral"}>
                        {t.status || "DRAFT"}
                      </Pill>
                    </div>

                    {t.target_price_range && (
                      <div className="text-[12.5px] font-mono text-ink mt-2 mb-3">
                        Mục tiêu: <span className="text-gain font-semibold">{t.target_price_range[0]?.toLocaleString()}</span> – <span className="text-gain font-semibold">{t.target_price_range[1]?.toLocaleString()}</span> đ
                      </div>
                    )}

                    {signals.length > 0 && (
                      <div className="mt-3 pt-3 border-t border-line text-[12px]">
                        <div className="text-[10.5px] uppercase tracking-wide text-muted mb-1 font-semibold">Tín hiệu xác nhận</div>
                        <ul className="space-y-1 text-secondary">
                          {signals.map((sig, i) => (
                            <li key={i} className="flex gap-1.5"><span className="text-gain">✓</span>{sig}</li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {invalidations.length > 0 && (
                      <div className="mt-3 pt-3 border-t border-line text-[12px]">
                        <div className="text-[10.5px] uppercase tracking-wide text-muted mb-1 font-semibold">Điều kiện vi phạm hủy bỏ</div>
                        <ul className="space-y-1 text-secondary">
                          {invalidations.map((inv, i) => (
                            <li key={i} className="flex gap-1.5"><span className="text-loss">✗</span>{inv}</li>
                          ))}
                        </ul>
                      </div>
                    )}

                    <div className="flex items-center justify-between mt-4 pt-3 border-t border-line text-[11px] text-muted font-mono">
                      <span>ID: {t.thesis_id}</span>
                      <span>{new Date(t.created_at).toLocaleDateString("vi-VN")}</span>
                    </div>
                  </Panel>
                )
              })
            )}
          </div>
        </div>
      )}
    </Page>
  )
}
