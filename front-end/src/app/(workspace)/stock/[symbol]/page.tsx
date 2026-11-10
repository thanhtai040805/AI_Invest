"use client"

import { useParams } from "next/navigation"
import { Page } from "@/components/Shell"
import { StockDetailContent } from "@/components/StockDetailView"
import { Link } from "@/lib/router"
import { Button } from "@/components/ui"

export default function StockDetailPage() {
  const { symbol } = useParams<{ symbol: string }>()
  const cleanSymbol = (symbol || "HPG").toUpperCase()

  return (
    <Page
      title={`Chi tiết mã ${cleanSymbol}`}
      sub="Cấu trúc giá, đồ thị kỹ thuật, sổ lệnh bước giá, dòng tiền VSA và giao dịch khối ngoại."
      actions={
        <Link to="/markets">
          <Button variant="secondary">← Quay lại Bảng giá HOSE</Button>
        </Link>
      }
    >
      <StockDetailContent symbol={cleanSymbol} />
    </Page>
  )
}
