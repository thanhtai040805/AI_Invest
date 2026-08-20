import { Inter, JetBrains_Mono } from "next/font/google";

// Chữ thân & tiêu đề thống nhất không chân (phong cách Notion/Codex); tiêu đề dùng .font-display giãn chữ hẹp để phân biệt
export const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

// Mã nguồn / dữ liệu
export const jbmono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jbmono",
  display: "swap",
});

export const fontVars = `${inter.variable} ${jbmono.variable}`;
