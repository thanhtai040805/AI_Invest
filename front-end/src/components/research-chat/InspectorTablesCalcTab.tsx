"use client"

import { useEffect, useRef } from "react"
import { AlertOctagon, Calculator, CheckCircle2, Table2 } from "lucide-react"
import { Pill } from "@/components/ui"
import {
  normalizeCalculatorInputs,
  type CalculatorTrace,
  type HydratedTable,
} from "@/lib/api/researchChat"
import type { InspectorFocus } from "./types"

function normalizeHeaders(headers: HydratedTable["headers"]): string[][] {
  if (!Array.isArray(headers) || headers.length === 0) return []
  if (Array.isArray(headers[0])) return headers as string[][]
  return [headers as string[]]
}

function normalizeRow(
  row: HydratedTable["rows"][number],
): Array<string | number | null> {
  if (Array.isArray(row)) return row
  if (row && typeof row === "object") {
    const label = row.label != null ? [row.label] : []
    const vals = Array.isArray(row.values)
      ? row.values
      : Array.isArray(row.cells)
        ? row.cells
        : []
    return [...label, ...vals]
  }
  return []
}

function formatCellValue(cell: string | number | null | undefined): string {
  if (cell === null || cell === undefined || cell === "") return "—"
  if (typeof cell === "number") {
    return Number.isFinite(cell) ? cell.toLocaleString("vi-VN") : "—"
  }
  return String(cell)
}

function tableMatchesFocus(table: HydratedTable, idx: number, focus: InspectorFocus | null): boolean {
  if (!focus || focus.tab !== "tables") return false
  if (focus.tableId && table.table_id === focus.tableId) return true
  if (focus.tableIndex != null && idx + 1 === focus.tableIndex) return true
  return false
}

function calcMatchesFocus(calc: CalculatorTrace, idx: number, focus: InspectorFocus | null): boolean {
  if (!focus || focus.tab !== "tables") return false
  if (focus.calcId && calc.calc_id?.toUpperCase() === focus.calcId.toUpperCase()) return true
  if (focus.calcIndex != null && idx + 1 === focus.calcIndex) return true
  return false
}

export function InspectorTablesCalcTab({
  tables,
  calculators,
  focus,
}: {
  tables: HydratedTable[]
  calculators: CalculatorTrace[]
  focus: InspectorFocus | null
}) {
  const tableRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const calcRefs = useRef<Record<string, HTMLDivElement | null>>({})

  useEffect(() => {
    if (!focus || focus.tab !== "tables") return
    const tblIdx = tables.findIndex((t, i) => tableMatchesFocus(t, i, focus))
    if (tblIdx >= 0) {
      const k = tables[tblIdx].table_id || `tbl-${tblIdx}`
      tableRefs.current[k]?.scrollIntoView({ behavior: "smooth", block: "nearest" })
      return
    }
    const cIdx = calculators.findIndex((c, i) => calcMatchesFocus(c, i, focus))
    if (cIdx >= 0) {
      const k = calculators[cIdx].calc_id || `calc-${cIdx}`
      calcRefs.current[k]?.scrollIntoView({ behavior: "smooth", block: "nearest" })
    }
  }, [focus, tables, calculators])

  if (tables.length === 0 && calculators.length === 0) {
    return (
      <div className="py-12 text-center text-[13px] text-muted">
        Chưa có bảng 2D hydrated hoặc phép tính Python Calculator nào trong câu trả lời này.
      </div>
    )
  }

  return (
    <div className="space-y-5">
      {/* Section 1: Python Financial Calculator Traces */}
      {calculators.length > 0 && (
        <div className="space-y-2.5">
          <div className="flex items-center gap-1.5 text-[11.5px] font-semibold uppercase tracking-[0.08em] text-muted">
            <Calculator className="h-3.5 w-3.5 text-gold" aria-hidden="true" />
            <span>Python Financial Calculator Traces ({calculators.length})</span>
          </div>

          {calculators.map((calc, idx) => {
            const key = calc.calc_id || `calc-${idx}`
            const highlighted = calcMatchesFocus(calc, idx, focus)
            const inputs = normalizeCalculatorInputs(calc.inputs)
            return (
              <div
                key={key}
                ref={(el) => {
                  calcRefs.current[key] = el
                }}
                className={`rounded-[9px] border p-3.5 space-y-2.5 transition-all ${
                  highlighted
                    ? "border-gold bg-gold/8 ring-2 ring-gold/25"
                    : "border-line bg-surface"
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <Pill tone="gold">
                      <span className="font-mono">{calc.calc_id || `CALC-${idx + 1}`}</span>
                    </Pill>
                    <span className="text-[13px] font-semibold text-ink truncate">
                      {calc.description || calc.label || "Phép tính tất định Python"}
                    </span>
                  </div>
                  <div className="font-mono text-[15px] font-bold text-teal tnum">
                    {calc.formatted}
                  </div>
                </div>

                <div className="rounded-[6px] border border-line bg-paper px-2.5 py-1.5 font-mono text-[11.5px] text-ink overflow-x-auto">
                  {calc.formula}
                </div>

                {inputs.length > 0 && (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                    {inputs.map((inp) => (
                      <div
                        key={inp.name}
                        className="rounded-[6px] border border-line bg-soft/60 px-2.5 py-1.5 text-[11.5px]"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-mono font-medium text-secondary">{inp.name}</span>
                          <span className="font-mono font-semibold text-ink tnum">
                            {formatCellValue(inp.value)}
                          </span>
                        </div>
                        {(inp.table_id || inp.row || inp.source) && (
                          <div className="mt-0.5 text-[10.5px] text-muted truncate">
                            {[inp.row, inp.source, inp.table_id ? `tbl:${inp.table_id.slice(0, 8)}` : null]
                              .filter(Boolean)
                              .join(" · ")}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Section 2: Hydrated 2D Tables */}
      {tables.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-1.5 text-[11.5px] font-semibold uppercase tracking-[0.08em] text-muted">
            <Table2 className="h-3.5 w-3.5 text-mineral" aria-hidden="true" />
            <span>Bảng Tài chính Hydrated 2D ({tables.length})</span>
          </div>

          {tables.map((tbl, idx) => {
            const key = tbl.table_id || `tbl-${idx}`
            const highlighted = tableMatchesFocus(tbl, idx, focus)
            const headerRows = normalizeHeaders(tbl.headers)
            const bodyRows = Array.isArray(tbl.rows) ? tbl.rows.map(normalizeRow) : []
            const status = String(tbl.verification_status ?? "VERIFIED").toUpperCase()
            const isUnverified = status === "TABLE_UNVERIFIED"
            const statusTone = isUnverified ? "loss" : status === "PARTIAL" ? "warning" : "gain"

            return (
              <div
                key={key}
                ref={(el) => {
                  tableRefs.current[key] = el
                }}
                className={`rounded-[9px] border overflow-hidden transition-all ${
                  highlighted
                    ? "border-mineral bg-surface ring-2 ring-mineral/20"
                    : "border-line bg-surface"
                }`}
              >
                <div className="border-b border-line bg-soft/65 px-3.5 py-2.5 space-y-1.5">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="text-[13px] font-semibold text-ink">
                      <span className="font-mono text-mineral mr-1.5">#{idx + 1}</span>
                      {tbl.title}
                    </div>
                    <Pill tone={statusTone}>
                      {isUnverified ? (
                        <AlertOctagon className="h-3 w-3" aria-hidden="true" />
                      ) : (
                        <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                      )}
                      <span>{status}</span>
                    </Pill>
                  </div>

                  <div className="flex flex-wrap items-center gap-2 text-[11px] font-mono text-secondary">
                    {tbl.sub_doc_slot && <span>Slot: {tbl.sub_doc_slot}</span>}
                    {tbl.page_from != null && (
                      <span>
                        Trang: {tbl.page_from}
                        {tbl.page_to && tbl.page_to !== tbl.page_from ? `–${tbl.page_to}` : ""}
                      </span>
                    )}
                    <span>
                      Đơn vị: {tbl.unit_raw || tbl.unit || "—"} ({tbl.currency || "VND"})
                    </span>
                    {tbl.unit_multiplier != null && (
                      <span>×{tbl.unit_multiplier.toLocaleString("en-US")}</span>
                    )}
                    {tbl.sanity_error_ratio != null && (
                      <span>
                        Sai số tổng dọc: {(tbl.sanity_error_ratio * 100).toFixed(3)}%
                      </span>
                    )}
                  </div>

                  {isUnverified && (
                    <div className="rounded-[6px] border border-loss/35 bg-loss/10 px-2.5 py-1.5 text-[11.5px] text-loss">
                      Cảnh báo Fail-Closed: Bảng không đạt cổng kiểm định số học (Arithmetic Sanity Gate). Python Calculator bị chặn không tự nội suy từ bảng này.
                    </div>
                  )}
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-[12px]">
                    {headerRows.length > 0 && (
                      <thead>
                        {headerRows.map((hRow, hIdx) => (
                          <tr key={`${key}-hr-${hIdx}`} className="border-b border-line bg-paper">
                            {hRow.map((col, cIdx) => (
                              <th
                                key={`${key}-hc-${hIdx}-${cIdx}`}
                                className={`px-3 py-2 font-semibold text-ink whitespace-nowrap ${
                                  cIdx === 0 ? "text-left" : "text-right"
                                }`}
                              >
                                {col}
                              </th>
                            ))}
                          </tr>
                        ))}
                      </thead>
                    )}
                    <tbody className="divide-y divide-line">
                      {bodyRows.map((cells, rIdx) => (
                        <tr key={`${key}-r-${rIdx}`} className="hover:bg-soft/35">
                          {cells.map((cell, cIdx) => (
                            <td
                              key={`${key}-c-${rIdx}-${cIdx}`}
                              className={`px-3 py-1.5 ${
                                cIdx === 0
                                  ? "text-left font-medium text-ink"
                                  : "text-right font-mono tnum text-secondary whitespace-nowrap"
                              }`}
                            >
                              {formatCellValue(cell)}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {tbl.footnotes_text && (
                  <div className="border-t border-line bg-paper/75 px-3.5 py-2 text-[11.5px] text-secondary leading-relaxed">
                    <span className="font-semibold text-ink">Ghi chú bảng: </span>
                    {tbl.footnotes_text}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
