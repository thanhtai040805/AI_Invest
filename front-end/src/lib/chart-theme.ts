/**
 * Chart theme configuration for ECharts
 * Reads CSS custom properties from :root for dynamic theme support.
 */
import * as echarts from "echarts/core";

export interface ChartTheme {
  upColor: string;
  downColor: string;
  volumeUp: string;
  volumeDown: string;
  gridColor: string;
  textColor: string;
  bollColor: string;
  infoColor: string;
  warningColor: string;
  tooltipBg: string;
  tooltipBorder: string;
  tooltipText: string;
  axisColor: string;
  maColors: string[];
}

function css(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function hslToHex(hsl: string): string {
  if (!hsl) return "";
  const [h, s, l] = hsl.split(/\s+/).map(parseFloat);
  if (isNaN(h)) return "";
  const a = (s / 100) * Math.min(l / 100, 1 - l / 100);
  const f = (n: number) => {
    const k = (n + h / 30) % 12;
    const color = l / 100 - a * Math.max(Math.min(k - 3, 9 - k, 1), -1);
    return Math.round(255 * color).toString(16).padStart(2, "0");
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

function isChinese(): boolean {
  return (document.documentElement.lang || navigator.language || "").startsWith("zh");
}

const CSS_VARS = [
  "--success", "--danger", "--info", "--warning",
  "--chart-grid", "--chart-text", "--chart-axis",
] as const;

let _cache: ChartTheme | null = null;
let _cacheKey = "";

function buildTheme(): ChartTheme {
  const cn = isChinese();
  const isDark = document.documentElement.classList.contains("dark") ||
    (!document.documentElement.classList.contains("light") &&
     window.matchMedia("(prefers-color-scheme: dark)").matches);

  // Read CSS custom properties with fallback
  const successHex = hslToHex(css("--success")) || (isDark ? "#22c55e" : "#16a34a");
  const dangerHex = hslToHex(css("--danger")) || (isDark ? "#ef4444" : "#dc2626");
  const infoHex = hslToHex(css("--info")) || (isDark ? "#60a5fa" : "#2563eb");
  const warningHex = hslToHex(css("--warning")) || (isDark ? "#fbbf24" : "#d97706");
  const gridHex = hslToHex(css("--chart-grid")) || (isDark ? "#1e2433" : "#e5e7eb");
  const textHex = hslToHex(css("--chart-text")) || (isDark ? "#e5e5e5" : "#333333");
  const axisHex = hslToHex(css("--chart-axis")) || (isDark ? "#404040" : "#d4d4d4");

  // Locale-aware candlestick colors: Chinese = red up / green down
  const upHex = cn ? dangerHex : successHex;
  const downHex = cn ? successHex : dangerHex;

  return {
    upColor: upHex,
    downColor: downHex,
    volumeUp: upHex + "66",
    volumeDown: downHex + "66",
    gridColor: gridHex,
    textColor: textHex,
    axisColor: axisHex,
    bollColor: isDark ? "#a855f7" : "#9333ea",
    infoColor: infoHex,
    warningColor: warningHex,
    maColors: [warningHex, "#8b5cf6", infoHex],
    tooltipBg: isDark ? "rgba(10,14,22,0.92)" : "rgba(255,255,255,0.96)",
    tooltipBorder: isDark ? "#1e2433" : "#e5e7eb",
    tooltipText: isDark ? "#d1d5db" : "#374151",
  };
}

export function getChartTheme(): ChartTheme {
  const key = `${document.documentElement.className}|${document.documentElement.lang || navigator.language}`;
  if (_cache && _cacheKey === key) return _cache;
  _cache = buildTheme();
  _cacheKey = key;
  return _cache;
}
