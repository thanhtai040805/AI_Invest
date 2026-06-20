"""Unit tests for Financial Ingestion Service — TASK-104."""

import pytest
from datetime import date, datetime, timedelta
from app.infrastructure.data_pipelines.financial_ingestion_service import FinancialIngestionService

@pytest.fixture
def financial_svc():
    return FinancialIngestionService()

def test_process_financial_statement_pit_enforcement(financial_svc):
    """Test that missing announcement_date strictly returns None."""
    raw = {
        "ticker": "VHM",
        "fiscal_year": 2026,
        "fiscal_quarter": 1,
        "period_end_date": date(2026, 3, 31),
        "revenue": 1000,
        "net_income": 200,
        "total_assets": 5000,
        "total_equity": 3000
    }
    
    processed = financial_svc.process_financial_statement(raw)
    assert processed is None

def test_process_financial_statement_annual_pit(financial_svc):
    """Test successful processing when announcement_date is provided."""
    raw = {
        "ticker": "VHM",
        "fiscal_year": 2025,
        "fiscal_quarter": 4,
        "period_end_date": date(2025, 12, 31),
        "announcement_date": date(2026, 3, 30), # Provided
        "revenue": 4000,
        "net_income": 800,
        "total_assets": 5000,
        "total_equity": 3000
    }
    
    processed = financial_svc.process_financial_statement(raw)
    
    assert processed["announcement_date"] == date(2026, 3, 30)
    assert processed["has_data_flag"] is True

def test_process_financial_statement_roic_calculation(financial_svc):
    """Test ROIC calculation with standard 20% tax."""
    raw = {
        "ticker": "VHM",
        "ebit": 1000,
        "total_equity": 4000,
        "total_debt": 1000,
        "ebt": 1000,
        "tax_expense": 200,
        "period_end_date": date(2026, 3, 31),
        "announcement_date": date(2026, 4, 30) # Provided
    }
    
    processed = financial_svc.process_financial_statement(raw)
    
    # ROIC = EBIT * (1 - 20%) / (Equity + Debt)
    # ROIC = 1000 * 0.8 / 5000 = 800 / 5000 = 0.16
    assert processed["roic"] == 0.16

def test_process_financial_statement_missing_data_flag(financial_svc):
    """Test has_data_flag = false when important fields are missing."""
    raw = {
        "ticker": "VHM",
        "revenue": 1000,
        # missing net_income, assets, equity
        "period_end_date": date(2026, 3, 31),
        "announcement_date": date(2026, 4, 30) # Provided
    }
    
    processed = financial_svc.process_financial_statement(raw)
    assert processed["has_data_flag"] is False
