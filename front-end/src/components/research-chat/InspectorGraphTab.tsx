"use client"

import { useEffect, useRef } from "react"
import { ArrowRight, GitMerge, Network, TrendingUp, Users } from "lucide-react"
import { Pill } from "@/components/ui"
import type { GraphHop } from "@/lib/api/researchChat"
import type { InspectorFocus } from "./types"

function inferLayer(hop: GraphHop): "MACRO" | "VALUE_CHAIN" | "TRUE_PEER" {
  const explicit = String(hop.layer ?? "").toUpperCase()
  if (explicit === "MACRO" || explicit === "VALUE_CHAIN" || explicit === "TRUE_PEER") {
    return explicit
  }
  const rel = String(hop.relation_type ?? hop.relation ?? "").toUpperCase()
  if (rel.includes("MACRO") || rel.includes("SECTOR")) return "MACRO"
  if (rel.includes("PEER")) return "TRUE_PEER"
  return "VALUE_CHAIN"
}

function tierTone(tier: string): string {
  const t = tier.toUpperCase()
  if (t === "CONFIRMED_BOTH") return "gain"
  if (t === "CONFLICT_FLAG") return "loss"
  if (t === "QUANT_DOC_NEUTRAL" || t === "QUANT_ONLY") return "mineral"
  if (t === "DOC_ONLY") return "gold"
  if (t === "CLUSTER") return "teal"
  return "neutral"
}

function hopMatchesFocus(hop: GraphHop, idx: number, focus: InspectorFocus | null): boolean {
  if (!focus || focus.tab !== "graph") return false
  const tier = String(hop.verification_tier ?? hop.tier ?? "").toUpperCase()
  const layer = inferLayer(hop)
  if (focus.kgTier) {
    if (tier === focus.kgTier || layer === focus.kgTier) return true
  }
  if (focus.kgIndex != null && idx + 1 === focus.kgIndex) return true
  return false
}

export function InspectorGraphTab({
  graphHops,
  expandedTickers,
  focus,
}: {
  graphHops: GraphHop[]
  expandedTickers: string[]
  focus: InspectorFocus | null
}) {
  const itemRefs = useRef<Record<string, HTMLDivElement | null>>({})

  useEffect(() => {
    if (!focus || focus.tab !== "graph") return
    const idx = graphHops.findIndex((h, i) => hopMatchesFocus(h, i, focus))
    if (idx >= 0) {
      itemRefs.current[`hop-${idx}`]?.scrollIntoView({ behavior: "smooth", block: "nearest" })
    }
  }, [focus, graphHops])

  const macroHops = graphHops
    .map((hop, idx) => ({ hop, idx }))
    .filter(({ hop }) => inferLayer(hop) === "MACRO")
  const valueChainHops = graphHops
    .map((hop, idx) => ({ hop, idx }))
    .filter(({ hop }) => inferLayer(hop) === "VALUE_CHAIN")
  const truePeerHops = graphHops
    .map((hop, idx) => ({ hop, idx }))
    .filter(({ hop }) => inferLayer(hop) === "TRUE_PEER")

  if (graphHops.length === 0 && expandedTickers.length <= 1) {
    return (
      <div className="py-12 text-center text-[13px] text-muted">
        Không có bước nhảy Đồ thị 3 Tầng (KG) nào được kích hoạt cho câu trả lời này.
      </div>
    )
  }

  const renderSection = (
    title: string,
    icon: typeof Network,
    items: Array<{ hop: GraphHop; idx: number }>,
  ) => {
    if (items.length === 0) return null
    const Icon = icon
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-1.5 text-[11.5px] font-semibold uppercase tracking-[0.08em] text-muted">
          <Icon className="h-3.5 w-3.5 text-mineral" aria-hidden="true" />
          <span>
            {title} ({items.length})
          </span>
        </div>

        <div className="space-y-2">
          {items.map(({ hop, idx }) => {
            const key = `hop-${idx}`
            const src = hop.src_label || hop.from || "—"
            const dst = hop.dst_label || hop.to || "—"
            const rel = hop.relation_type || hop.relation || "RELATED"
            const tier = String(hop.verification_tier ?? hop.tier ?? "CLUSTER").toUpperCase()
            const highlighted = hopMatchesFocus(hop, idx, focus)

            return (
              <div
                key={key}
                ref={(el) => {
                  itemRefs.current[key] = el
                }}
                className={`rounded-[9px] border p-3 space-y-2 transition-all ${
                  highlighted
                    ? "border-mineral bg-mineral/6 ring-2 ring-mineral/20"
                    : "border-line bg-surface"
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-1.5 text-[12.5px] font-medium text-ink min-w-0">
                    <span className="rounded bg-soft px-2 py-0.5 font-mono text-[11.5px] border border-line">
                      {src}
                    </span>
                    <ArrowRight className="h-3.5 w-3.5 text-muted shrink-0" aria-hidden="true" />
                    <span className="font-mono text-[11px] text-mineral font-semibold">
                      {rel}
                    </span>
                    <ArrowRight className="h-3.5 w-3.5 text-muted shrink-0" aria-hidden="true" />
                    <span className="rounded bg-soft px-2 py-0.5 font-mono text-[11.5px] border border-line">
                      {dst}
                    </span>
                  </div>
                  <Pill tone={tierTone(tier)}>
                    <span className="font-mono">{tier}</span>
                  </Pill>
                </div>

                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] font-mono text-secondary tnum">
                  {hop.beta != null && Number.isFinite(hop.beta) && (
                    <span>
                      β:{" "}
                      <strong className={hop.beta >= 0 ? "text-gain" : "text-loss"}>
                        {hop.beta >= 0 ? "+" : ""}
                        {hop.beta.toFixed(3)}
                      </strong>
                    </span>
                  )}
                  {hop.p_value != null && Number.isFinite(hop.p_value) && (
                    <span>p={hop.p_value.toFixed(4)}</span>
                  )}
                  {hop.fdr_q_value != null && Number.isFinite(hop.fdr_q_value) && (
                    <span>FDR q={hop.fdr_q_value.toFixed(4)}</span>
                  )}
                  {hop.tercile_stable != null && (
                    <span>
                      Tercile:{" "}
                      <strong className={hop.tercile_stable ? "text-teal" : "text-warning"}>
                        {hop.tercile_stable ? "Ổn định (✓)" : "Chưa ổn định"}
                      </strong>
                    </span>
                  )}
                  {hop.similarity != null && Number.isFinite(hop.similarity) && (
                    <span>Sim={hop.similarity.toFixed(3)}</span>
                  )}
                  {hop.effective_from && (
                    <span className="text-muted">PIT ≥ {hop.effective_from}</span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {expandedTickers.length > 0 && (
        <div className="rounded-[8px] border border-line bg-paper px-3 py-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted mr-1">
            Mạng mã mở rộng (PIT)
          </span>
          {expandedTickers.map((t) => (
            <Pill key={t} tone="mineral">
              <span className="font-mono font-semibold">{t}</span>
            </Pill>
          ))}
        </div>
      )}

      {renderSection("Tầng 1 · Vĩ mô & Liên ngành (MACRO)", TrendingUp, macroHops)}
      {renderSection("Tầng 2 · Chuỗi giá trị & Doanh nghiệp (VALUE_CHAIN)", GitMerge, valueChainHops)}
      {renderSection("Tầng 3 · Cụm Đối thủ Thực chất (TRUE_PEER)", Users, truePeerHops)}
    </div>
  )
}
