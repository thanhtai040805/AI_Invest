import type { Metadata } from "next";
import { Outfit, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { QueryProvider } from "@/providers/QueryProvider";
import { RealtimeProvider } from "@/providers/RealtimeProvider";
import { NotificationProvider } from "@/components/ui/NotificationProvider";

const outfit = Outfit({
  variable: "--font-outfit",
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700", "800"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "AIInvest — Hệ thống giao dịch thông minh",
  description:
    "Phân tích thị trường chứng khoán Việt Nam với trí tuệ nhân tạo. Dữ liệu thời gian thực, sàng lọc nâng cao và quản lý danh mục chuyên nghiệp.",
  keywords: "chứng khoán, đầu tư, AI, phân tích kỹ thuật, HOSE, HNX",
  openGraph: {
    title: "AIInvest — Hệ thống giao dịch thông minh",
    description: "Phân tích thị trường chứng khoán Việt Nam với AI.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="vi"
      className={`${outfit.variable} ${jetbrainsMono.variable} h-full antialiased`}
    >
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <meta name="theme-color" content="#09090a" />
        <link
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="grain-overlay min-h-full flex flex-col bg-[var(--color-background)] text-[var(--color-on-surface)]">
        <QueryProvider>
          <RealtimeProvider>
            <NotificationProvider>{children}</NotificationProvider>
          </RealtimeProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
