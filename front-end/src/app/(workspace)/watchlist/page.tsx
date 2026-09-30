"use client"

import { useState, useMemo } from "react"
import { Page } from "@/components/Shell"
import { Link } from "@/lib/router"
import { Button, EmptyState, Panel, PanelHead, PercentChange, fmt } from "@/components/ui"
import { workspaceApi, marketApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import { useRealtimeMarket } from "@/lib/use-realtime"
import type { ApiMarketStock } from "@/types"

interface WatchlistData {
  id: string
  name: string
  symbols: string[]
  createdAt?: string
}

export default function WatchlistPage() {
  const [newSymbol, setNewSymbol] = useState("")
  const [isAdding, setIsAdding] = useState(false)
  const [saving, setSaving] = useState(false)
  const [actionMessage, setActionMessage] = useState("")

  const watchlistsRes = useResource(() => workspaceApi.watchlists().catch(() => []), [])
  const snapshotRes = useResource(() => marketApi.snapshot().catch(() => null), [])
  const { snapshot } = useRealtimeMarket()

  const rawWatchlists = (watchlistsRes.data as WatchlistData[]) || []
  const stockItems = (snapshot?.stocks ?? snapshotRes.data?.stocks ?? []) as ApiMarketStock[]

  const activeWatchlist = rawWatchlists[0] || {
    id: "default",
    name: "Theo dõi chính",
    symbols: [],
  }

  const trackedStocks = useMemo(() => {
    const bySymbol = new Map(stockItems.map((stock: ApiMarketStock) => [stock.symbol, stock]))
    return activeWatchlist.symbols.map((symbol) => ({ symbol, stock: bySymbol.get(symbol) }))
  }, [activeWatchlist.symbols, stockItems])

  const handleAddSymbol = async () => {
    if (saving) return
    const sym = newSymbol.trim().toUpperCase()
    if (!/^[A-Z0-9.-]{1,16}$/.test(sym)) {
      setActionMessage("Nhập mã gồm 1–16 ký tự chữ, số, dấu chấm hoặc gạch ngang.")
      return
    }
    if (activeWatchlist.symbols.includes(sym)) {
      setActionMessage(`${sym} đã có trong danh sách.`)
      return
    }

    setSaving(true)
    setActionMessage("")
    try {
      const updatedSymbols = [...activeWatchlist.symbols, sym]
      if (activeWatchlist.id === "default") await workspaceApi.createWatchlist({ name: "Theo dõi chính", symbols: updatedSymbols })
      else await workspaceApi.updateWatchlist(activeWatchlist.id, { symbols: updatedSymbols })
      setActionMessage(`Đã thêm ${sym} vào danh sách theo dõi.`)
      setNewSymbol("")
      setIsAdding(false)
      await watchlistsRes.reload()
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "Không thể cập nhật danh sách theo dõi.")
    } finally {
      setSaving(false)
    }
  }

  const handleRemoveSymbol = async (sym: string) => {
    if (saving) return
    const updatedSymbols = activeWatchlist.symbols.filter((s) => s !== sym)
    setSaving(true)
    setActionMessage("")
    try {
      if (activeWatchlist.id !== "default") await workspaceApi.updateWatchlist(activeWatchlist.id, { symbols: updatedSymbols })
      await watchlistsRes.reload()
      setActionMessage(`Đã xóa ${sym} khỏi danh sách theo dõi.`)
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "Không thể cập nhật danh sách theo dõi.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Page
      title={activeWatchlist.name}
      sub="Danh mục các mã cổ phiếu đang được hệ thống giám sát và đối soát liên tục."
      actions={
        <div className="flex items-center gap-2">
          {isAdding ? (
            <div className="flex items-center gap-1.5">
              <input
                type="text"
                placeholder="Nhập mã CP (e.g. VHM)..."
                value={newSymbol}
                onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
                className="h-8 px-3 rounded-[6px] border border-line bg-surface text-[12px] font-mono outline-none focus:border-mineral w-36"
                onKeyDown={(e) => e.key === "Enter" && handleAddSymbol()}
                autoFocus
              />
              <Button variant="primary" onClick={() => void handleAddSymbol()} disabled={saving}>{saving ? "Đang lưu…" : "Lưu"}</Button>
              <Button variant="secondary" onClick={() => setIsAdding(false)} disabled={saving}>Hủy</Button>
            </div>
          ) : (
            <Button variant="primary" onClick={() => setIsAdding(true)}>+ Thêm mã cổ phiếu</Button>
          )}
        </div>
      }
    >
      {actionMessage && <p role="status" className="mb-3 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-secondary">{actionMessage}</p>}
      <Panel flush>
        <div className="px-5 pt-4 pb-2">
          <PanelHead
            title="Bảng giá & Chỉ số giám sát"
            sub={`${trackedStocks.length} mã đang theo dõi · HOSE · Cập nhật khi nhận tick DNSE`}
          />
        </div>

        {trackedStocks.length === 0 ? (
          <div className="p-8">
            <EmptyState
              title="Danh mục theo dõi đang trống"
              body="Bấm vào nút '+ Thêm mã cổ phiếu' ở trên hoặc mở Discovery để bắt đầu thêm các mã tiềm năng."
              action={
                <Link to="/discovery">
                  <Button variant="primary">Khám phá Discovery</Button>
                </Link>
              }
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="text-[11px] uppercase tracking-wide text-muted border-y border-line">
                  <th className="py-2.5 px-5 text-left font-medium">Mã CP</th>
                  <th className="py-2.5 px-3 text-right font-medium">Thị giá</th>
                  <th className="py-2.5 px-3 text-right font-medium">Biến động</th>
                  <th className="py-2.5 px-3 text-right font-medium">Khối lượng</th>
                  <th className="py-2.5 pr-5 text-right font-medium">Thao tác</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {trackedStocks.map(({ symbol, stock }) => (
                  <tr key={symbol} className="hover:bg-soft/50 transition-colors">
                    <td className="py-3 px-5 font-mono font-bold">
                      <Link to={`/stock/${symbol}`} className="text-ink hover:underline">
                        {symbol}
                      </Link>
                    </td>
                    <td className="py-3 px-3 text-right font-mono font-semibold text-ink tnum">
                      {stock?.price ? fmt(stock.price) : "—"}
                    </td>
                    <td className="py-3 px-3 text-right">
                      {stock?.change_pct != null ? <PercentChange value={stock.change_pct} /> : "—"}
                    </td>
                    <td className="py-3 px-3 text-right font-mono text-secondary tnum">
                      {stock?.volume != null ? Number(stock.volume).toLocaleString("vi-VN") : "—"}
                    </td>
                    <td className="py-3 pr-5 text-right">
                      <button
                        onClick={() => void handleRemoveSymbol(symbol)}
                        disabled={saving}
                        className="text-[11.5px] text-secondary hover:text-loss transition-colors px-2 py-1 rounded hover:bg-loss/10 disabled:opacity-50"
                        title="Xóa khỏi watchlist"
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </Page>
  )
}
