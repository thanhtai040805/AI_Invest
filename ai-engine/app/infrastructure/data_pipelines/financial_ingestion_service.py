"""Financial Ingestion Service — TASK-104

Module chịu trách nhiệm lấy và xử lý báo cáo tài chính (BCTC).
Đảm bảo tính Point-in-Time (PIT) thông qua announcement_date.
Tự động tính toán các chỉ số phái sinh (ROIC, Accrual Ratio, FCF).
"""

import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
from app.config.settings import get_settings

logger = logging.getLogger(__name__)

class FinancialIngestionService:
    def __init__(self):
        self.settings = get_settings()
        self.db_url = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")

    def fetch_financials(
        self, 
        symbol: str, 
        fiscal_year: int, 
        fiscal_quarter: int
    ) -> Dict[str, Any]:
        """Lấy BCTC cho một ticker và kỳ kế toán cụ thể."""
        # TODO: Tích hợp với AlphaStock hoặc vnstock API để lấy data thô
        # Hiện tại, giả định trả về cấu trúc thô để xử lý
        return {}

    def process_financial_statement(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Xử lý data thô thành cấu trúc FinancialStatement chuẩn.
        
        Thực hiện:
        1. Xác định announcement_date (PIT enforcement).
        2. Tính toán các chỉ số phái sinh (ROIC, FCF, Accrual).
        3. Kiểm tra tính đầy đủ (has_data_flag).
        """
        symbol = raw_data.get("ticker")
        pe_date = raw_data.get("period_end_date")
        
        # 1. PIT Enforcement: announcement_date
        # THEO MANDATE: Không dùng fallback data. Yêu cầu announcement_date phải có sẵn từ nguồn dữ liệu.
        ann_date = raw_data.get("announcement_date")
        if not ann_date:
            logger.error(f"Missing mandatory announcement_date for {symbol}. PIT integrity cannot be guaranteed.")
            # Trả về None để báo hiệu dữ liệu không hợp lệ
            return None

        # 2. Derived Metrics
        revenue = raw_data.get("revenue", 0)
        ebit = raw_data.get("ebit", 0)
        tax_expense = raw_data.get("tax_expense", 0)
        ebt = raw_data.get("ebt", 0)
        
        # ROIC Logic: 20% tax unless specified
        # TODO: Cần data 'tax_commitment_years' để dùng effective_tax_rate thực tế
        effective_tax_rate = tax_expense / ebt if ebt and ebt > 0 else 0.20
        standard_tax = 0.20
        # AC: dùng 20% trừ khi có cam kết > 5 năm (hiện tại chưa có data này -> dùng 20%)
        roic_tax_rate = standard_tax 
        
        equity = raw_data.get("total_equity", 1)
        debt = raw_data.get("total_debt", 0)
        invested_capital = equity + debt
        
        roic = (ebit * (1 - roic_tax_rate)) / invested_capital if invested_capital > 0 else 0
        
        # FCF = CFO - CAPEX
        cfo = raw_data.get("cfo", 0)
        capex = abs(raw_data.get("capex", 0))
        fcf = cfo - capex
        
        # Accrual Ratio = (Net Income - CFO) / Total Assets
        assets = raw_data.get("total_assets", 1)
        net_income = raw_data.get("net_income", 0)
        accrual_ratio = (net_income - cfo) / assets if assets > 0 else 0

        # 3. Check completeness
        important_fields = ["revenue", "net_income", "total_assets", "total_equity"]
        has_data_flag = all(raw_data.get(f) is not None for f in important_fields)

        processed = {
            **raw_data,
            "announcement_date": ann_date,
            "roic": roic,
            "fcf": fcf,
            "accrual_ratio": accrual_ratio,
            "has_data_flag": has_data_flag,
            "derived_at": datetime.now()
        }
        
        return processed

    def save_financial_statement(self, data: Dict[str, Any]) -> bool:
        """Lưu BCTC vào database."""
        if not data.get("ticker") or not data.get("announcement_date"):
            return False
            
        import psycopg2
        from psycopg2.extras import Json
        
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor()
        
        try:
            cur.execute("""
                INSERT INTO financial_statements (
                    symbol, fiscal_year, fiscal_quarter, period_end, 
                    announcement_date, has_data_flag, data
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, fiscal_year, fiscal_quarter) 
                DO UPDATE SET 
                    announcement_date = EXCLUDED.announcement_date,
                    has_data_flag = EXCLUDED.has_data_flag,
                    data = EXCLUDED.data,
                    fetched_at = NOW()
            """, (
                data["ticker"], data["fiscal_year"], data["fiscal_quarter"],
                data["period_end_date"], data["announcement_date"],
                data["has_data_flag"], Json(data)
            ))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error saving financial statement for {data['ticker']}: {e}")
            conn.rollback()
            return False
        finally:
            cur.close()
            conn.close()

financial_ingestion_svc = FinancialIngestionService()
