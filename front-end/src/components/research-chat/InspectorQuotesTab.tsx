"use client"

import { useEffect, useRef } from "react"
import { CheckCircle2, FileText, ShieldAlert } from "lucide-react"
import { Pill } from "@/components/ui"
import type { ResearchCitation } from "@/lib/api/researchChat"
import type { InspectorFocus } from "./types"

function citationMatchesFocus(
  cit: ResearchCitation,
  idx: number,
  focus: InspectorFocus | null,
): boolean {
  if (!focus || focus.tab !== "quotes") return false
  if (focus.citationId && cit.citation_id?.toUpperCase() === focus.citationId.toUpperCase()) {
    return true
  }
  if (focus.slot && focus.page != null) {
    const slotPrefix = focus.slot.split("_")[0]
    const citPrefix = (cit.sub_doc_slot || "").toUpperCase().split("_")[0]
    if (
      (cit.sub_doc_slot?.toUpperCase() === focus.slot.toUpperCase() || citPrefix === slotPrefix) &&
      Number(cit.page) === focus.page
    ) {
      return true
    }
  }
  if (focus.citationIndex != null) {
    if (cit.n != null && Number(cit.n) === focus.citationIndex) return true
    if (idx + 1 === focus.citationIndex) return true
  }
  return false
}

export function InspectorQuotesTab({
  citations,
  focus,
}: {
  citations: ResearchCitation[]
  focus: InspectorFocus | null
}) {
  const itemRefs = useRef<Record<string, HTMLDivElement | null>>({})

  useEffect(() => {
    if (!focus || focus.tab !== "quotes") return
    const targetIdx = citations.findIndex((c, idx) => citationMatchesFocus(c, idx, focus))
    if (targetIdx >= 0) {
      const key = citations[targetIdx].citation_id || `cit-${targetIdx}`
      itemRefs.current[key]?.scrollIntoView({ behavior: "smooth", block: "nearest" })
    }
  }, [focus, citations])

  if (citations.length === 0) {
    return (
      <div className="py-12 text-center text-[13px] text-muted">
        Chưa có trích dẫn nào cho câu trả lời đang chọn.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {citations.map((cit, idx) => {
        const key = cit.citation_id || `cit-${idx}`
        const highlighted = citationMatchesFocus(cit, idx, focus)
        return (
          <div
            key={key}
            ref={(el) => {
              itemRefs.current[key] = el
            }}
            className={`rounded-[9px] border p-3.5 transition-all ${
              highlighted
                ? "border-mineral bg-mineral/6 ring-2 ring-mineral/20"
                : "border-line bg-surface"
            }`}
          >
            <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="inline-flex items-center gap-1 rounded bg-soft px-1.5 py-0.5 font-mono text-[11px] font-semibold text-ink border border-line">
                  <FileText className="h-3 w-3 text-mineral" aria-hidden="true" />
                  {cit.citation_id || `C${cit.n ?? idx + 1}`}
                </span>
                {cit.evidence_origin === "CROSS_REF" ? (
                  <Pill tone="warning">
                    <span className="font-mono">
                      CROSS-REF{cit.ticker ? ` · ${cit.ticker}` : ""}
                    </span>
                  </Pill>
                ) : (
                  <Pill tone="teal">
                    <span className="font-mono">
                      PRIMARY{cit.ticker ? ` · ${cit.ticker}` : ""}
                    </span>
                  </Pill>
                )}
                {cit.sub_doc_slot && (
                  <Pill tone="mineral">
                    <span className="font-mono">{cit.sub_doc_slot}</span>
                  </Pill>
                )}
                <span className="font-mono text-[11.5px] text-secondary">
                  Trang {cit.page ?? "—"}
                </span>
                {cit.published_date && (
                  <span className="font-mono text-[11px] text-muted">
                    · PIT {cit.published_date}
                  </span>
                )}
              </div>

              {cit.verified ? (
                <Pill tone="gain">
                  <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                  <span>quote_hash ✓</span>
                </Pill>
              ) : (
                <Pill tone="loss">
                  <ShieldAlert className="h-3 w-3" aria-hidden="true" />
                  <span>quote_hash ✗</span>
                </Pill>
              )}
            </div>

            <blockquote className="rounded-[7px] border border-line bg-paper px-3 py-2.5 text-[12.5px] leading-relaxed text-ink whitespace-pre-wrap">
              {cit.quote}
            </blockquote>

            {cit.quote_hash && (
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[10.5px] font-mono text-muted">
                <span className="truncate max-w-full">
                  SHA-256: {cit.quote_hash}
                </span>
                {cit.document_version_id && (
                  <span>ver: {cit.document_version_id.slice(0, 12)}</span>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
