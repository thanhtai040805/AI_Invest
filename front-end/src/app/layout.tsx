import type { Metadata } from "next"
import { Be_Vietnam_Pro, JetBrains_Mono, Source_Serif_4 } from "next/font/google"
import { Providers } from "@/components/providers"
import "./globals.css"

const sans = Be_Vietnam_Pro({
  subsets: ["latin", "vietnamese"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-sans-vn",
})
const mono = JetBrains_Mono({
  subsets: ["latin", "vietnamese"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-mono-vn",
})
const serif = Source_Serif_4({
  subsets: ["latin", "vietnamese"],
  variable: "--font-source-serif",
})

export const metadata: Metadata = {
  title: "AIInvest — Trí tuệ đầu tư Việt Nam",
  description: "Nền tảng nghiên cứu và quản trị đầu tư dựa trên bằng chứng.",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi" className={`${sans.variable} ${mono.variable} ${serif.variable}`}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
