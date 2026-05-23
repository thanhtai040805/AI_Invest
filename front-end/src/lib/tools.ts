/**
 * Tool name localization
 */

export function localizeToolName(tool: string): string {
  // Map tool names to Vietnamese
  const toolMap: Record<string, string> = {
    "market_analyst": "Phân tích thị trường",
    "fund_analyst": "Phân tích cơ bản",
    "bull_researcher": "Nhà nghiên cứu lạc quan",
    "bear_researcher": "Nhà nghiên cứu bi quan",
    "portfolio_manager": "Quản lý danh mục",
    "risk_gate": "Kiểm soát rủi ro",
  };

  return toolMap[tool] || tool;
}
