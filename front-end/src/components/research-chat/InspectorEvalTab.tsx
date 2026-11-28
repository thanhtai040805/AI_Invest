"use client"

import { useState, type FormEvent } from "react"
import { CheckCircle2, RefreshCw, ShieldCheck, XCircle } from "lucide-react"
import { Button, Pill } from "@/components/ui"
import {
  researchChatApi,
  type EvalSummaryResponse,
  type EvalVerdict,
} from "@/lib/api/researchChat"

interface InspectorEvalTabProps {
  messageId: string | null
  evalSummary: EvalSummaryResponse | null
  onRefreshSummary: () => Promise<void> | void
}

function GateBar({
  code,
  label,
  valueText,
  ratio,
  passed,
  targetText,
}: {
  code: string
  label: string
  valueText: string
  ratio: number
  passed: boolean
  targetText: string
}) {
  const clamped = Math.max(0, Math.min(1, ratio))
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2 text-[12px]">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="font-mono font-semibold text-mineral">{code}</span>
          <span className="text-ink truncate">{label}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0 font-mono tnum">
          <span className={passed ? "text-gain font-semibold" : "text-ink font-semibold"}>
            {valueText}
          </span>
          <span className="text-muted text-[11px]">/ {targetText}</span>
          {passed ? (
            <CheckCircle2 className="h-3.5 w-3.5 text-gain" aria-hidden="true" />
          ) : (
            <XCircle className="h-3.5 w-3.5 text-muted" aria-hidden="true" />
          )}
        </div>
      </div>
      <div className="h-1.5 w-full rounded-full bg-soft overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-300 ${
            passed ? "bg-gain" : "bg-mineral"
          }`}
          style={{ width: `${Math.round(clamped * 100)}%` }}
        />
      </div>
    </div>
  )
}

export function InspectorEvalTab({
  messageId,
  evalSummary,
  onRefreshSummary,
}: InspectorEvalTabProps) {
  const [verdict, setVerdict] = useState<EvalVerdict>("PASS")
  const [citationOk, setCitationOk] = useState(true)
  const [tableCalcOk, setTableCalcOk] = useState(true)
  const [graphContextOk, setGraphContextOk] = useState(true)
  const [hallucinationFlag, setHallucinationFlag] = useState(false)
  const [notes, setNotes] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [submitMessage, setSubmitMessage] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!messageId) return
    setSubmitting(true)
    setSubmitError(null)
    setSubmitMessage(null)
    try {
      await researchChatApi.submitFeedback({
        messageId,
        verdict,
        citationOk,
        tableCalcOk,
        graphContextOk,
        hallucinationFlag,
        notes: notes.trim() || undefined,
      })
      setSubmitMessage("Đã lưu đánh giá vào sag_chat_eval_feedback.")
      setNotes("")
      await onRefreshSummary()
    } catch (err) {
      const msg =
        err && typeof err === "object" && "message" in err
          ? String((err as { message: unknown }).message)
          : "Không thể gửi đánh giá."
      setSubmitError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  const gates = evalSummary?.gates
  const g1 = gates?.g1CitationVerifiedRate ?? null
  const g2 = gates?.g2TableCalcVerifiedRate ?? null
  const g3 = gates?.g3AbstentionComplianceRate ?? null
  const g4 = gates?.g4PitZeroLeakCount ?? 0
  const g5 = gates?.g5HumanPassRate ?? null

  return (
    <div className="space-y-4">
      {/* Per-message Human Evaluation Form */}
      <form
        onSubmit={handleSubmit}
        className="rounded-[9px] border border-line bg-surface p-3.5 space-y-3"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-[13px] font-semibold text-ink">
              Chấm điểm Kiểm nghiệm Câu trả lời
            </h3>
            <p className="text-[11.5px] text-muted">
              {messageId
                ? `Message ID: ${messageId.slice(0, 12)}…`
                : "Hãy chọn hoặc chạy một câu trả lời AI để chấm điểm."}
            </p>
          </div>
          {messageId && <Pill tone="mineral">Sẵn sàng chấm</Pill>}
        </div>

        <div className="grid grid-cols-3 gap-2">
          {(
            [
              { value: "PASS", label: "PASS", activeCls: "border-gain bg-gain/12 text-gain" },
              {
                value: "NEEDS_FIX",
                label: "NEEDS_FIX",
                activeCls: "border-warning bg-warning/12 text-warning",
              },
              { value: "FAIL", label: "FAIL", activeCls: "border-loss bg-loss/12 text-loss" },
            ] as const
          ).map((opt) => {
            const active = verdict === opt.value
            return (
              <button
                key={opt.value}
                type="button"
                disabled={!messageId || submitting}
                onClick={() => setVerdict(opt.value)}
                className={`h-8 rounded-[7px] border font-mono text-[12px] font-semibold transition-colors ${
                  active
                    ? opt.activeCls
                    : "border-line bg-paper text-secondary hover:text-ink"
                } disabled:opacity-50`}
              >
                {opt.label}
              </button>
            )
          })}
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[12px]">
          <label className="flex items-center gap-2 rounded-[6px] border border-line bg-paper px-2.5 py-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={citationOk}
              disabled={!messageId || submitting}
              onChange={(e) => setCitationOk(e.target.checked)}
              className="accent-teal"
            />
            <span className="text-ink">Trích dẫn đúng nguyên văn (quote_hash)</span>
          </label>
          <label className="flex items-center gap-2 rounded-[6px] border border-line bg-paper px-2.5 py-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={tableCalcOk}
              disabled={!messageId || submitting}
              onChange={(e) => setTableCalcOk(e.target.checked)}
              className="accent-teal"
            />
            <span className="text-ink">Bảng 2D & Python Calc chính xác</span>
          </label>
          <label className="flex items-center gap-2 rounded-[6px] border border-line bg-paper px-2.5 py-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={graphContextOk}
              disabled={!messageId || submitting}
              onChange={(e) => setGraphContextOk(e.target.checked)}
              className="accent-teal"
            />
            <span className="text-ink">Đồ thị 3 Tầng (Vĩ mô / Peer) hợp lý</span>
          </label>
          <label className="flex items-center gap-2 rounded-[6px] border border-line bg-paper px-2.5 py-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={hallucinationFlag}
              disabled={!messageId || submitting}
              onChange={(e) => setHallucinationFlag(e.target.checked)}
              className="accent-loss"
            />
            <span className="text-loss font-medium">Phát hiện ảo giác / Rò rỉ PIT</span>
          </label>
        </div>

        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          disabled={!messageId || submitting}
          rows={2}
          placeholder="Ghi chú kiểm định (VD: sai đơn vị tỷ/triệu, sót thuyết minh, rò rỉ dữ liệu 2026)…"
          aria-label="Ghi chú kiểm định"
          className="w-full resize-none rounded-[7px] border border-line bg-paper px-3 py-2 text-[12.5px] text-ink placeholder:text-muted focus:border-mineral focus:outline-none disabled:opacity-50"
        />

        {submitMessage && (
          <div className="rounded-[6px] border border-gain/30 bg-gain/10 px-2.5 py-1.5 text-[12px] text-gain">
            {submitMessage}
          </div>
        )}
        {submitError && (
          <div className="rounded-[6px] border border-loss/30 bg-loss/10 px-2.5 py-1.5 text-[12px] text-loss">
            {submitError}
          </div>
        )}

        <div className="flex justify-end">
          <Button type="submit" variant="primary" size="sm" disabled={!messageId || submitting}>
            {submitting ? "Đang lưu…" : "Lưu kết quả chấm điểm"}
          </Button>
        </div>
      </form>

      {/* Live Exit Gate G1-G5 Progress */}
      <div className="rounded-[9px] border border-line bg-surface p-3.5 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5">
            <ShieldCheck className="h-4 w-4 text-teal" aria-hidden="true" />
            <h3 className="text-[13px] font-semibold text-ink">
              Cổng Kiểm Nghiệm Pha 1 → Pha 2 (Exit Gate G1–G5)
            </h3>
          </div>
          <button
            type="button"
            onClick={() => void onRefreshSummary()}
            className="inline-flex items-center gap-1 text-[11.5px] text-mineral hover:text-ink"
          >
            <RefreshCw className="h-3 w-3" aria-hidden="true" />
            <span>Làm mới</span>
          </button>
        </div>

        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="rounded-[7px] border border-line bg-paper px-2 py-2">
            <div className="text-[10px] uppercase tracking-wider text-muted">Phiên chat</div>
            <div className="font-mono text-[15px] font-semibold text-ink tnum">
              {evalSummary?.totalSessions ?? 0}
            </div>
          </div>
          <div className="rounded-[7px] border border-line bg-paper px-2 py-2">
            <div className="text-[10px] uppercase tracking-wider text-muted">Câu trả lời AI</div>
            <div className="font-mono text-[15px] font-semibold text-ink tnum">
              {evalSummary?.totalAssistantMessages ?? 0}
            </div>
          </div>
          <div className="rounded-[7px] border border-line bg-paper px-2 py-2">
            <div className="text-[10px] uppercase tracking-wider text-muted">Lượt chấm</div>
            <div className="font-mono text-[15px] font-semibold text-ink tnum">
              {evalSummary?.feedbackCount ?? 0}
            </div>
          </div>
        </div>

        <div className="space-y-2.5 pt-1">
          <GateBar
            code="G1"
            label="Khớp quote_hash nguyên văn"
            valueText={g1 != null ? `${(g1 * 100).toFixed(1)}%` : "—"}
            ratio={g1 ?? 0}
            passed={(g1 ?? 0) >= 0.98}
            targetText="≥ 98%"
          />
          <GateBar
            code="G2"
            label="Chính xác Bảng 2D & Python Calc"
            valueText={g2 != null ? `${(g2 * 100).toFixed(1)}%` : "—"}
            ratio={g2 ?? 0}
            passed={(g2 ?? 0) >= 0.98}
            targetText="≥ 98%"
          />
          <GateBar
            code="G3"
            label="Tuân thủ từ chối trung thực (Abstention)"
            valueText={g3 != null ? `${(g3 * 100).toFixed(1)}%` : "—"}
            ratio={g3 ?? 0}
            passed={(g3 ?? 0) >= 1}
            targetText="100%"
          />
          <GateBar
            code="G4"
            label="Rò rỉ tương lai PIT (Zero Leak)"
            valueText={`${g4} lỗi`}
            ratio={g4 === 0 && (evalSummary?.totalAssistantMessages ?? 0) > 0 ? 1 : 0}
            passed={g4 === 0 && (evalSummary?.totalAssistantMessages ?? 0) > 0}
            targetText="0 lỗi"
          />
          <GateBar
            code="G5"
            label="Tỷ lệ PASS đánh giá người dùng"
            valueText={g5 != null ? `${(g5 * 100).toFixed(1)}%` : "—"}
            ratio={g5 ?? 0}
            passed={(g5 ?? 0) >= 0.9}
            targetText="≥ 90%"
          />
        </div>
      </div>
    </div>
  )
}
