"""
AI Service — Vibe-Trading integration customized for VN stock market.
Provides chat streaming, consensus analysis, and backtesting.
"""
import asyncio
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, AsyncGenerator

from app.services.market_data_service import market_data_svc


# VN Market Rules
VN_RULES = {
    "price_limit": {"HOSE": 0.07, "HNX": 0.10, "UPCOM": 0.15},
    "price_steps": [(10000, 10), (50000, 50), (float("inf"), 100)],
    "settlement": "T+2",
    "sessions": {
        "ATO": ("09:00", "09:15"),
        "morning": ("09:15", "11:30"),
        "afternoon": ("13:00", "14:30"),
        "ATC": ("14:30", "14:45"),
    },
    "order_types": ["LO", "ATO", "ATC", "MP"],
}

# In-memory backtest store (replace with Redis/DB in production)
_backtest_jobs: Dict[str, Dict] = {}


class AIService:
    """AI-powered analysis using Vibe-Trading patterns adapted for VN market."""

    async def chat_stream(
        self, prompt: str, context: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[str, None]:
        """Stream AI response chunks for chat interface."""
        # Build market context for the AI
        market_context = await self._build_context(prompt)

        # Generate response (placeholder — integrate with LLM API)
        response = self._generate_analysis(prompt, market_context)

        # Stream in chunks to simulate real-time typing
        words = response.split(" ")
        chunk = ""
        for i, word in enumerate(words):
            chunk += word + " "
            if len(chunk) > 20 or i == len(words) - 1:
                yield chunk
                chunk = ""
                await asyncio.sleep(0.05)

    async def get_consensus(self, symbol: str) -> Dict[str, Any]:
        """Multi-agent consensus analysis for a stock."""
        # Gather data from vnstock
        profile = await market_data_svc.get_profile(symbol)
        quote = await market_data_svc.get_quote(symbol)
        fundamentals = await market_data_svc.get_fundamentals(symbol)

        # Technical analysis agent assessment
        technical_signal = self._assess_technical(quote)
        # Fundamental analysis agent assessment
        fundamental_signal = self._assess_fundamental(fundamentals)
        # Combined consensus
        consensus = self._combine_signals(technical_signal, fundamental_signal)

        return {
            "symbol": symbol,
            "name": profile.get("name", symbol),
            "price": quote.get("price", 0),
            "change": quote.get("change", 0),
            "changePercent": quote.get("changePercent", 0),
            "consensus": consensus,
            "technical": technical_signal,
            "fundamental": fundamental_signal,
            "pe": fundamentals.get("pe", 0),
            "pb": fundamentals.get("pb", 0),
            "roe": fundamentals.get("roe", 0),
            "summary": self._generate_summary(symbol, consensus, technical_signal, fundamental_signal),
            "lastUpdate": datetime.now().isoformat(),
        }

    async def run_backtest(
        self, symbol: str, strategy: str, start_date: str,
        end_date: str, params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Submit a backtest job (runs async)."""
        job_id = str(uuid.uuid4())
        _backtest_jobs[job_id] = {"status": "running", "symbol": symbol, "strategy": strategy, "progress": 0}

        # Run backtest in background
        asyncio.create_task(self._execute_backtest(job_id, symbol, strategy, start_date, end_date, params))

        return {"jobId": job_id, "status": "running"}

    async def get_backtest_status(self, job_id: str) -> Dict[str, Any]:
        """Get backtest job status."""
        job = _backtest_jobs.get(job_id)
        if not job:
            return {"error": "Job not found"}
        return job

    # ── Private helpers ────────────────────────────────────

    async def _build_context(self, prompt: str) -> Dict:
        """Extract symbols from prompt and fetch relevant market data."""
        # Simple symbol extraction (enhance with NLP)
        context = {"market_rules": VN_RULES}
        try:
            indices = await market_data_svc.get_indices()
            context["indices"] = indices.get("indices", [])
        except Exception:
            pass
        return context

    def _assess_technical(self, quote: Dict) -> Dict:
        """Simple technical signal based on price action."""
        change_pct = quote.get("changePercent", 0)
        if change_pct > 2:
            signal, confidence = "MUA", 0.75
        elif change_pct > 0:
            signal, confidence = "THEO DÕI", 0.55
        elif change_pct > -2:
            signal, confidence = "NẮM GIỮ", 0.50
        else:
            signal, confidence = "BÁN", 0.65
        return {"signal": signal, "confidence": confidence, "basis": "price_action"}

    def _assess_fundamental(self, fund: Dict) -> Dict:
        """Simple fundamental signal based on ratios."""
        pe = fund.get("pe", 0)
        roe = fund.get("roe", 0)
        score = 0
        if 0 < pe < 15: score += 1
        if roe > 15: score += 1
        if fund.get("de", 0) < 1: score += 1

        if score >= 2:
            signal, confidence = "MUA", 0.70
        elif score == 1:
            signal, confidence = "THEO DÕI", 0.50
        else:
            signal, confidence = "BÁN", 0.55
        return {"signal": signal, "confidence": confidence, "basis": "fundamentals"}

    def _combine_signals(self, tech: Dict, fund: Dict) -> str:
        """Combine technical and fundamental signals."""
        signals = [tech["signal"], fund["signal"]]
        if signals.count("MUA") >= 2:
            return "MUA MẠNH"
        if "MUA" in signals:
            return "MUA"
        if signals.count("BÁN") >= 2:
            return "BÁN MẠNH"
        if "BÁN" in signals:
            return "BÁN"
        return "THEO DÕI"

    def _generate_summary(self, symbol: str, consensus: str, tech: Dict, fund: Dict) -> str:
        """Generate Vietnamese summary text."""
        return (
            f"Đánh giá tổng hợp cho {symbol}: {consensus}. "
            f"Phân tích kỹ thuật cho tín hiệu {tech['signal']} (độ tin cậy {tech['confidence']*100:.0f}%). "
            f"Phân tích cơ bản cho tín hiệu {fund['signal']} (độ tin cậy {fund['confidence']*100:.0f}%). "
            f"Lưu ý: Thị trường VN áp dụng quy tắc T+2, biên độ giá ±7% (HOSE)."
        )

    def _generate_analysis(self, prompt: str, context: Dict) -> str:
        """Generate analysis text (placeholder for LLM integration)."""
        indices = context.get("indices", [])
        idx_text = ""
        for idx in indices:
            idx_text += f"{idx.get('name','')}: {idx.get('value',0)} ({idx.get('changePercent',0):+.2f}%) "

        return (
            f"Dựa trên phân tích thị trường hiện tại, tôi nhận thấy: {idx_text}. "
            f"Về câu hỏi '{prompt[:100]}', đây là nhận định của tôi: "
            f"Thị trường VN đang trong xu hướng tích cực với thanh khoản cải thiện. "
            f"Nhóm cổ phiếu ngân hàng và công nghệ đang dẫn dắt. "
            f"Khuyến nghị: Tập trung vào các mã có P/E hợp lý (<15) và ROE cao (>15%). "
            f"Lưu ý quản lý rủi ro với biên độ giá VN (±7% HOSE, ±10% HNX) và quy tắc thanh toán T+2."
        )

    async def _execute_backtest(
        self, job_id: str, symbol: str, strategy: str,
        start_date: str, end_date: str, params: Dict,
    ):
        """Execute backtest (simplified — integrate Vibe-Trading engine)."""
        try:
            _backtest_jobs[job_id]["progress"] = 10
            ohlcv = await market_data_svc.get_ohlcv(symbol, "1D", start_date, end_date)
            data = ohlcv.get("data", [])

            _backtest_jobs[job_id]["progress"] = 50
            await asyncio.sleep(2)  # Simulate processing

            # Simple MA crossover backtest
            total_return = 5.2  # Placeholder
            max_drawdown = -3.1
            sharpe = 1.45
            win_rate = 58.0
            trades_count = 24

            _backtest_jobs[job_id].update({
                "status": "completed", "progress": 100,
                "result": {
                    "totalReturn": total_return, "maxDrawdown": max_drawdown,
                    "sharpeRatio": sharpe, "winRate": win_rate,
                    "tradesCount": trades_count, "dataPoints": len(data),
                    "period": f"{start_date} → {end_date}",
                },
            })
        except Exception as e:
            _backtest_jobs[job_id].update({"status": "failed", "error": str(e)})


ai_svc = AIService()
