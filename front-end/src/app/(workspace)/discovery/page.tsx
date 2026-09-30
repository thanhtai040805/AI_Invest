"use client"

import { useState, type FormEvent } from "react"
import { Page } from "@/components/Shell"
import { Link } from "@/lib/router"
import { Button, EmptyState, Panel, PanelHead, PercentChange, fmt } from "@/components/ui"
import { screenerApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import type { ApiMarketStock } from "@/types"

type NumericFilterKey =
  | "peMin" | "peMax" | "pbMin" | "pbMax" | "roeMin" | "roeMax"
  | "rsiMin" | "rsiMax" | "deMin" | "deMax"
  | "marketCapMin" | "marketCapMax" | "volumeMin"

type FilterDraft = Record<NumericFilterKey, string> & {
  sort: string
  sortDir: "asc" | "desc"
}

type ScreenerPreset = {
  id: string
  name: string
  filters: Record<string, unknown>
}

type ScreenerStock = ApiMarketStock & {
  [key: string]: unknown
  pe?: unknown
  pb?: unknown
  roe?: unknown
  rsi?: unknown
  de?: unknown
}

type ScreenerResponse = {
  stocks?: ScreenerStock[]
  total?: number
  source?: string
  error?: string
}

type ScreenerResults = {
  stocks: ScreenerStock[]
  total: number
}

const EMPTY_DRAFT: FilterDraft = {
  peMin: "",
  peMax: "",
  pbMin: "",
  pbMax: "",
  roeMin: "",
  roeMax: "",
  rsiMin: "",
  rsiMax: "",
  deMin: "",
  deMax: "",
  marketCapMin: "",
  marketCapMax: "",
  volumeMin: "",
  sort: "changePercent",
  sortDir: "desc",
}

const RANGE_FIELDS: { label: string; min: NumericFilterKey; max: NumericFilterKey; unit?: string }[] = [
  { label: "P/E", min: "peMin", max: "peMax" },
  { label: "P/B", min: "pbMin", max: "pbMax" },
  { label: "ROE", min: "roeMin", max: "roeMax", unit: "%" },
  { label: "RSI", min: "rsiMin", max: "rsiMax" },
  { label: "Nợ / vốn chủ", min: "deMin", max: "deMax" },
  { label: "Vốn hóa", min: "marketCapMin", max: "marketCapMax", unit: "nghìn tỷ ₫" },
]

function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function formatMetric(value: unknown, digits = 2, suffix = ""): string {
  const number = finiteNumber(value)
  return number === null
    ? "—"
    : new Intl.NumberFormat("vi-VN", { maximumFractionDigits: digits }).format(number) + suffix
}

function errorText(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) return error.message
  if (error && typeof error === "object" && "message" in error && typeof error.message === "string") {
    return error.message
  }
  return fallback
}

function draftFromFilters(filters: Record<string, unknown>): FilterDraft {
  const draft = { ...EMPTY_DRAFT }
  for (const key of Object.keys(EMPTY_DRAFT) as (keyof FilterDraft)[]) {
    if (key === "sort" || key === "sortDir") continue
    const value = finiteNumber(filters[key])
    if (value !== null) {
      draft[key] = key.startsWith("marketCap") ? String(value / 1_000_000_000_000) : String(value)
    }
  }
  if (typeof filters.sort === "string") draft.sort = filters.sort
  if (filters.sortDir === "asc" || filters.sortDir === "desc") draft.sortDir = filters.sortDir
  return draft
}

function buildFilters(draft: FilterDraft): { filters: Record<string, unknown>; error?: string } {
  const filters: Record<string, unknown> = {
    sort: draft.sort,
    sortDir: draft.sortDir,
  }
  for (const key of Object.keys(EMPTY_DRAFT) as (keyof FilterDraft)[]) {
    if (key === "sort" || key === "sortDir") continue
    const raw = draft[key].trim()
    if (!raw) continue
    const value = Number(raw)
    if (!Number.isFinite(value)) return { filters, error: "Các ngưỡng lọc phải là số hợp lệ." }
    filters[key] = key.startsWith("marketCap") ? value * 1_000_000_000_000 : value
  }

  for (const field of RANGE_FIELDS) {
    const min = finiteNumber(draft[field.min])
    const max = finiteNumber(draft[field.max])
    if (min !== null && max !== null && min > max) {
      return { filters, error: "Ngưỡng thấp nhất của " + field.label + " không thể lớn hơn ngưỡng cao nhất." }
    }
  }

  return { filters }
}

function hasCriteria(filters: Record<string, unknown>): boolean {
  return Object.keys(filters).some((key) => key !== "sort" && key !== "sortDir")
}

export default function Discovery() {
  const [draft, setDraft] = useState<FilterDraft>(EMPTY_DRAFT)
  const [results, setResults] = useState<ScreenerResults | null>(null)
  const [activeFilters, setActiveFilters] = useState<Record<string, unknown>>({})
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [savingPreset, setSavingPreset] = useState(false)
  const [presetName, setPresetName] = useState("")
  const [message, setMessage] = useState("")

  const builtinPresets = useResource<ScreenerPreset[]>(
    async () => (await screenerApi.presets().catch(() => [])) as ScreenerPreset[],
    [],
  )
  const savedPresets = useResource<ScreenerPreset[]>(
    async () => (await screenerApi.myPresets().catch(() => [])) as ScreenerPreset[],
    [],
  )

  const runScreener = async (filters: Record<string, unknown>) => {
    setLoading(true)
    setMessage("")
    setResults(null)
    setActiveFilters(filters)
    try {
      const response = await screenerApi.filter({
        ...filters,
        limit: 50,
        offset: 0,
      }) as ScreenerResponse
      if (response?.error) throw new Error(response.error)
      const stocks = Array.isArray(response?.stocks) ? response.stocks : []
      const total = finiteNumber(response?.total) ?? stocks.length
      setResults({ stocks, total })
    } catch (error) {
      setMessage(errorText(error, "Không thể tải kết quả sàng lọc."))
    } finally {
      setLoading(false)
    }
  }

  const applyDraft = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const built = buildFilters(draft)
    if (built.error) {
      setMessage(built.error)
      return
    }
    void runScreener(built.filters)
  }

  const loadMore = async () => {
    if (!results || loadingMore || results.stocks.length >= results.total) return
    setLoadingMore(true)
    setMessage("")
    try {
      const response = await screenerApi.filter({
        ...activeFilters,
        limit: 50,
        offset: results.stocks.length,
      }) as ScreenerResponse
      if (response?.error) throw new Error(response.error)
      const nextStocks = Array.isArray(response?.stocks) ? response.stocks : []
      setResults((current) => current
        ? { ...current, stocks: [...current.stocks, ...nextStocks] }
        : current)
    } catch (error) {
      setMessage(errorText(error, "Không thể tải thêm kết quả."))
    } finally {
      setLoadingMore(false)
    }
  }

  const loadPreset = (value: string) => {
    const [kind, id] = value.split(":", 2)
    const list = kind === "saved" ? savedPresets.data : builtinPresets.data
    const preset = list?.find((item) => item.id === id)
    if (!preset) return
    const filters = Object.fromEntries(Object.entries(preset.filters || {}).filter(([key]) => key !== "exchange"))
    setDraft(draftFromFilters(filters))
    void runScreener(filters)
  }

  const savePreset = async () => {
    const name = presetName.trim()
    if (!name) {
      setMessage("Nhập tên cho bộ lọc cần lưu.")
      return
    }
    const built = buildFilters(draft)
    if (built.error) {
      setMessage(built.error)
      return
    }
    if (!hasCriteria(built.filters)) {
      setMessage("Hãy chọn ít nhất một tiêu chí trước khi lưu bộ lọc.")
      return
    }

    setSavingPreset(true)
    setMessage("")
    try {
      await screenerApi.savePreset(name, built.filters)
      setPresetName("")
      await savedPresets.reload()
      setMessage("Đã lưu bộ lọc.")
    } catch (error) {
      setMessage(errorText(error, "Không thể lưu bộ lọc."))
    } finally {
      setSavingPreset(false)
    }
  }

  const presetValue = (kind: "builtin" | "saved", id: string) => kind + ":" + id

  return (
    <Page
      title="Khám phá Alpha"
      sub="Sàng lọc theo chỉ số tài chính và kỹ thuật có dữ liệu từ API."
    >
      <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4 items-start">
        <div className="space-y-4">
          <Panel>
            <PanelHead title="Bộ lọc cổ phiếu" sub="Các ngưỡng đều có thể để trống." />
            <form onSubmit={applyDraft} className="space-y-3 text-[12px]">
              <p className="rounded-[6px] bg-soft px-2.5 py-2 text-[11px] text-secondary">
                Phạm vi dữ liệu hiện có: HOSE.
              </p>

              {RANGE_FIELDS.map((field) => (
                <fieldset key={field.min}>
                  <legend className="mb-1 text-secondary">
                    {field.label}{field.unit ? " (" + field.unit + ")" : ""}
                  </legend>
                  <div className="grid grid-cols-2 gap-2">
                    <label>
                      <span className="sr-only">{field.label} thấp nhất</span>
                      <input
                        type="number"
                        step="any"
                        inputMode="decimal"
                        aria-label={field.label + " thấp nhất"}
                        placeholder="Từ"
                        value={draft[field.min]}
                        onChange={(event) => setDraft((current) => ({ ...current, [field.min]: event.target.value }))}
                        className="h-9 w-full rounded-[6px] border border-line bg-surface px-2.5 text-ink outline-none focus:border-mineral"
                      />
                    </label>
                    <label>
                      <span className="sr-only">{field.label} cao nhất</span>
                      <input
                        type="number"
                        step="any"
                        inputMode="decimal"
                        aria-label={field.label + " cao nhất"}
                        placeholder="Đến"
                        value={draft[field.max]}
                        onChange={(event) => setDraft((current) => ({ ...current, [field.max]: event.target.value }))}
                        className="h-9 w-full rounded-[6px] border border-line bg-surface px-2.5 text-ink outline-none focus:border-mineral"
                      />
                    </label>
                  </div>
                </fieldset>
              ))}

              <label className="block">
                <span className="text-secondary">Khối lượng tối thiểu (cổ phiếu)</span>
                <input
                  type="number"
                  min="0"
                  step="1"
                  inputMode="numeric"
                  value={draft.volumeMin}
                  onChange={(event) => setDraft((current) => ({ ...current, volumeMin: event.target.value }))}
                  className="mt-1 h-9 w-full rounded-[6px] border border-line bg-surface px-2.5 text-ink outline-none focus:border-mineral"
                />
              </label>

              <label className="block">
                <span className="text-secondary">Sắp xếp theo</span>
                <select
                  value={draft.sort}
                  onChange={(event) => setDraft((current) => ({ ...current, sort: event.target.value }))}
                  className="mt-1 h-9 w-full rounded-[6px] border border-line bg-surface px-2.5 text-ink outline-none focus:border-mineral"
                >
                  <option value="changePercent">Biến động (%)</option>
                  <option value="roe">ROE (%)</option>
                  <option value="pe">P/E</option>
                  <option value="pb">P/B</option>
                  <option value="volume">Khối lượng</option>
                </select>
              </label>
              <label className="block">
                <span className="text-secondary">Thứ tự</span>
                <select
                  value={draft.sortDir}
                  onChange={(event) => setDraft((current) => ({ ...current, sortDir: event.target.value as "asc" | "desc" }))}
                  className="mt-1 h-9 w-full rounded-[6px] border border-line bg-surface px-2.5 text-ink outline-none focus:border-mineral"
                >
                  <option value="desc">Giảm dần</option>
                  <option value="asc">Tăng dần</option>
                </select>
              </label>
              <Button type="submit" variant="primary" className="w-full" disabled={loading}>
                {loading ? "Đang sàng lọc…" : "Áp dụng bộ lọc"}
              </Button>
              <p className="text-[11px] leading-relaxed text-muted">
                Mã thiếu dữ liệu chỉ bị loại khi bạn đặt ngưỡng cho chỉ số đó.
              </p>
            </form>
          </Panel>

          <Panel>
            <PanelHead title="Bộ lọc đã lưu" />
            <label className="block">
              <span className="sr-only">Chọn bộ lọc mẫu hoặc đã lưu</span>
              <select
                defaultValue=""
                onChange={(event) => {
                  if (event.target.value) loadPreset(event.target.value)
                  event.target.value = ""
                }}
                className="h-9 w-full rounded-[6px] border border-line bg-surface px-2.5 text-[12px] text-ink outline-none focus:border-mineral"
              >
                <option value="">Chọn bộ lọc…</option>
                {Boolean(builtinPresets.data?.length) && (
                  <optgroup label="Bộ lọc mẫu">
                    {builtinPresets.data?.map((preset) => (
                      <option key={preset.id} value={presetValue("builtin", preset.id)}>{preset.name}</option>
                    ))}
                  </optgroup>
                )}
                {Boolean(savedPresets.data?.length) && (
                  <optgroup label="Bộ lọc của tôi">
                    {savedPresets.data?.map((preset) => (
                      <option key={preset.id} value={presetValue("saved", preset.id)}>{preset.name}</option>
                    ))}
                  </optgroup>
                )}
              </select>
            </label>
            <div className="mt-3 space-y-2">
              <label className="block">
                <span className="text-secondary text-[12px]">Tên bộ lọc</span>
                <input
                  value={presetName}
                  onChange={(event) => setPresetName(event.target.value)}
                  maxLength={80}
                  placeholder="Ví dụ: ROE cao, P/E thấp"
                  className="mt-1 h-9 w-full rounded-[6px] border border-line bg-surface px-2.5 text-[12px] text-ink outline-none focus:border-mineral"
                />
              </label>
              <Button variant="secondary" className="w-full" onClick={() => void savePreset()} disabled={savingPreset}>
                {savingPreset ? "Đang lưu…" : "Lưu bộ lọc hiện tại"}
              </Button>
            </div>
          </Panel>
        </div>

        <Panel flush>
          <div className="px-5 pt-4 pb-2">
            <PanelHead
              title="Kết quả sàng lọc"
              sub={results ? results.stocks.length.toLocaleString("vi-VN") + " / " + results.total.toLocaleString("vi-VN") + " mã" : "Chưa chạy bộ lọc"}
            />
          </div>

          {message && (
            <p role="status" className="mx-5 mb-3 rounded-lg border border-line bg-soft px-3 py-2 text-[12px] text-secondary">
              {message}
            </p>
          )}

          {loading ? (
            <div className="px-5 py-12 text-center text-[13px] text-muted" role="status">Đang tải dữ liệu sàng lọc…</div>
          ) : !results ? (
            <EmptyState title="Chưa có kết quả" body="Chọn tiêu chí rồi áp dụng để nhận danh sách từ bộ sàng lọc." />
          ) : results.stocks.length === 0 ? (
            <EmptyState title="Không tìm thấy mã phù hợp" body="Thử nới lỏng một hoặc nhiều ngưỡng lọc." />
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[780px] text-[12px]">
                  <thead>
                    <tr className="border-y border-line text-[10px] uppercase tracking-wide text-muted">
                      <th className="px-5 py-2.5 text-left font-medium">Mã / doanh nghiệp</th>
                      <th className="px-3 py-2.5 text-right font-medium">Giá</th>
                      <th className="px-3 py-2.5 text-right font-medium">Biến động</th>
                      <th className="px-3 py-2.5 text-right font-medium">P/E</th>
                      <th className="px-3 py-2.5 text-right font-medium">P/B</th>
                      <th className="px-3 py-2.5 text-right font-medium">ROE</th>
                      <th className="px-3 py-2.5 text-right font-medium">RSI</th>
                      <th className="px-5 py-2.5 text-right font-medium">Khối lượng</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {results.stocks.map((stock, index) => {
                      const symbol = String(stock.symbol || "")
                      const change = finiteNumber(stock.changePercent ?? stock.change_pct ?? stock.changePct)
                      const price = finiteNumber(stock.price ?? stock.matchPrice)
                      const volume = finiteNumber(stock.volume)
                      return (
                        <tr key={symbol || index} className="hover:bg-soft/50">
                          <td className="px-5 py-3">
                            <Link to={"/stock/" + encodeURIComponent(symbol)} className="font-mono font-semibold text-ink hover:underline">
                              {symbol || "—"}
                            </Link>
                            <div className="mt-0.5 max-w-[210px] truncate text-[11px] text-muted">{String(stock.name || "—")}</div>
                          </td>
                          <td className="px-3 py-3 text-right font-mono text-ink tnum">{price === null ? "—" : fmt(price)}</td>
                          <td className="px-3 py-3 text-right">{change === null ? "—" : <PercentChange value={change} arrow={false} />}</td>
                          <td className="px-3 py-3 text-right font-mono text-secondary tnum">{formatMetric(stock.pe)}</td>
                          <td className="px-3 py-3 text-right font-mono text-secondary tnum">{formatMetric(stock.pb)}</td>
                          <td className="px-3 py-3 text-right font-mono text-secondary tnum">{formatMetric(stock.roe, 2, "%")}</td>
                          <td className="px-3 py-3 text-right font-mono text-secondary tnum">{formatMetric(stock.rsi)}</td>
                          <td className="px-5 py-3 text-right font-mono text-secondary tnum">{volume === null ? "—" : fmt(volume)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              {results.stocks.length < results.total && (
                <div className="border-t border-line p-4 text-center">
                  <Button variant="secondary" onClick={() => void loadMore()} disabled={loadingMore}>
                    {loadingMore ? "Đang tải…" : "Tải thêm mã"}
                  </Button>
                </div>
              )}
            </>
          )}
        </Panel>
      </div>
    </Page>
  )
}
