// Start `npm run dev -- --port 3108`, then run:
// npx --package @playwright/cli playwright-cli -s=portfolio-check open http://localhost:3108
// npx --package @playwright/cli playwright-cli -s=portfolio-check run-code --filename scripts/check-portfolio-thesis-navigation.js
// eslint-disable-next-line @typescript-eslint/no-unused-expressions -- Playwright CLI evaluates this file as a function.
async page => {
  const base = "http://localhost:3108"
  const assert = (condition, message) => { if (!condition) throw new Error(message) }
  const snapshot = {
    summary: { accountId: "navigation-check", nav: 100000, cash: 80000, totalCost: 20000, openingCash: 100000, realizedPnl: 0, unrealizedPnl: 0, totalPnl: 0, totalReturnPct: 0, ledgerComplete: true, dailyBaseline: 100000, dailyPnL: 0, dailyPnLPercent: 0, valuedAt: "2026-10-05T08:00:00Z", stalePrices: [] },
    positions: [{ id: "hpg", symbol: "HPG", quantity: 2, avgPrice: 10000, currentPrice: 10000, marketValue: 20000, costBasis: 20000, pnl: 0, pnlPercent: 0, weight: 20, priceAsOf: "2026-10-05T08:00:00Z", priceSource: "test", stale: true }],
    performance: { equityCurve: [], asOf: null }, risks: { sharpe: null, alpha: null, beta: null, maxDrawdown: null, message: "" }, orders: [],
  }
  const theses = [
    { thesis_id: "fpt", ticker: "FPT", generated_at: "2026-10-05T08:00:00Z", analysis_date: "2026-10-05", entry_price_estimated: 50000 },
    { thesis_id: "hpg-old", ticker: "HPG", generated_at: "2026-10-02T08:00:00Z", analysis_date: "2026-10-02", entry_price_estimated: 9000 },
    { thesis_id: "hpg-latest", ticker: "HPG", generated_at: "2026-10-05T08:00:00Z", analysis_date: "2026-10-05", entry_price_estimated: 11000 },
  ]
  await page.addInitScript(() => localStorage.setItem("aiinvest_access_token", "navigation-check"))
  await page.route(/\/api\//, async route => {
    const url = new URL(route.request().url())
    let json = {}
    if (url.pathname.endsWith("/auth/me")) json = { id: "navigation-check", email: "check@example.invalid" }
    else if (url.pathname.endsWith("/portfolio/snapshot") || url.pathname.endsWith("/agent/portfolio")) json = snapshot
    else if (url.pathname.endsWith("/workspace/agent")) {
      const date = url.searchParams.get("date")
      json = { theses: theses.filter(row => !date || row.analysis_date === date), counterTheses: [], resolutions: [], decisions: [], positionHealth: [], logs: [], dates: ["2026-10-05", "2026-10-02"] }
    }
    await route.fulfill({ json })
  })
  await page.routeWebSocket(/socket\.io/, socket => socket.close())
  await page.setViewportSize({ width: 1440, height: 1000 })

  await page.goto(`${base}/portfolio`)
  const row = page.getByRole("row").filter({ has: page.getByRole("link", { name: "HPG", exact: true }) })
  await row.waitFor()
  const cell = row.locator("td").nth(4)
  await cell.scrollIntoViewIfNeeded()
  const box = await cell.boundingBox()
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
  await page.waitForURL("**/agent?ticker=HPG#investment-thesis")
  const section = page.locator("#investment-thesis")
  await section.getByText("11.000 ₫", { exact: true }).waitFor()
  assert(await page.getByRole("button", { name: /^HPG/ }).getAttribute("aria-pressed") === "true", "The clicked holding must be selected")
  assert(await section.getByText("9.000 ₫", { exact: true }).count() === 0, "Show the latest thesis, not an older version")
  await page.waitForFunction(() => { const top = document.getElementById("investment-thesis").getBoundingClientRect().top; return top >= 0 && top < 100 })

  await page.goto(`${base}/portfolio`)
  await page.getByRole("link", { name: "HPG", exact: true }).click()
  await page.waitForURL("**/stock/HPG")

  await page.goto(`${base}/portfolio`)
  const thesisLink = page.getByRole("link", { name: /Xem luận điểm đầu tư HPG/ })
  await thesisLink.focus()
  await page.keyboard.press("Enter")
  await page.waitForURL("**/agent?ticker=HPG#investment-thesis")
  await section.getByText("11.000 ₫", { exact: true }).waitFor()

  await page.goto(`${base}/agent?ticker=ZZZ#investment-thesis`)
  await section.getByText(/Chưa có luận điểm/).waitFor()
  assert(await page.getByRole("button", { name: /^ZZZ/ }).getAttribute("aria-pressed") === "true", "A missing thesis must not select another ticker")
  assert(await section.getByText("11.000 ₫", { exact: true }).count() === 0, "Do not substitute another stock's thesis")

  await page.goto(`${base}/agent`)
  assert(!(await page.getByRole("button", { name: "Ngày phân tích", exact: true }).textContent()).includes("Tất cả ngày"), "Ordinary AI War visits retain the analysis-day filter")
  return "PASS: holding row, stock link, keyboard navigation, latest thesis, missing thesis, scroll target, and ordinary AI War entry"
}
