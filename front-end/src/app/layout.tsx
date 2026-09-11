import type { Metadata } from "next"
import { Geist, Geist_Mono, Source_Serif_4 } from "next/font/google"
import { Providers } from "@/components/providers"
import "./globals.css"
const sans = Geist({ subsets: ["latin", "latin-ext"], variable: "--font-geist" })
const mono = Geist_Mono({ subsets: ["latin", "latin-ext"], variable: "--font-geist-mono" })
const serif = Source_Serif_4({ subsets: ["latin", "vietnamese"], variable: "--font-source-serif" })
export const metadata: Metadata = { title: "AIInvest — Trí tuệ đầu tư Việt Nam", description: "Nền tảng nghiên cứu và quản trị đầu tư dựa trên bằng chứng." }
export default function RootLayout({ children }: { children: React.ReactNode }) { return <html lang="vi" className={`${sans.variable} ${mono.variable} ${serif.variable}`}><body><Providers>{children}</Providers></body></html> }
