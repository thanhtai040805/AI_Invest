"use client"

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { displayDate, validDate } from "@/lib/financial-date"

type NavPoint = { date: string; value: number | null }
const money = (value: number) => `${value.toLocaleString("vi-VN", { maximumFractionDigits: 0 })} ₫`
const dateLabel = (value: number) => displayDate(new Date(value).toISOString().slice(0, 10))

export function NavLineChart({ points, missingDates = [] }: { points: readonly NavPoint[]; missingDates?: readonly string[] }) {
  const byDate = new Map<string, NavPoint>()
  // Only explicitly missing sessions break the curve; weekends are not missing observations.
  for (const date of missingDates) if (validDate(date)) byDate.set(date, { date, value: null })
  for (const point of points) {
    if (validDate(point.date)) byDate.set(point.date, {
      date: point.date,
      value: typeof point.value === "number" && Number.isFinite(point.value) ? point.value : null,
    })
  }
  const data = [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date))
    .map(point => ({ ...point, timestamp: Date.parse(`${point.date}T00:00:00Z`) }))
  const observed = data.filter((point): point is typeof point & { value: number } => point.value !== null)
  if (!observed.length) return <p className="grid h-64 place-items-center text-center text-sm text-muted">Chưa có lịch sử NAV trong khoảng đang xem.</p>

  const values = observed.map(point => point.value)
  const min = Math.min(...values), max = Math.max(...values)
  const divisor = Math.max(Math.abs(min), Math.abs(max)) >= 1e6 ? 1e6 : 1
  const padding = min === max ? Math.max(Math.abs(max) * 0.01, 1) : (max - min) * 0.12
  const first = observed[0], last = observed[observed.length - 1]

  return (
    <figure aria-label="Biểu đồ lịch sử NAV cuối ngày" className="min-w-0">
      <figcaption className="mb-3 flex flex-wrap items-baseline justify-between gap-2 text-xs text-muted">
        <span>Đơn vị: {divisor === 1e6 ? "triệu đồng" : "đồng Việt Nam"}</span>
        <span className="tabular-nums">{displayDate(first.date)} — {displayDate(last.date)}</span>
      </figcaption>
      <div className="h-64 w-full min-w-0 sm:h-72">
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <LineChart data={data} margin={{ top: 12, right: 12, bottom: 4, left: 0 }} accessibilityLayer>
            <CartesianGrid vertical={false} stroke="var(--color-line)" strokeDasharray="3 5" strokeOpacity={0.7} />
            <XAxis dataKey="timestamp" type="number" scale="utc" domain={["dataMin", "dataMax"]}
              tickFormatter={value => dateLabel(value).slice(0, 5)} tickCount={5} minTickGap={24}
              axisLine={false} tickLine={false} tickMargin={12} height={34}
              tick={{ fill: "var(--color-muted)", fontSize: 11 }} />
            <YAxis domain={[min - padding, max + padding]} tickFormatter={value => (value / divisor).toLocaleString("vi-VN", { maximumFractionDigits: 2 })} tickCount={4}
              axisLine={false} tickLine={false} tickMargin={10} width={72}
              tick={{ fill: "var(--color-muted)", fontSize: 11 }} />
            <Tooltip cursor={{ stroke: "var(--color-mineral)", strokeDasharray: "3 4", strokeOpacity: 0.5 }}
              labelFormatter={label => typeof label === "number" ? dateLabel(label) : ""}
              formatter={value => typeof value === "number" ? [money(value), "NAV cuối ngày"] : ["Chưa có dữ liệu", "NAV"]}
              contentStyle={{ background: "var(--color-surface)", border: "1px solid var(--color-line)", borderRadius: 10, padding: "10px 14px", fontSize: 12, boxShadow: "0 6px 20px #18201d0d" }}
              labelStyle={{ color: "var(--color-muted)", marginBottom: 4 }}
              itemStyle={{ color: "var(--color-ink)", padding: 0, fontVariantNumeric: "tabular-nums" }} />
            <Line dataKey="value" name="NAV cuối ngày" type="monotone" stroke="var(--color-teal)" strokeWidth={2.25}
              strokeLinecap="round" strokeLinejoin="round" connectNulls={false} isAnimationActive={false}
              activeDot={{ r: 5, fill: "var(--color-teal)", stroke: "var(--color-surface)", strokeWidth: 2.5 }}
              dot={({ cx, cy, index }) => {
                if (cx === undefined || cy === undefined || !Number.isFinite(cx) || !Number.isFinite(cy)) return null
                const isolated = data[index - 1]?.value == null && data[index + 1]?.value == null
                return <circle key={data[index].date} cx={cx} cy={cy} r={isolated || data[index].date === last.date ? 3.5 : 0}
                  fill="var(--color-teal)" stroke="var(--color-surface)" strokeWidth={2} />
              }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      {observed.length === 1 && <p className="mt-2 text-xs text-muted">Mới có một phiên NAV; chưa đủ lịch sử để thể hiện xu hướng.</p>}
    </figure>
  )
}
