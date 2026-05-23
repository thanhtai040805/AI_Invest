/**
 * Chart theme configuration for ECharts
 */
import * as echarts from "echarts/core";

export function getChartTheme(): "dark" | "light" {
  const isDark = localStorage.getItem("theme") === "dark" || 
    (!localStorage.getItem("theme") && window.matchMedia("(prefers-color-scheme: dark)").matches);
  return isDark ? "dark" : "light";
}

export function getChartColors() {
  const isDark = getChartTheme() === "dark";
  
  return {
    background: isDark ? "#1a1a1a" : "#ffffff",
    text: isDark ? "#e5e5e5" : "#333333",
    grid: isDark ? "#2a2a2a" : "#e5e5e5",
    line: isDark ? "#3b82f6" : "#2563eb",
    up: isDark ? "#22c55e" : "#16a34a",
    down: isDark ? "#ef4444" : "#dc2626",
  };
}

export interface ChartTheme {
  upColor: string;
  downColor: string;
  volumeUp: string;
  volumeDown: string;
  gridColor: string;
  textColor: string;
  bollColor: string;
}

export function getChartThemeEx(): ChartTheme {
  const isDark = getChartTheme() === "dark";
  return {
    upColor: isDark ? "#22c55e" : "#16a34a",
    downColor: isDark ? "#ef4444" : "#dc2626",
    volumeUp: isDark ? "#22c55e" : "#16a34a",
    volumeDown: isDark ? "#ef4444" : "#dc2626",
    gridColor: isDark ? "#2a2a2a" : "#e5e5e5",
    textColor: isDark ? "#e5e5e5" : "#333333",
    bollColor: isDark ? "#a855f7" : "#9333ea",
  };
}
