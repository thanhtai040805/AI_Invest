from app.domain.services.document_selector import ActiveDocumentSelector, parse_period_dates


def test_parse_period_dates_preserves_non_calendar_fiscal_year():
    assert parse_period_dates("BCTC năm 2025 từ 01/10/2024 - 30/09/2025") == (
        "2024-10-01",
        "2025-09-30",
    )


class _Cursor:
    def __init__(self) -> None:
        self.rows = []

    def execute(self, query, _params=()):
        normalized = " ".join(query.split())
        if "SELECT title, published_date, article_pdf_urls" in normalized:
            self.rows = []
        elif "doc_type = 'governance_report'" in normalized:
            self.rows = []
        elif "doc_type = 'financial_statement'" in normalized and "LOWER(title)" not in normalized:
            if "published_date >=" in normalized:
                self.rows = [
                    {
                        "id": 2,
                        "title": "BCTC Công ty mẹ quý 2 năm 2026",
                        "doc_type": "financial_statement",
                        "published_date": "2026-07-28",
                        "article_pdf_urls": ["https://example.test/separate-q2.pdf"],
                        "source": "vnstock_docs",
                    },
                    {
                        "id": 3,
                        "title": "BCTC Hợp nhất Soát xét 6 tháng đầu năm 2026",
                        "doc_type": "financial_statement",
                        "published_date": "2026-08-22",
                        "article_pdf_urls": ["https://example.test/consolidated-q2.pdf"],
                        "source": "vnstock_docs",
                    },
                ]
            else:
                self.rows = [
                    {
                        "id": 4,
                        "title": "BCTC Công ty mẹ Kiểm toán năm 2025",
                        "doc_type": "financial_statement",
                        "published_date": "2026-03-20",
                        "article_pdf_urls": ["https://example.test/separate-annual.pdf"],
                        "source": "vnstock_docs",
                    },
                    {
                        "id": 5,
                        "title": "BCTC Hợp nhất Kiểm toán năm 2025",
                        "doc_type": "financial_statement",
                        "published_date": "2026-03-20",
                        "article_pdf_urls": ["https://example.test/consolidated-annual.pdf"],
                        "source": "vnstock_docs",
                    },
                ]
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def close(self):
        return None


class _Connection:
    def __init__(self) -> None:
        self.cursor_instance = _Cursor()

    def cursor(self, cursor_factory=None):
        return self.cursor_instance

    def close(self):
        return None


def test_selector_keeps_financial_documents_separate_for_same_period(monkeypatch):
    selector = ActiveDocumentSelector()
    monkeypatch.setattr(selector, "_get_connection", lambda: _Connection())

    selected = selector.select_active_documents("FPT")

    assert selected.annual_audited is not None
    assert selected.annual_audited.scope == "SEPARATE"
    assert "Công ty mẹ" in selected.annual_audited.title or "Cong ty me" in selected.annual_audited.title
    assert selected.latest_quarter is not None
    assert selected.latest_quarter.scope == "SEPARATE"
