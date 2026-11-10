"""Prepare auditable, sector-aware BCTC evidence for Agent-04 prompts."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Dict, Iterable, Optional


SECTOR_GUIDANCE = {
    "AGRICULTURE": "Nông nghiệp/thủy sản: xem doanh thu, biên, tồn kho, phải thu, CFO và nợ khi có; không suy ra sản lượng, giá hàng hóa hoặc mùa vụ.",
    "BANKS": "Ngân hàng: xem thu nhập lãi thuần, dự phòng tín dụng, dư nợ cho vay, tiền gửi và ROE khi có. Không dùng GPM, CFO/FCF, vốn lưu động, current ratio, debt/equity hoặc net debt/EBITDA như doanh nghiệp thông thường; NIM, NPL, bao phủ nợ xấu, CAR, CIR, CASA chỉ dùng nếu có trong input.",
    "BASIC_RESOURCES": "Tài nguyên cơ bản: phân biệt khai khoáng, cao su, vật liệu và thương mại theo ngành con; dùng doanh thu/biên, tồn kho, phải thu, CFO, CapEx, tài sản và nợ nếu có; không suy ra trữ lượng, sản lượng hay giá hàng hóa.",
    "CHEMICALS": "Hóa chất/nhựa: xem doanh thu, biên, tồn kho, phải thu, CFO, CapEx và nợ nếu có; không suy ra giá nguyên liệu, công suất hoặc sản lượng.",
    "CONSTRUCTION": "Xây dựng/tư vấn/hạ tầng: ưu tiên phải thu, ứng trước, tồn kho/dở dang, CFO và nợ nếu có. Dòng tiền có thể lệch theo nghiệm thu/bàn giao; không suy ra dự án, pháp lý hoặc tiến độ.",
    "CONSTRUCTION_MATERIALS": "Vật liệu xây dựng: xem doanh thu/biên, tồn kho, phải thu, tài sản cố định, CFO, CapEx và nợ nếu có; không suy ra công suất hoặc sản lượng.",
    "CONSUMER_SERVICES": "Dịch vụ lưu trú/ăn uống/giải trí: xem doanh thu, biên/chi phí, phải thu, tiền, CFO và nợ nếu có; không suy ra giá phòng, công suất hoặc lượng khách.",
    "FINANCIAL_SERVICES": "Tài chính: phân biệt chứng khoán, bảo hiểm và tài chính khác theo ngành con. Môi giới, cho vay ký quỹ, tự doanh, phí bảo hiểm, bồi thường và khả năng thanh toán chỉ dùng nếu có; không áp CFO/FCF, GPM, vốn lưu động, current ratio, debt/equity hoặc net debt/EBITDA thông thường.",
    "FOOD_BEVERAGE": "Thực phẩm/đồ uống: xem doanh thu/biên, tồn kho, phải thu, CFO, CapEx và nợ nếu có; không suy ra sản lượng hoặc giá đầu vào.",
    "HEALTHCARE": "Y tế: phân biệt phân phối và dịch vụ theo dữ liệu; xem doanh thu/biên, tồn kho, phải thu, CFO, CapEx và nợ nếu có; không suy ra bệnh nhân, giường bệnh hoặc công suất.",
    "INDUSTRIAL_GOODS": "Hàng công nghiệp: phân biệt phụ trợ, hàng gia dụng, điện, máy móc và dệt may; xem biên, tồn kho, phải thu/phải trả, tài sản cố định, CFO, CapEx và nợ nếu có; không suy ra đơn hàng, backlog hoặc công suất.",
    "OIL_GAS": "Dầu khí: phân biệt khai khoáng, dịch vụ phụ trợ và tiện ích theo ngành con; chỉ dùng chỉ tiêu doanh nghiệp có trong BCTC; không suy ra giá dầu, trữ lượng hoặc sản lượng.",
    "OTHER_INDUSTRIALS": "Truyền thông/quảng cáo: xem doanh thu, biên/chi phí, phải thu, tiền, CFO và nợ nếu có; không suy ra doanh thu định kỳ hoặc lượng người dùng.",
    "REAL_ESTATE": "Bất động sản: ưu tiên tồn kho/dở dang, khách hàng ứng trước, phải thu, tiền, nợ và chi phí lãi nếu có. CFO/FCF có thể lệch theo bàn giao; không tự suy ra pháp lý, tiến độ hoặc giá trị dự án.",
    "RETAIL_TRADE": "Thương mại/bán lẻ: phân biệt bán buôn và bán lẻ; xem tồn kho, phải thu/phải trả, doanh thu, biên và CFO nếu có. Chỉ tính vòng quay khi đủ kỳ; không suy ra doanh số cửa hàng hoặc lượng khách.",
    "TECHNOLOGY": "Công nghệ/thông tin/truyền thông số: xem doanh thu, tăng trưởng, biên, phải thu, tiền, CFO, CapEx và nợ nếu có; không suy ra ARR, churn, người dùng hoặc doanh thu định kỳ.",
    "TRANSPORTATION": "Vận tải/kho bãi: xem tài sản cố định, khấu hao, nợ/chi phí lãi, CFO và CapEx nếu có; không suy ra tải trọng, hệ số lấp đầy hoặc giá cước.",
    "UTILITIES": "Tiện ích: xem tài sản cố định/dở dang, nợ, lãi vay, CFO và CapEx nếu có; không suy ra biểu giá, sản lượng hoặc công suất.",
}

INDUSTRY_GUIDANCE = {
    "che bien thuy san": "Ưu tiên tồn kho nguyên liệu/thành phẩm và phải thu; không suy ra sản lượng đánh bắt/nuôi.",
    "nong lam ngu": "Chỉ nêu yếu tố mùa vụ nếu kỳ và số liệu trong input hỗ trợ.",
    "ngan hang": "Dùng hồ sơ ngân hàng; không áp GPM, CFO/FCF hoặc vốn lưu động thông thường.",
    "khai khoang": "Không suy ra trữ lượng, sản lượng hoặc giá bán.",
    "san pham cao su": "Không suy ra diện tích khai thác, sản lượng hoặc giá cao su.",
    "vat lieu xay dung": "Dùng hồ sơ vật liệu; không suy ra công suất hoặc sản lượng.",
    "ban buon": "Ưu tiên tồn kho/phải thu khi có số liệu cùng kỳ; không suy ra sell-through.",
    "sx nhua hoa chat": "Không suy ra giá nguyên liệu hoặc công suất.",
    "xay dung": "Ưu tiên vốn lưu động, phải thu, ứng trước và dở dang; không tự nêu dự án.",
    "dich vu tu van ho tro": "Dùng hồ sơ dịch vụ; không ép chỉ tiêu tồn kho/công trình.",
    "xay dung dien ha tang": "Không tự nêu tiến độ dự án hoặc cơ chế giá.",
    "dich vu luu tru an uong giai tri": "Không suy ra công suất phòng, giá phòng hoặc lượng khách.",
    "chung khoan": "Ghi số dư các khoản cho vay theo đúng nhãn; chỉ gọi là ký quỹ nếu dòng nguồn nêu rõ ký quỹ. Lãi/lỗ FVTPL phải giữ riêng; không dùng GPM.",
    "bao hiem": "Phí bảo hiểm và bồi thường chỉ dùng khi có; combined ratio/solvency không được tự suy ra.",
    "tai chinh khac": "Không gán hồ sơ ngân hàng, chứng khoán hoặc bảo hiểm khi thiếu bằng chứng.",
    "thuc pham do uong": "Không suy ra sản lượng hoặc giá nguyên liệu.",
    "cham soc suc khoe": "Không suy ra bệnh nhân, giường bệnh hoặc công suất.",
    "sx phu tro": "Không suy ra backlog hoặc công suất.",
    "sx hang gia dung": "Không suy ra thị phần hoặc sell-through.",
    "thiet bi dien": "Không suy ra đơn hàng hoặc công suất.",
    "sx thiet bi may moc": "Không suy ra backlog hoặc mức sử dụng máy.",
    "det may": "Không suy ra sản lượng, đơn hàng hoặc giá nguyên liệu.",
    "tien ich": "Không suy ra biểu giá hoặc sản lượng điện/nước.",
    "truyen thong quang cao": "Không suy ra doanh thu định kỳ hoặc lượng người dùng.",
    "bat dong san": "Không suy ra trạng thái pháp lý hoặc tiến độ dự án.",
    "ban le": "Không suy ra doanh thu cửa hàng, khách ghé thăm hoặc sell-through.",
    "cong nghe va thong tin": "Không suy ra ARR, churn, người dùng hoặc doanh thu định kỳ.",
    "truyen thong so giai tri": "Không suy ra người dùng hoặc doanh thu định kỳ.",
    "van tai kho bai": "Không suy ra tải trọng, hệ số lấp đầy hoặc giá cước.",
}

_ALIASES = {
    "revenue": ("doanh thu thuan ve ban hang va cung cap dich vu", "doanh thu thuan", "doanh thu ban hang"),
    "net_income": ("loi nhuan sau thue thu nhap doanh nghiep", "18 loi nhuan sau thue", "29 loi nhuan sau thue", "xiii loi nhuan sau thue", "loi nhuan sau thue"),
    "net_interest_income": ("thu nhap lai thuan",),
    "credit_provisions": ("chi phi du phong rui ro tin dung", "du phong rui ro tin dung"),
    "customer_loans": ("cho vay khach hang",),
    "customer_deposits": ("tien gui cua khach hang", "tien gui khach hang"),
    "brokerage_revenue": ("doanh thu moi gioi chung khoan",),
    "margin_lending": ("cho vay hoat dong giao dich ky quy", "cho vay hoat dong margin", "cho vay hoat dong ky quy"),
    "broker_loans": ("cac khoan cho vay",),
    "proprietary_trading_gains": ("lai tu cac tai san tai chinh ghi nhan thong qua lai lo",),
    "proprietary_trading_losses": ("lo cac tai san tai chinh ghi nhan thong qua lo",),
    "insurance_premiums": ("doanh thu phi bao hiem thuan", "thu phi bao hiem thuan", "thu phi bao hiem goc", "doanh thu bao hiem"),
    "insurance_claims": ("chi phi boi thuong bao hiem", "chi boi thuong bao hiem", "chi boi thuong"),
    "cfo": ("luu chuyen tien thuan tu hoat dong kinh doanh", "tien thuan tu hdkd", "luu chuyen thuan tu hoat dong kinh doanh"),
    "capex": (
        "tien chi de mua sam xay dung tscd va cac tai san dai han khac",
        "tien chi de mua sam xay dung tai san co dinh va cac tai san dai han khac",
        "tien chi de mua sam xay dung tscd",
        "tien chi mua sam xay dung tscd va cac tai san dai han khac",
        "tien chi mua sam xay dung tscd",
        "tien mua tai san co dinh", "mua sam tai san co dinh", "mua sam tscd",
    ),
    "cash": ("tien va cac khoan tuong duong tien", "tien va tuong duong tien", "tien"),
    "inventory": ("hang ton kho",),
    "current_assets": ("tai san ngan han",),
    "current_liabilities": ("no ngan han",),
    "receivables_change": ("tang giam cac khoan phai thu", "tang cac khoan phai thu", "giam cac khoan phai thu"),
    "inventory_change": ("tang giam cac khoan hang ton kho", "tang giam hang ton kho"),
    "payables_change": ("tang giam cac khoan phai tra", "tang cac khoan phai tra", "giam cac khoan phai tra"),
    "short_debt": ("vay va no thue tai chinh ngan han", "vay ngan han"),
    "long_debt": ("vay va no thue tai chinh dai han", "vay dai han"),
    "customer_advances": ("nguoi mua tra tien truoc ngan han", "nguoi mua tra tien truoc dai han", "nguoi mua tra tien truoc"),
    "interest_expense": ("chi phi lai vay", "chi phi lai va cac chi phi tuong tu"),
    "interest_paid": (
        "tien lai vay da tra", "tien chi tra lai vay",
        "chi phi lai va cac chi phi tuong tu da tra",
    ),
}

_FIELD_LABELS = {
    "revenue": "Doanh thu thuần",
    "net_income": "Lợi nhuận sau thuế",
    "net_interest_income": "Thu nhập lãi thuần",
    "credit_provisions": "Chi phí dự phòng tín dụng",
    "customer_loans": "Dư nợ cho vay khách hàng",
    "customer_deposits": "Tiền gửi khách hàng",
    "brokerage_revenue": "Doanh thu môi giới chứng khoán",
    "proprietary_trading_gains": "Lãi FVTPL",
    "proprietary_trading_losses": "Lỗ FVTPL",
    "broker_loans": "Các khoản cho vay theo nhãn BCTC",
    "margin_lending": "Cho vay ký quỹ theo nhãn BCTC",
    "insurance_premiums": "Phí bảo hiểm",
    "insurance_claims": "Chi phí bồi thường bảo hiểm",
    "cfo": "CFO",
    "capex": "CapEx",
    "fcf": "FCF (CFO - |CapEx|)",
    "cash": "Tiền và tương đương tiền",
    "inventory": "Hàng tồn kho",
    "current_assets": "Tổng tài sản ngắn hạn",
    "current_liabilities": "Tổng nợ ngắn hạn",
    "receivables_change": "Biến động khoản phải thu trong CF",
    "inventory_change": "Biến động hàng tồn kho trong CF",
    "payables_change": "Biến động khoản phải trả trong CF",
    "short_debt": "Vay ngắn hạn",
    "long_debt": "Vay dài hạn",
    "customer_advances": "Khách hàng trả tiền trước",
    "net_debt": "Nợ vay ròng (nợ vay ngắn hạn + dài hạn - tiền)",
    "interest_expense": "Chi phí lãi vay trên IS",
    "interest_paid": "Lãi vay đã trả trên CF",
}


def normalize_label(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def sector_guidance(sector: Any, industry: Any = None) -> str:
    sector_key = re.sub(r"[^A-Z0-9]+", "_", str(sector or "").upper()).strip("_")
    profile = SECTOR_GUIDANCE.get(sector_key)
    if not profile:
        profile = "Ngành chưa phân loại trong DB: chỉ dùng chỉ tiêu phổ thông có trong input; không suy diễn chỉ tiêu đặc thù."
    return " ".join(part for part in (profile, INDUSTRY_GUIDANCE.get(normalize_label(industry), "")) if part)


def _period_rank(row: Dict[str, Any]) -> tuple[str, int, str]:
    frequency = str(row.get("frequency") or "").lower()
    return (str(row.get("period_end") or ""), 1 if frequency == "quarterly" else 0, str(row.get("fetched_at") or ""))


def latest_by_type(statements: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for row in statements:
        statement_type = str(row.get("statement_type") or "").upper()
        if statement_type in {"IS", "BS", "CF"} and (
            statement_type not in latest or _period_rank(row) > _period_rank(latest[statement_type])
        ):
            latest[statement_type] = row
    return latest


def latest_aligned_package(statements: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Dict[str, Any]]]:
    packages: Dict[tuple[str, str], Dict[str, Dict[str, Any]]] = {}
    for row in statements:
        statement_type = str(row.get("statement_type") or "").upper()
        if statement_type in {"IS", "BS", "CF"}:
            key = (str(row.get("period_end") or ""), str(row.get("frequency") or ""))
            packages.setdefault(key, {})[statement_type] = row
    complete = [(key, package) for key, package in packages.items() if set(package) == {"IS", "BS", "CF"}]
    if not complete:
        return None
    _, package = max(complete, key=lambda item: (item[0][0], item[0][1].lower() == "quarterly"))
    return package


def _find(data: Any, field: str) -> Optional[tuple[float, str]]:
    if not isinstance(data, dict):
        return None
    entries = []
    for key, value in data.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            continue
        normalized = normalize_label(key)
        stripped = re.sub(r"^(?:(?:\d+|[ivxlcdm]+|[a-z])\s+)+", "", normalized)
        entries.append((str(key), float(value), normalized, stripped))
    aliases = _ALIASES[field]
    for relation in ("exact", "suffix", "prefix"):
        for alias in aliases:
            if relation == "prefix" and (len(alias) < 14 or field in {"interest_expense", "interest_paid"}):
                continue
            matches = []
            for entry in entries:
                normalized, stripped = entry[2], entry[3]
                if relation == "exact" and alias in {normalized, stripped}:
                    matches.append(entry)
                elif relation == "suffix" and (normalized.endswith(" " + alias) or stripped.endswith(" " + alias)):
                    matches.append(entry)
                elif relation == "prefix" and (normalized.startswith(alias + " ") or stripped.startswith(alias + " ")):
                    matches.append(entry)
            if matches:
                key, value, _, _ = min(matches, key=lambda entry: len(entry[2]))
                return value, key
    return None


def _money(value: float) -> str:
    # AlphaStock, CafeF and VCI ETL preserve statement amounts in VND.
    return f"{value / 1_000_000_000:,.2f} tỷ VND"


def _field(data: Any, name: str, row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    found = _find(data, name)
    if not found:
        return None
    value, key = found
    return {
        "value": value,
        "source_key": key,
        "period_end": str(row.get("period_end") or ""),
        "frequency": str(row.get("frequency") or ""),
        "statement_type": str(row.get("statement_type") or "").upper(),
        "source": row.get("source"),
    }


def _field_line(name: str, field: Dict[str, Any]) -> str:
    return (
        f"- {_FIELD_LABELS.get(name, name)}: {_money(field['value'])} | {field['statement_type']} {field['period_end']} "
        f"({field['frequency']}, nguồn={field['source'] or 'không rõ'}, dòng='{field['source_key']}')"
    )


def build_financial_evidence(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Build a read-only snapshot; each extracted metric retains its statement period and source."""
    sector, industry = snapshot.get("sector"), snapshot.get("industry")
    guidance = sector_guidance(sector, industry)
    statements = snapshot.get("statements") or []
    latest = latest_by_type(statements)
    package = latest_aligned_package(statements)
    latest_pairs = {(row.get("period_end"), row.get("frequency")) for row in latest.values()}
    latest_aligned = len(latest) == 3 and len(latest_pairs) == 1
    is_financial = str(sector or "").upper() in {"BANKS", "FINANCIAL_SERVICES"}
    industry_key = normalize_label(industry)
    fields: Dict[str, tuple[str, str]] = {}
    if str(sector or "").upper() == "BANKS":
        expected = ("net_interest_income", "credit_provisions", "net_income", "customer_loans", "customer_deposits")
        fields.update({
            "net_interest_income": ("IS", "net_interest_income"),
            "credit_provisions": ("IS", "credit_provisions"),
            "net_income": ("IS", "net_income"),
            "customer_loans": ("BS", "customer_loans"),
            "customer_deposits": ("BS", "customer_deposits"),
        })
    elif industry_key == "chung khoan":
        expected = ("brokerage_revenue", "proprietary_trading_gains", "proprietary_trading_losses", "broker_loans", "margin_lending", "net_income")
        fields.update({
            "brokerage_revenue": ("IS", "brokerage_revenue"),
            "proprietary_trading_gains": ("IS", "proprietary_trading_gains"),
            "proprietary_trading_losses": ("IS", "proprietary_trading_losses"),
            "broker_loans": ("BS", "broker_loans"),
            "margin_lending": ("BS", "margin_lending"),
            "net_income": ("IS", "net_income"),
        })
    elif industry_key == "bao hiem":
        expected = ("insurance_premiums", "insurance_claims", "net_income")
        fields.update({
            "insurance_premiums": ("IS", "insurance_premiums"),
            "insurance_claims": ("IS", "insurance_claims"),
            "net_income": ("IS", "net_income"),
        })
    else:
        expected = ("revenue", "net_income")
        fields.update({"revenue": ("IS", "revenue"), "net_income": ("IS", "net_income")})

    if not is_financial:
        fields.update({
            "cfo": ("CF", "cfo"), "capex": ("CF", "capex"),
            "receivables_change": ("CF", "receivables_change"),
            "inventory_change": ("CF", "inventory_change"),
            "payables_change": ("CF", "payables_change"),
            "cash": ("BS", "cash"), "inventory": ("BS", "inventory"),
            "current_assets": ("BS", "current_assets"),
            "current_liabilities": ("BS", "current_liabilities"),
            "short_debt": ("BS", "short_debt"), "long_debt": ("BS", "long_debt"),
            "customer_advances": ("BS", "customer_advances"),
            "interest_expense": ("IS", "interest_expense"),
            "interest_paid": ("CF", "interest_paid"),
        })
        expected = (
            "revenue", "net_income", "cfo", "capex", "fcf", "cash", "current_assets",
            "current_liabilities", "short_debt", "long_debt", "net_debt", "interest_expense",
            "interest_paid",
        )
        if sector in {
            "AGRICULTURE", "BASIC_RESOURCES", "CHEMICALS", "CONSTRUCTION",
            "CONSTRUCTION_MATERIALS", "CONSUMER_SERVICES", "FOOD_BEVERAGE",
            "HEALTHCARE", "INDUSTRIAL_GOODS", "REAL_ESTATE", "RETAIL_TRADE", "TECHNOLOGY",
        }:
            expected += ("receivables_change", "payables_change")
        if sector in {
            "AGRICULTURE", "BASIC_RESOURCES", "CHEMICALS", "CONSTRUCTION_MATERIALS",
            "FOOD_BEVERAGE", "HEALTHCARE", "INDUSTRIAL_GOODS", "REAL_ESTATE", "RETAIL_TRADE",
        } or (sector == "CONSTRUCTION" and industry_key != "dich vu tu van ho tro"):
            expected += ("inventory", "inventory_change")
        if sector in {"CONSTRUCTION", "REAL_ESTATE"} and industry_key != "dich vu tu van ho tro":
            expected += ("customer_advances",)

    metrics: Dict[str, Dict[str, Any]] = {}
    for name, (statement_type, alias) in fields.items():
        row = latest.get(statement_type)
        metric = _field(row.get("data") if row else None, alias, row)
        if metric:
            metrics[name] = metric

    cf = latest.get("CF")
    cfo, capex = metrics.get("cfo"), metrics.get("capex")
    if cf and cfo is not None and capex is not None:
        metrics["fcf"] = {
            "value": cfo["value"] - abs(capex["value"]),
            "period_end": str(cf.get("period_end") or ""),
            "frequency": str(cf.get("frequency") or ""),
            "statement_type": "CF", "source": cf.get("source"),
            "formula": "CFO - |CapEx| trên cùng một bản ghi CF; không quy đổi sang quý/năm khác.",
        }

    bs = latest.get("BS")
    cash, short_debt, long_debt = metrics.get("cash"), metrics.get("short_debt"), metrics.get("long_debt")
    if bs and cash is not None and short_debt is not None and long_debt is not None:
        metrics["net_debt"] = {
            "value": short_debt["value"] + long_debt["value"] - cash["value"],
            "period_end": str(bs.get("period_end") or ""),
            "frequency": str(bs.get("frequency") or ""),
            "statement_type": "BS", "source": bs.get("source"),
            "formula": "Nợ vay ngắn hạn + nợ vay dài hạn - tiền và tương đương tiền; cùng một bản ghi BS.",
        }

    lines = [
        f"Ngành DB: {sector or 'Chưa phân loại'} / {industry or 'Chưa có ngành con'}",
        f"Hướng phân tích ngành: {guidance}",
        f"Ngày chốt={snapshot.get('as_of') or 'không rõ'}; thời điểm chốt={snapshot.get('observed_at') or 'không rõ'}.",
        "Cơ sở khả dụng: chỉ dùng bản ghi có fetched_at/updated_at lưu hiện tại không muộn hơn thời điểm chốt. Đây là lần fetch/update cuối được lưu, không đảm bảo lần đầu dữ liệu xuất hiện; ETL upsert có thể ghi đè thời điểm cũ nên lịch sử khả dụng có thể không khôi phục được. Nếu timestamp muộn hơn thời điểm chốt, không dùng và không giả định BCTC đã có sẵn.",
    ]
    for statement_type in ("IS", "BS", "CF"):
        row = latest.get(statement_type)
        if row:
            lines.append(
                f"BCTC mới nhất {statement_type}: kỳ={row.get('period_end')} {row.get('frequency')}; "
                f"fetched_at lưu hiện tại={row.get('fetched_at')}; nguồn={row.get('source')}; "
                f"published_date lưu={row.get('published_date')} (chưa xác minh ngày công bố)."
            )
        else:
            lines.append(f"BCTC mới nhất {statement_type}: Chưa có trong dữ liệu.")
    if package:
        period = next(iter(package.values())).get("period_end")
        frequency = next(iter(package.values())).get("frequency")
        lines.append(f"Bộ IS/BS/CF đồng kỳ gần nhất: {period} {frequency}.")
        if not latest_aligned:
            lines.append("Cảnh báo: BCTC mới nhất theo từng loại lệch kỳ; chỉ so sánh các dòng có cùng kỳ/tần suất, không kết hợp để tính tỷ lệ.")
    else:
        lines.append("Không có bộ IS/BS/CF đồng kỳ; không kết hợp số giữa các kỳ hoặc giữa quý và năm.")
    lines.append("Chỉ tiêu trích xuất theo bản ghi mới nhất của từng loại:")
    lines.extend(_field_line(name, value) for name, value in metrics.items() if "value" in value and "source_key" in value)
    lines.extend(
        f"- {_FIELD_LABELS.get(name, name)}: {_money(value['value'])} | {value['statement_type']} {value['period_end']} "
        f"({value['frequency']}, nguồn={value['source']}); phép tính: {value['formula']}"
        for name, value in metrics.items() if "formula" in value
    )
    missing = [key for key in expected if key not in metrics]
    if missing:
        lines.append(
            "Chỉ tiêu thuộc hồ sơ ngành nhưng chưa trích xuất được từ snapshot này: "
            + ", ".join(_FIELD_LABELS.get(key, key) for key in missing)
            + ". Chưa có trong dữ liệu; không suy ra từ tổng số hoặc mã dòng không rõ nghĩa."
        )

    ratio = snapshot.get("ratios")
    ratio_meta = None
    if ratio:
        ratio_meta = {
            key: ratio.get(key) for key in ("ratio_date", "frequency", "updated_at", "published_date")
        }
        lines.append(
            f"Tỷ số DB gần nhất: kỳ={ratio.get('ratio_date')} {ratio.get('frequency')}; "
            f"updated_at lưu hiện tại={ratio.get('updated_at')}; published_date lưu={ratio.get('published_date')} (chưa xác minh)."
        )
    else:
        lines.append("Tỷ số DB: Chưa có bản ghi được quan sát trước thời điểm chốt dữ liệu.")

    if not is_financial:
        for key, label in (("current_ratio", "Current ratio"), ("debt_equity", "Debt/equity")):
            value = ratio.get(key) if ratio else None
            rendered = f"{float(value):,.2f}x" if value is not None else "Chưa có trong dữ liệu"
            lines.append(f"- {label} theo financial_ratios: {rendered}.")
        lines.append("Định nghĩa debt/equity không được lưu cùng bản ghi; không mặc định đây là nợ vay/vốn chủ sở hữu, hãy đối chiếu các dòng vay trên BS.")
        lines.append("- EBITDA trực tiếp cùng kỳ/tần suất: Chưa có trong dữ liệu; không tính Net Debt/EBITDA.")
    else:
        lines.append("CFO/FCF, GPM, current ratio, debt/equity và Net Debt/EBITDA thông thường: Không áp dụng theo ngành tài chính.")
        if industry_key == "chung khoan" and "margin_lending" not in metrics:
            lines.append("Cho vay ký quỹ: Chưa có dòng riêng trong snapshot; không gán tổng 'Các khoản cho vay' thành dư nợ margin.")

    return {
        "sector": sector, "industry": industry, "guidance": guidance,
        "as_of": snapshot.get("as_of"), "observed_at": snapshot.get("observed_at"),
        "latest_by_type": latest,
        "aligned_package": package,
        "aligned": latest_aligned,
        "metrics": metrics, "ratios": ratio, "ratio_meta": ratio_meta,
        "formatted": "\n".join(lines),
    }


def build_core_metrics(
    research_report: Dict[str, Any], event_data: Dict[str, Any], evidence: Dict[str, Any],
) -> Dict[str, Any]:
    """Keep Agent-03 metrics, then fill gaps only from an observed DB ratio row."""
    def first_present(*values: Any) -> Any:
        return next((value for value in values if value is not None), None)

    metrics = {
        "pe": first_present(research_report.get("pe"), event_data.get("pe")),
        "pb": first_present(research_report.get("pb"), event_data.get("pb")),
        "roe": first_present(research_report.get("roe"), event_data.get("roe")),
        "roic": first_present(research_report.get("roic"), event_data.get("roic")),
        "gpm": first_present(research_report.get("gpm"), research_report.get("gross_profit_margin"), research_report.get("gross_margin")),
        "earnings_growth": first_present(research_report.get("earnings_growth"), event_data.get("earnings_growth")),
        "f4_score": first_present(research_report.get("f4_earnings"), research_report.get("f4_score")),
    }
    sources: Dict[str, str] = {}
    ratios = evidence.get("ratios") or {}
    financial_sector = str(evidence.get("sector") or "").upper() in {"BANKS", "FINANCIAL_SERVICES"}
    ratio_map = {
        "pe": ("pe", False), "pb": ("pb", False), "roe": ("roe", True),
        "gpm": ("gross_margin", True), "earnings_growth": ("yoy_earnings_growth", True),
    }
    for metric, (ratio_key, percentage) in ratio_map.items():
        if metrics[metric] is None and not (metric == "gpm" and financial_sector):
            value = ratios.get(ratio_key)
            if value is not None:
                metrics[metric] = float(value) * (100.0 if percentage else 1.0)
                meta = evidence.get("ratio_meta") or {}
                sources[metric] = (
                    f"financial_ratios kỳ {meta.get('ratio_date')} ({meta.get('frequency')}); "
                    f"DB cập nhật {meta.get('updated_at')}; published_date chưa xác minh"
                )
    if financial_sector:
        metrics["gpm"] = None
    metrics["_sources"] = {
        key: sources.get(key, "Agent-03/event input")
        for key, value in metrics.items() if value is not None and not key.startswith("_")
    }
    metrics["_gpm_not_applicable"] = financial_sector
    return metrics
