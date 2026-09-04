"""Bộ Ontology & Taxonomy Thực thể Tài chính Việt Nam Chuyên sâu (Senior Financial Analyst & Broker Level).

Được chuẩn hóa dựa trên phân tích thực tế 48 Báo cáo Tài chính (BCTC) sau OCR
tại `d:\\sag_48_bctc_markdown` (HPG, FTS, VCI, VND, HCM, VCB, TCB, ACB, MBB,
MWG, VHM, KDH, NKG, PDR, FPT, MSN, VNM, HSG...).

Bao gồm 8 Miền Tri thức Tài chính:
1. BCTC & Kỳ báo cáo (Report Type: riêng/hợp nhất/giữa niên độ/kiểm toán/soát xét; Kỳ báo cáo Q1.2026, năm 2025...)
2. Tài sản tài chính & Nợ (FVTPL, AFS, HTM, Chứng khoán kinh doanh, Trái phiếu, Dư nợ cho vay/ký quỹ, Nhóm nợ 1-5, Dự phòng, Tài sản đảm bảo, Phái sinh, Tỷ lệ thận trọng)
3. Khoản mục & Chính sách kế toán (Hàng tồn kho, TSCĐ, XDCB dở dang, BĐS đầu tư, Lợi thế thương mại, Nguyên tắc ghi nhận...)
4. Pháp nhân & Quản trị (Công ty con/liên kết/liên doanh kèm % sở hữu, Bên liên quan, Lãnh đạo, Cơ quan quản lý, Đơn vị tổ chức...)
5. Sự kiện vốn & Chuyển nhượng (Phát hành/ESOP, Cổ tức, Mua lại cổ phiếu quỹ, Thoái vốn, Góp vốn, Loại trừ hợp nhất...)
6. Ngành & Dự án (Hàng hóa, Dự án BĐS/năng lực sản xuất, Thương hiệu/chuỗi, Phân đoạn hoạt động, Tài nguyên khoáng sản...)
7. Vĩ mô, Văn bản & Chính sách (Chỉ số vĩ mô, Thông tư/Nghị định, Giấy phép, Nghị quyết, Chỉ số thị trường...)
8. Catalyst & Tin tức (Nguyên nhân thay đổi giá/lợi nhuận, Nguồn tin, Tin đồn...)
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class FinancialEntityType(StrEnum):
    # Domain 1: BCTC & Kỳ báo cáo
    TICKER = "TICKER"  # Mã cổ phiếu niêm yết / đăng ký GD (HPG, FTS, VCI, VCB, MWG, VHM, FPT, VNM...)
    REPORT_TYPE = "REPORT_TYPE"  # Loại BCTC: riêng / hợp nhất / giữa niên độ / kiểm toán / soát xét
    REPORT_PERIOD = "REPORT_PERIOD"  # Kỳ báo cáo: Q1.2026, năm 2025, 31/3/2026, kỳ 3 tháng kết thúc...

    # Domain 2: Tài sản tài chính & Nợ
    FINANCIAL_ASSET_CLASS = "FINANCIAL_ASSET_CLASS"  # FVTPL, AFS, HTM, Chứng khoán kinh doanh, Trái phiếu (CP/TCTD/doanh nghiệp), Chứng chỉ tiền gửi
    INVESTMENT_PORTFOLIO_HOLDING = "INVESTMENT_PORTFOLIO_HOLDING"  # Mã cổ phiếu/trái phiếu trong danh mục tự doanh (VD: cổ phiếu niêm yết/chưa niêm yết của CTCK)
    LOAN_PORTFOLIO = "LOAN_PORTFOLIO"  # Dư nợ cho vay: cho vay khách hàng, cho vay ký quỹ (margin), ứng trước tiền bán chứng khoán, cho vay TCTD
    DEBT_CLASSIFICATION = "DEBT_CLASSIFICATION"  # Nhóm nợ 1-5: Nợ đủ tiêu chuẩn / Nợ cần chú ý / Nợ dưới tiêu chuẩn / Nợ nghi ngờ / Nợ có khả năng mất vốn
    PROVISION_TYPE = "PROVISION_TYPE"  # Dự phòng rủi ro tín dụng cụ thể/chung, dự phòng giảm giá, dự phòng tổn thất đầu tư (kèm tỷ lệ trích + cơ sở pháp lý)
    COLLATERAL = "COLLATERAL"  # Tài sản đảm bảo: cổ phiếu niêm yết, quyền sử dụng đất, TSCĐ, hàng hóa
    DERIVATIVE_INSTRUMENT = "DERIVATIVE_INSTRUMENT"  # Công cụ phái sinh: hợp đồng kỳ hạn, quyền chọn, phái sinh lãi suất/tỷ giá/giá cả hàng hóa
    PRUDENTIAL_RATIO = "PRUDENTIAL_RATIO"  # Tỷ lệ thận trọng: tỷ lệ ký quỹ ban đầu/duy trì, tỷ lệ an toàn vốn, tỷ lệ dự phòng chung (0,75%)
    OFF_BALANCE_COMMITMENT = "OFF_BALANCE_COMMITMENT"  # Cam kết ngoại bảng: cam kết tín dụng, bảo lãnh, cam kết thanh toán chưa ghi sổ

    # Domain 3: Khoản mục & Chính sách kế toán
    ACCOUNTING_ITEM = "ACCOUNTING_ITEM"  # Hàng tồn kho, Phải thu, Chi phí XDCB dở dang, Nợ vay, Thặng dư vốn cổ phần, Cổ phiếu quỹ...
    INVENTORY_TYPE = "INVENTORY_TYPE"  # Phân loại tồn kho: nguyên liệu/vật liệu, thành phẩm, hàng hóa, BĐS xây dựng dở dang
    FINANCIAL_METRIC = "FINANCIAL_METRIC"  # Chỉ tiêu tài chính: ROIC, NIM, NPL, FCF, Gross Margin, P/E, P/B, D/E, tỷ lệ margin/VCSH...
    ACCOUNTING_POLICY = "ACCOUNTING_POLICY"  # Chính sách kế toán: khấu hao đường thẳng, giá thấp hơn giữa giá gốc & giá trị thuần, dồn tích
    INVESTMENT_IN_SUB_ASSOC = "INVESTMENT_IN_SUB_ASSOC"  # Khoản đầu tư vào công ty con/liên kết/liên doanh theo giá gốc (kèm dự phòng)
    MINORITY_INTEREST = "MINORITY_INTEREST"  # Lợi ích cổ đông không kiểm soát trong BCTC hợp nhất
    AUDITOR_OPINION = "AUDITOR_OPINION"  # Ý kiến kiểm toán: Chấp nhận toàn phần, Ngoại trừ, Từ chối, Lưu ý...
    AUDIT_FIRM = "AUDIT_FIRM"  # Công ty kiểm toán: KPMG, EY, Deloitte, PwC, Grant Thornton...
    ACCOUNTING_CIRCULAR = "ACCOUNTING_CIRCULAR"  # Thông tư/Nghị định kế toán: TT 99/2025/TT-BTC, TT 202/2014, TT 200/2014, TT 48/2019, NĐ 86/2024, TT 31/2024/TT-NHNN...

    # Domain 4: Pháp nhân & Quản trị
    COMPANY = "COMPANY"  # Pháp nhân: công ty, ngân hàng thương mại, công ty chứng khoán, công ty bảo hiểm, công ty mẹ
    SUBSIDIARY_AFFILIATE = "SUBSIDIARY_AFFILIATE"  # Công ty con/liên kết/liên doanh (kèm % sở hữu, % quyền biểu quyết, trực tiếp/gián tiếp)
    RELATED_PARTY = "RELATED_PARTY"  # Bên liên quan: công ty mẹ, cổ đông lớn, giao dịch RPT, tài khoản thanh toán chung
    EXECUTIVE_INSIDER = "EXECUTIVE_INSIDER"  # Chủ tịch HĐQT, CEO/TGĐ, Kế toán trưởng, thành viên HĐQT (điều hành/không điều hành/độc lập)
    REGULATORY_BODY = "REGULATORY_BODY"  # Cơ quan quản lý: UBCKNN, NHNNVN, Bộ Tài chính, Sở GDCK, Tổng Công ty Lưu ký & Bù trừ CKVN
    ORGANIZATIONAL_UNIT = "ORGANIZATIONAL_UNIT"  # Đơn vị tổ chức: chi nhánh, văn phòng đại diện, phòng giao dịch, đơn vị hạch toán phụ thuộc
    COUNTERPARTY = "COUNTERPARTY"  # Bên đối tác: TCTD khác, nhà đầu tư mua cổ phần, bên bán, đối tác góp vốn

    # Domain 5: Sự kiện vốn & Chuyển nhượng
    CAPITAL_EVENT = "CAPITAL_EVENT"  # Phát hành riêng lẻ, ESOP, Trả cổ tức tiền/cổ phiếu, Thưởng cổ phiếu, Mua lại cổ phiếu quỹ
    CAPITAL_TRANSACTION_PARTY = "CAPITAL_TRANSACTION_PARTY"  # Bên trong giao dịch vốn: bên chuyển nhượng, bên mua, công ty mục tiêu (thoái vốn, góp vốn, M&A)

    # Domain 6: Ngành & Dự án
    COMMODITY_TICKER = "COMMODITY_TICKER"  # Giá HRC, Thép cuộn, Ure, Dầu WTI/Brent, Vàng, Thịt heo, Phốt pho vàng...
    REAL_ESTATE_PROJECT = "REAL_ESTATE_PROJECT"  # Dự án BĐS: Vinhomes Ocean Park 2/3, Vinhomes Golden City, Khang Điền...
    PROJECT_CAPACITY = "PROJECT_CAPACITY"  # Dự án năng lực sản xuất: Dung Quất 2, Mỏ Bô-xít, Cảng Gemalink, Lô B Ô Môn, Công trình XDCB dở dang
    MINERAL_RESOURCE = "MINERAL_RESOURCE"  # Tài nguyên khoáng sản: quặng, mỏ (VD: Núi Pháo)
    BRAND_PRODUCT_LINE = "BRAND_PRODUCT_LINE"  # Thương hiệu/dòng sản phẩm: Thế Giới Di Động, Điện Máy Xanh, Bách Hóa Xanh, nhãn hiệu
    OPERATING_SEGMENT = "OPERATING_SEGMENT"  # Phân đoạn hoạt động: Môi giới, Tự doanh, Kinh doanh vốn, Tư vấn, Bán lẻ...
    SUPPLY_CHAIN_PARTNER = "SUPPLY_CHAIN_PARTNER"  # Khách hàng OEM, Đối tác bao tiêu, Supplier, nhà cung cấp

    # Domain 7: Vĩ mô, Văn bản & Chính sách
    MACRO_INDICATOR = "MACRO_INDICATOR"  # Lãi suất SBV, Tỷ giá USD/VND, CPI, Lợi suất TPCP 10Y, FED Rate...
    MARKET_INDEX = "MARKET_INDEX"  # VN-Index, VN30, FTSE Vietnam, MSCI Frontier, ETF Diamond...
    POLICY_DOCUMENT = "POLICY_DOCUMENT"  # Nghị định 65/08, Nghị định 86/2024/NĐ-CP, Luật TCTD 32/2024/QH15, Luật Đất đai, QH Điện VIII...
    LICENSE_DOCUMENT = "LICENSE_DOCUMENT"  # Giấy phép: Giấy phép Thành lập & Hoạt động, GP-UBCK, GP/KDBH, Giấy CN ĐKDN
    RESOLUTION_DOCUMENT = "RESOLUTION_DOCUMENT"  # Nghị quyết: Nghị quyết ĐHĐCĐ, Nghị quyết HĐQT (VD: 01/NQ/ĐHĐCĐ/2025)

    # Domain 8: Catalyst & Tin tức
    CATALYST_TYPE = "CATALYST_TYPE"  # Chuyển sàn HOSE, Thoái vốn SCIC, Ký hợp đồng EPC, Thắng kiện...
    NEWS_SOURCE = "NEWS_SOURCE"  # VTV, CafeF, VnEconomy, FiinPro, Báo Đầu tư...
    RUMOR_CLAIM = "RUMOR_CLAIM"  # Tin đồn bán vốn, Tin đồn lỗ/lãi đột biến, Tin đồn thanh tra...


class FinancialEventType(StrEnum):
    # BCTC & Kế toán & Phân loại
    REVENUE_EBITDA_SHOCK = "REVENUE_EBITDA_SHOCK"
    DEBT_RESTRUCTURING = "DEBT_RESTRUCTURING"
    PROVISION_ADJUSTMENT = "PROVISION_ADJUSTMENT"  # Trích lập / hoàn nhập dự phòng rủi ro, dự phòng giảm giá
    OFF_BALANCE_COMMITMENT = "OFF_BALANCE_COMMITMENT"  # Cam kết tín dụng/bảo lãnh ngoài bảng cân đối
    ACCOUNTING_POLICY_RECLASSIFICATION = "ACCOUNTING_POLICY_RECLASSIFICATION"  # Thay đổi chính sách kế toán / phân loại lại chỉ tiêu (VD: TT 99/2025)
    DEBT_CLASSIFICATION_CHANGE = "DEBT_CLASSIFICATION_CHANGE"  # Chuyển nhóm nợ, phân loại lại nợ xấu

    # Quản trị & Công ty con
    SUBSIDIARY_PROFIT_RECOGNITION = "SUBSIDIARY_PROFIT_RECOGNITION"  # Ghi nhận lợi nhuận / Cổ tức / Dự phòng giảm giá đầu tư công ty con
    RELATED_PARTY_TRANSACTION = "RELATED_PARTY_TRANSACTION"  # Giao dịch bên liên quan: cho vay, tài khoản thanh toán chung, hợp tác đầu tư
    INSIDER_BUY_SELL = "INSIDER_BUY_SELL"
    CAPITAL_INCREASE_DILUTION = "CAPITAL_INCREASE_DILUTION"  # Phát hành cổ phiếu mới / ESOP / tăng vốn điều lệ
    DIVIDEND_DECLARATION = "DIVIDEND_DECLARATION"  # Công bố / chi trả cổ tức, thưởng cổ phiếu
    SHARE_BUYBACK = "SHARE_BUYBACK"  # Mua lại cổ phiếu quỹ
    OWNERSHIP_CHANGE = "OWNERSHIP_CHANGE"  # Thay đổi tỷ lệ sở hữu: góp vốn, chuyển nhượng vốn, tăng/giảm vốn
    DIVESTITURE = "DIVESTITURE"  # Thoái vốn, chuyển nhượng công ty con, bán cổ phần (VD: PDR bán 94% Ngô Mây)
    CONSOLIDATION_EXCLUSION = "CONSOLIDATION_EXCLUSION"  # Loại trừ khỏi hợp nhất: mất quyền kiểm soát, ngân hàng chuyển giao bắt buộc
    BOARD_DISPUTE = "BOARD_DISPUTE"
    PERSONNEL_CHANGE = "PERSONNEL_CHANGE"  # Bổ nhiệm / miễn nhiệm / hết nhiệm kỳ lãnh đạo, thành viên HĐQT

    # Tự doanh & Cho vay Margin (Đặc thù CTCK / Ngân hàng)
    FVTPL_FAIR_VALUE_REVALUATION = "FVTPL_FAIR_VALUE_REVALUATION"  # Chênh lệch tăng/giảm do đánh giá lại TSTC FVTPL theo giá thị trường
    MARGIN_LENDING_CHANGE = "MARGIN_LENDING_CHANGE"  # Biến động dư nợ cho vay ký quỹ / ứng trước tiền bán
    SELF_TRADING_PROFIT_LOSS = "SELF_TRADING_PROFIT_LOSS"  # Biến động lãi/lỗ hoạt động tự doanh chứng khoán

    # Ngành & Chuỗi cung ứng
    CAPACITY_EXPANSION_COMMISSIONING = "CAPACITY_EXPANSION_COMMISSIONING"
    COMMODITY_PRICE_SPIKE = "COMMODITY_PRICE_SPIKE"
    SUPPLY_CHAIN_DISRUPTION = "SUPPLY_CHAIN_DISRUPTION"

    # Vĩ mô & Chính sách
    RATE_CUT_HIKE = "RATE_CUT_HIKE"
    CURRENCY_DEPRECIATION = "CURRENCY_DEPRECIATION"
    POLICY_RELEASE_EFFECT = "POLICY_RELEASE_EFFECT"

    # Catalyst & Thị trường
    INDEX_REBALANCING = "INDEX_REBALANCING"
    PRIVATIZATION_DIVESTMENT = "PRIVATIZATION_DIVESTMENT"
    LEGAL_APPROVAL_MILESTONE = "LEGAL_APPROVAL_MILESTONE"

    # News, Tin đồn & Giải trình
    EARNINGS_EXPLANATION_SHOCK = "EARNINGS_EXPLANATION_SHOCK"  # Giải trình chênh lệch lợi nhuận gửi UBCK/Sở GDCK
    RUMOR_EMERGENCE = "RUMOR_EMERGENCE"
    OFFICIAL_DENIAL_CONFIRMATION = "OFFICIAL_DENIAL_CONFIRMATION"
    MEDIA_CAMPAIGN = "MEDIA_CAMPAIGN"


# Mô tả tiếng Việt cho từng entity type, dùng trong prompt trích xuất.
_ENTITY_TYPE_DESCRIPTIONS: dict[str, str] = {
    "TICKER": "Mã cổ phiếu niêm yết / đăng ký GD (HPG, FTS, VCI, VCB, MWG, VHM, FPT, VNM...)",
    "REPORT_TYPE": "Loại BCTC: riêng / hợp nhất / giữa niên độ / kiểm toán / soát xét",
    "REPORT_PERIOD": "Kỳ báo cáo: Q1.2026, năm 2025, 31/3/2026, kỳ 3 tháng kết thúc...",
    "FINANCIAL_ASSET_CLASS": "Phân loại tài sản tài chính: FVTPL, AFS, HTM, Chứng khoán kinh doanh, Trái phiếu (CP/TCTD/doanh nghiệp), Chứng chỉ tiền gửi",
    "INVESTMENT_PORTFOLIO_HOLDING": "Mã cổ phiếu/trái phiếu trong danh mục tự doanh (VD: cổ phiếu niêm yết/chưa niêm yết của CTCK)",
    "LOAN_PORTFOLIO": "Dư nợ cho vay: cho vay khách hàng, cho vay ký quỹ (margin), ứng trước tiền bán chứng khoán, cho vay TCTD",
    "DEBT_CLASSIFICATION": "Nhóm nợ 1-5: Nợ đủ tiêu chuẩn / Nợ cần chú ý / Nợ dưới tiêu chuẩn / Nợ nghi ngờ / Nợ có khả năng mất vốn",
    "PROVISION_TYPE": "Dự phòng rủi ro tín dụng cụ thể/chung, dự phòng giảm giá, dự phòng tổn thất đầu tư (kèm tỷ lệ trích + cơ sở pháp lý)",
    "COLLATERAL": "Tài sản đảm bảo: cổ phiếu niêm yết, quyền sử dụng đất, TSCĐ, hàng hóa",
    "DERIVATIVE_INSTRUMENT": "Công cụ phái sinh: hợp đồng kỳ hạn, quyền chọn, phái sinh lãi suất/tỷ giá/giá cả hàng hóa",
    "PRUDENTIAL_RATIO": "Tỷ lệ thận trọng: tỷ lệ ký quỹ ban đầu/duy trì, tỷ lệ an toàn vốn, tỷ lệ dự phòng chung (0,75%)",
    "OFF_BALANCE_COMMITMENT": "Cam kết ngoại bảng: cam kết tín dụng, bảo lãnh, cam kết thanh toán chưa ghi sổ",
    "ACCOUNTING_ITEM": "Hàng tồn kho, Phải thu, Chi phí XDCB dở dang, Nợ vay, Thặng dư vốn cổ phần, Cổ phiếu quỹ...",
    "INVENTORY_TYPE": "Phân loại tồn kho: nguyên liệu/vật liệu, thành phẩm, hàng hóa, BĐS xây dựng dở dang",
    "FINANCIAL_METRIC": "Chỉ tiêu tài chính: ROIC, NIM, NPL, FCF, Gross Margin, P/E, P/B, D/E, tỷ lệ margin/VCSH...",
    "ACCOUNTING_POLICY": "Chính sách kế toán: khấu hao đường thẳng, giá thấp hơn giữa giá gốc & giá trị thuần, dồn tích",
    "INVESTMENT_IN_SUB_ASSOC": "Khoản đầu tư vào công ty con/liên kết/liên doanh theo giá gốc (kèm dự phòng)",
    "MINORITY_INTEREST": "Lợi ích cổ đông không kiểm soát trong BCTC hợp nhất",
    "AUDITOR_OPINION": "Ý kiến kiểm toán: Chấp nhận toàn phần, Ngoại trừ, Từ chối, Lưu ý...",
    "AUDIT_FIRM": "Công ty kiểm toán: KPMG, EY, Deloitte, PwC, Grant Thornton...",
    "ACCOUNTING_CIRCULAR": "Thông tư/Nghị định kế toán: TT 99/2025/TT-BTC, TT 202/2014, TT 200/2014, TT 48/2019, NĐ 86/2024, TT 31/2024/TT-NHNN...",
    "COMPANY": "Pháp nhân: công ty, ngân hàng thương mại, công ty chứng khoán, công ty bảo hiểm, công ty mẹ",
    "SUBSIDIARY_AFFILIATE": "Công ty con/liên kết/liên doanh (kèm % sở hữu, % quyền biểu quyết, trực tiếp/gián tiếp)",
    "RELATED_PARTY": "Bên liên quan: công ty mẹ, cổ đông lớn, giao dịch RPT, tài khoản thanh toán chung",
    "EXECUTIVE_INSIDER": "Chủ tịch HĐQT, CEO/TGĐ, Kế toán trưởng, thành viên HĐQT (điều hành/không điều hành/độc lập)",
    "REGULATORY_BODY": "Cơ quan quản lý: UBCKNN, NHNNVN, Bộ Tài chính, Sở GDCK, Tổng Công ty Lưu ký & Bù trừ CKVN",
    "ORGANIZATIONAL_UNIT": "Đơn vị tổ chức: chi nhánh, văn phòng đại diện, phòng giao dịch, đơn vị hạch toán phụ thuộc",
    "COUNTERPARTY": "Bên đối tác: TCTD khác, nhà đầu tư mua cổ phần, bên bán, đối tác góp vốn",
    "CAPITAL_EVENT": "Phát hành riêng lẻ, ESOP, Trả cổ tức tiền/cổ phiếu, Thưởng cổ phiếu, Mua lại cổ phiếu quỹ",
    "CAPITAL_TRANSACTION_PARTY": "Bên trong giao dịch vốn: bên chuyển nhượng, bên mua, công ty mục tiêu (thoái vốn, góp vốn, M&A)",
    "COMMODITY_TICKER": "Giá HRC, Thép cuộn, Ure, Dầu WTI/Brent, Vàng, Thịt heo, Phốt pho vàng...",
    "REAL_ESTATE_PROJECT": "Dự án BĐS: Vinhomes Ocean Park 2/3, Vinhomes Golden City, Khang Điền...",
    "PROJECT_CAPACITY": "Dự án năng lực sản xuất: Dung Quất 2, Mỏ Bô-xít, Cảng Gemalink, Lô B Ô Môn, Công trình XDCB dở dang",
    "MINERAL_RESOURCE": "Tài nguyên khoáng sản: quặng, mỏ (VD: Núi Pháo)",
    "BRAND_PRODUCT_LINE": "Thương hiệu/dòng sản phẩm: Thế Giới Di Động, Điện Máy Xanh, Bách Hóa Xanh, nhãn hiệu",
    "OPERATING_SEGMENT": "Phân đoạn hoạt động: Môi giới, Tự doanh, Kinh doanh vốn, Tư vấn, Bán lẻ...",
    "SUPPLY_CHAIN_PARTNER": "Khách hàng OEM, Đối tác bao tiêu, Supplier, nhà cung cấp",
    "MACRO_INDICATOR": "Lãi suất SBV, Tỷ giá USD/VND, CPI, Lợi suất TPCP 10Y, FED Rate...",
    "MARKET_INDEX": "VN-Index, VN30, FTSE Vietnam, MSCI Frontier, ETF Diamond...",
    "POLICY_DOCUMENT": "Nghị định 65/08, Nghị định 86/2024/NĐ-CP, Luật TCTD 32/2024/QH15, Luật Đất đai, QH Điện VIII...",
    "LICENSE_DOCUMENT": "Giấy phép: Giấy phép Thành lập & Hoạt động, GP-UBCK, GP/KDBH, Giấy CN ĐKDN",
    "RESOLUTION_DOCUMENT": "Nghị quyết: Nghị quyết ĐHĐCĐ, Nghị quyết HĐQT (VD: 01/NQ/ĐHĐCĐ/2025)",
    "CATALYST_TYPE": "Chuyển sàn HOSE, Thoái vốn SCIC, Ký hợp đồng EPC, Thắng kiện...",
    "NEWS_SOURCE": "VTV, CafeF, VnEconomy, FiinPro, Báo Đầu tư...",
    "RUMOR_CLAIM": "Tin đồn bán vốn, Tin đồn lỗ/lãi đột biến, Tin đồn thanh tra...",
}
from functools import cache

# Map doc_types (matching ai-engine unified_rag_service) to Domain IDs (1 to 8)
DOC_TYPE_DOMAINS: dict[str, tuple[int, ...]] = {
    "financial_statement": (1, 2, 3, 4),
    "analyst_report": (2, 4, 6, 8),
    "annual_report": (4, 5, 6, 7),
    "agm_resolution": (4, 5, 7),
    "news": (7, 8),
    "social_media": (7, 8),
}

DOMAIN_ENTITY_TYPES: dict[int, tuple[FinancialEntityType, ...]] = {
    1: (FinancialEntityType.TICKER, FinancialEntityType.REPORT_TYPE, FinancialEntityType.REPORT_PERIOD),
    2: (
        FinancialEntityType.FINANCIAL_ASSET_CLASS,
        FinancialEntityType.INVESTMENT_PORTFOLIO_HOLDING,
        FinancialEntityType.LOAN_PORTFOLIO,
        FinancialEntityType.DEBT_CLASSIFICATION,
        FinancialEntityType.PROVISION_TYPE,
        FinancialEntityType.COLLATERAL,
        FinancialEntityType.DERIVATIVE_INSTRUMENT,
        FinancialEntityType.PRUDENTIAL_RATIO,
        FinancialEntityType.OFF_BALANCE_COMMITMENT,
    ),
    3: (
        FinancialEntityType.ACCOUNTING_ITEM,
        FinancialEntityType.INVENTORY_TYPE,
        FinancialEntityType.FINANCIAL_METRIC,
        FinancialEntityType.ACCOUNTING_POLICY,
        FinancialEntityType.INVESTMENT_IN_SUB_ASSOC,
        FinancialEntityType.MINORITY_INTEREST,
        FinancialEntityType.AUDITOR_OPINION,
        FinancialEntityType.AUDIT_FIRM,
        FinancialEntityType.ACCOUNTING_CIRCULAR,
    ),
    4: (
        FinancialEntityType.COMPANY,
        FinancialEntityType.SUBSIDIARY_AFFILIATE,
        FinancialEntityType.RELATED_PARTY,
        FinancialEntityType.EXECUTIVE_INSIDER,
        FinancialEntityType.REGULATORY_BODY,
        FinancialEntityType.ORGANIZATIONAL_UNIT,
        FinancialEntityType.COUNTERPARTY,
    ),
    5: (FinancialEntityType.CAPITAL_EVENT, FinancialEntityType.CAPITAL_TRANSACTION_PARTY),
    6: (
        FinancialEntityType.COMMODITY_TICKER,
        FinancialEntityType.REAL_ESTATE_PROJECT,
        FinancialEntityType.PROJECT_CAPACITY,
        FinancialEntityType.MINERAL_RESOURCE,
        FinancialEntityType.BRAND_PRODUCT_LINE,
        FinancialEntityType.OPERATING_SEGMENT,
        FinancialEntityType.SUPPLY_CHAIN_PARTNER,
    ),
    7: (
        FinancialEntityType.MACRO_INDICATOR,
        FinancialEntityType.MARKET_INDEX,
        FinancialEntityType.POLICY_DOCUMENT,
        FinancialEntityType.LICENSE_DOCUMENT,
        FinancialEntityType.RESOLUTION_DOCUMENT,
    ),
    8: (FinancialEntityType.CATALYST_TYPE, FinancialEntityType.NEWS_SOURCE, FinancialEntityType.RUMOR_CLAIM),
}

DOMAIN_EVENT_TYPES: dict[int, tuple[FinancialEventType, ...]] = {
    1: (FinancialEventType.REVENUE_EBITDA_SHOCK, FinancialEventType.EARNINGS_EXPLANATION_SHOCK),
    2: (
        FinancialEventType.DEBT_RESTRUCTURING,
        FinancialEventType.PROVISION_ADJUSTMENT,
        FinancialEventType.OFF_BALANCE_COMMITMENT,
        FinancialEventType.DEBT_CLASSIFICATION_CHANGE,
        FinancialEventType.FVTPL_FAIR_VALUE_REVALUATION,
        FinancialEventType.MARGIN_LENDING_CHANGE,
        FinancialEventType.SELF_TRADING_PROFIT_LOSS,
    ),
    3: (FinancialEventType.ACCOUNTING_POLICY_RECLASSIFICATION, FinancialEventType.SUBSIDIARY_PROFIT_RECOGNITION),
    4: (
        FinancialEventType.RELATED_PARTY_TRANSACTION,
        FinancialEventType.INSIDER_BUY_SELL,
        FinancialEventType.PERSONNEL_CHANGE,
        FinancialEventType.BOARD_DISPUTE,
        FinancialEventType.CONSOLIDATION_EXCLUSION,
    ),
    5: (
        FinancialEventType.CAPITAL_INCREASE_DILUTION,
        FinancialEventType.DIVIDEND_DECLARATION,
        FinancialEventType.SHARE_BUYBACK,
        FinancialEventType.OWNERSHIP_CHANGE,
        FinancialEventType.DIVESTITURE,
        FinancialEventType.PRIVATIZATION_DIVESTMENT,
    ),
    6: (
        FinancialEventType.CAPACITY_EXPANSION_COMMISSIONING,
        FinancialEventType.COMMODITY_PRICE_SPIKE,
        FinancialEventType.SUPPLY_CHAIN_DISRUPTION,
    ),
    7: (
        FinancialEventType.RATE_CUT_HIKE,
        FinancialEventType.CURRENCY_DEPRECIATION,
        FinancialEventType.POLICY_RELEASE_EFFECT,
        FinancialEventType.INDEX_REBALANCING,
        FinancialEventType.LEGAL_APPROVAL_MILESTONE,
    ),
    8: (
        FinancialEventType.RUMOR_EMERGENCE,
        FinancialEventType.OFFICIAL_DENIAL_CONFIRMATION,
        FinancialEventType.MEDIA_CAMPAIGN,
    ),
}


@cache
def get_financial_extraction_prompt(doc_type: str | None = None, is_full_document: bool = True) -> str:
    """Trả về Extraction Prompt được tinh gọn theo loại tài liệu (doc_type). Cache RAM 100%."""
    if doc_type and doc_type in DOC_TYPE_DOMAINS:
        domains = DOC_TYPE_DOMAINS[doc_type]
        entities = []
        events = []
        for d in domains:
            entities.extend(DOMAIN_ENTITY_TYPES.get(d, ()))
            events.extend(DOMAIN_EVENT_TYPES.get(d, ()))
        entity_types = tuple(dict.fromkeys(entities))
        event_types = tuple(dict.fromkeys(events))
    else:
        entity_types = tuple(FinancialEntityType)
        event_types = tuple(FinancialEventType)

    entity_lines = "\n".join(
        f"- {e.value}: {_ENTITY_TYPE_DESCRIPTIONS.get(e.value, e.value)}" for e in entity_types
    )
    event_lines = ", ".join(e.value for e in event_types)

    if is_full_document:
        instructions = """Nhiệm vụ của bạn là phân tích TOÀN BỘ tài liệu này (Thuyết minh Báo cáo Tài chính hoặc Báo cáo Quản trị / Báo cáo Thường niên) và trích xuất TOÀN DIỆN:
1. Danh sách TẤT CẢ các Sự kiện tài chính & quản trị trọng yếu (Events) xuất hiện trong tài liệu (gồm: Thuyết minh Nợ vay & Ngân hàng chủ nợ, Giao dịch các bên liên quan RPT, Danh mục đầu tư/Tự doanh FVTPL, Đầu tư công ty con/liên kết, Chi phí XDCB dở dang / Dự án CapEx, Cơ cấu cổ đông, Nghị quyết & Giao dịch người nội bộ/HĐQT, v.v.).
2. Danh sách ĐẦY ĐỦ các Thực thể (Entities) và Quan hệ (Relations) với số tiền VND và chiều mũi tên quan hệ chuẩn xác phục vụ phân tích cấu trúc sở hữu, dòng tiền và rủi ro bên liên quan cho đồ thị GIL."""
    else:
        instructions = """Nhiệm vụ của bạn là phân tích đoạn văn bản Thuyết minh BCTC / Báo cáo Quản trị này và trích xuất:
1. Một Event chính đại diện cho ngữ cảnh đầy đủ của đoạn văn bản hoặc bảng số liệu.
2. Các Entity chỉ mục liên quan theo bộ Taxonomy tinh gọn sau:"""

    return f"""Bạn là Chuyên gia Phân tích Tài chính & Senior Broker hàng đầu tại Thị trường Chứng khoán Việt Nam.
{instructions}

[CÁC LOẠI THỰC THỂ (ENTITY TYPES)]
{entity_lines}

[CÁC LOẠI SỰ KIỆN (EVENT TYPES)]
{event_lines}

LƯU Ý ĐẶC BIỆT KHI XỬ LÝ BẢNG BIỂU & SỐ LIỆU TÀI CHÍNH:
- Đọc kỹ các bảng số liệu để nhận diện danh mục tự doanh (FVTPL), công ty con (kèm % sở hữu), nhóm nợ (1-5), số dư Margin và giao dịch bên liên quan (RPT).
- Bắt buộc chuẩn hóa số tiền về đơn vị VND đầy đủ (ví dụ: '500 triệu đồng' -> 500,000,000; '120 tỷ đồng' -> 120,000,000,000).
- Kết nối chính xác tên cổ phiếu, dự án, bên vay và bên cho vay.

Hãy trả về kết quả định dạng JSON khớp với schema yêu cầu.
"""


@cache
def get_financial_extraction_schema(doc_type: str | None = None) -> dict[str, Any]:
    """Trả về JSON Schema định nghĩa định dạng output cho Extractor (được cache trong RAM)."""
    if doc_type and doc_type in DOC_TYPE_DOMAINS:
        domains = DOC_TYPE_DOMAINS[doc_type]
        entities = []
        events = []
        for d in domains:
            entities.extend(DOMAIN_ENTITY_TYPES.get(d, ()))
            events.extend(DOMAIN_EVENT_TYPES.get(d, ()))
        entity_enums = [e.value for e in dict.fromkeys(entities)]
        event_enums = [e.value for e in dict.fromkeys(events)]
    else:
        entity_enums = [e.value for e in FinancialEntityType]
        event_enums = [e.value for e in FinancialEventType]

    return {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Tiêu đề tóm tắt sự kiện tài chính (dưới 15 từ)"},
            "summary": {"type": "string", "description": "Tóm tắt ngữ cảnh chính của đoạn văn bản hoặc bảng BCTC"},
            "category": {
                "type": "string",
                "enum": event_enums,
                "description": "Phân loại loại hình sự kiện tài chính",
            },
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Tên thực thể (VD: HPG, VCB, FVTPL, Nợ Nhóm 5, Dung Quất 2)"},
                        "type": {
                            "type": "string",
                            "enum": entity_enums,
                            "description": "Phân loại thực thể tài chính",
                        },
                    },
                    "required": ["name", "type"],
                },
            },
        },
        "required": ["title", "summary", "category", "entities"],
    }


_ENTITY_TYPE_LINES = "\n".join(
    f"- {e.value}: {_ENTITY_TYPE_DESCRIPTIONS.get(e.value, e.value)}" for e in FinancialEntityType
)

FINANCIAL_EXTRACTION_PROMPT = get_financial_extraction_prompt()


def infer_doc_type(title_or_path: str | None) -> str | None:
    """Suy luận doc_type từ tiêu đề hoặc đường dẫn file tài liệu."""
    if not title_or_path:
        return None
    text = title_or_path.casefold()
    if any(k in text for k in ("bctc", "báo cáo tài chính", "balance_sheet", "financial_statement")):
        return "financial_statement"
    if any(k in text for k in ("bctn", "báo cáo thường niên", "annual_report")):
        return "annual_report"
    if any(k in text for k in ("nghị quyết", "nghi quyet", "đhđcđ", "agm_resolution", "resolution")):
        return "agm_resolution"
    if any(k in text for k in ("báo cáo phân tích", "analyst_report", "khuyến nghị")):
        return "analyst_report"
    if any(k in text for k in ("tin tức", "news", "báo chí")):
        return "news"
    if any(k in text for k in ("mạng xã hội", "social_media", "forum", "diễn đàn")):
        return "social_media"
    return None


# ── Từ điển Ánh xạ Thực thể Chuẩn hóa (Canonical Entity & Alias Resolution) ──
# Ánh xạ toàn bộ các biến thể tên gọi thường gặp của doanh nghiệp về Canonical Ticker & Tên chuẩn
CANONICAL_TICKER_ALIASES: dict[str, tuple[str, str]] = {
    # Thép & Vật liệu
    "hpg": ("HPG", "TICKER"),
    "hòa phát": ("HPG", "TICKER"),
    "tập đoàn hòa phát": ("HPG", "TICKER"),
    "ctcp tập đoàn hòa phát": ("HPG", "TICKER"),
    "công ty cổ phần tập đoàn hòa phát": ("HPG", "TICKER"),
    "thép hòa phát": ("HPG", "TICKER"),
    "hoa phat group": ("HPG", "TICKER"),
    "hsg": ("HSG", "TICKER"),
    "hoa sen": ("HSG", "TICKER"),
    "tập đoàn hoa sen": ("HSG", "TICKER"),
    "ctcp tập đoàn hoa sen": ("HSG", "TICKER"),
    "nkg": ("NKG", "TICKER"),
    "nam kim": ("NKG", "TICKER"),
    "thép nam kim": ("NKG", "TICKER"),
    "ctcp thép nam kim": ("NKG", "TICKER"),

    # Chứng khoán
    "fts": ("FTS", "TICKER"),
    "chứng khoán fpt": ("FTS", "TICKER"),
    "ctcp chứng khoán fpt": ("FTS", "TICKER"),
    "fpt securities": ("FTS", "TICKER"),
    "vci": ("VCI", "TICKER"),
    "vietcap": ("VCI", "TICKER"),
    "chứng khoán vietcap": ("VCI", "TICKER"),
    "chứng khoán bản việt": ("VCI", "TICKER"),
    "ctcp chứng khoán vietcap": ("VCI", "TICKER"),
    "vnd": ("VND", "TICKER"),
    "vndirect": ("VND", "TICKER"),
    "chứng khoán vndirect": ("VND", "TICKER"),
    "ctcp chứng khoán vndirect": ("VND", "TICKER"),
    "hcm": ("HCM", "TICKER"),
    "hsc": ("HCM", "TICKER"),
    "chứng khoán hcm": ("HCM", "TICKER"),
    "chứng khoán tp hcm": ("HCM", "TICKER"),
    "ctcp chứng khoán thành phố hồ chí minh": ("HCM", "TICKER"),
    "ssi": ("SSI", "TICKER"),
    "chứng khoán ssi": ("SSI", "TICKER"),
    "ctcp chứng khoán ssi": ("SSI", "TICKER"),

    # Ngân hàng
    "vcb": ("VCB", "TICKER"),
    "vietcombank": ("VCB", "TICKER"),
    "ngân hàng ngoại thương": ("VCB", "TICKER"),
    "ngân hàng tmcp ngoại thương việt nam": ("VCB", "TICKER"),
    "tcb": ("TCB", "TICKER"),
    "techcombank": ("TCB", "TICKER"),
    "ngân hàng kỹ thương": ("TCB", "TICKER"),
    "ngân hàng tmcp kỹ thương việt nam": ("TCB", "TICKER"),
    "mbb": ("MBB", "TICKER"),
    "mb bank": ("MBB", "TICKER"),
    "ngân hàng quân đội": ("MBB", "TICKER"),
    "ngân hàng tmcp quân đội": ("MBB", "TICKER"),
    "acb": ("ACB", "TICKER"),
    "ngân hàng á châu": ("ACB", "TICKER"),
    "ngân hàng tmcp á châu": ("ACB", "TICKER"),
    "bid": ("BID", "TICKER"),
    "bidv": ("BID", "TICKER"),
    "ngân hàng đầu tư và phát triển việt nam": ("BID", "TICKER"),
    "ctg": ("CTG", "TICKER"),
    "vietinbank": ("CTG", "TICKER"),
    "ngân hàng công thương": ("CTG", "TICKER"),
    "vpbank": ("VPB", "TICKER"),
    "vpb": ("VPB", "TICKER"),

    # Công nghệ & Viễn thông
    "fpt": ("FPT", "TICKER"),
    "tập đoàn fpt": ("FPT", "TICKER"),
    "ctcp fpt": ("FPT", "TICKER"),
    "công ty cổ phần fpt": ("FPT", "TICKER"),

    # Bán lẻ & Tiêu dùng
    "mwg": ("MWG", "TICKER"),
    "thế giới di động": ("MWG", "TICKER"),
    "ctcp đầu tư thế giới di động": ("MWG", "TICKER"),
    "bách hóa xanh": ("MWG", "TICKER"),
    "điện máy xanh": ("MWG", "TICKER"),
    "vnm": ("VNM", "TICKER"),
    "vinamilk": ("VNM", "TICKER"),
    "sữa việt nam": ("VNM", "TICKER"),
    "ctcp sữa việt nam": ("VNM", "TICKER"),
    "msn": ("MSN", "TICKER"),
    "masan": ("MSN", "TICKER"),
    "tập đoàn masan": ("MSN", "TICKER"),
    "ctcp tập đoàn masan": ("MSN", "TICKER"),

    # Bất động sản
    "vhm": ("VHM", "TICKER"),
    "vinhomes": ("VHM", "TICKER"),
    "ctcp vinhomes": ("VHM", "TICKER"),
    "vic": ("VIC", "TICKER"),
    "vingroup": ("VIC", "TICKER"),
    "tập đoàn vingroup": ("VIC", "TICKER"),
    "kdh": ("KDH", "TICKER"),
    "khang điền": ("KDH", "TICKER"),
    "nhà khang điền": ("KDH", "TICKER"),
    "pdr": ("PDR", "TICKER"),
    "phát đạt": ("PDR", "TICKER"),
    "bất động sản phát đạt": ("PDR", "TICKER"),
    "dxg": ("DXG", "TICKER"),
    "đất xanh": ("DXG", "TICKER"),
    "tập đoàn đất xanh": ("DXG", "TICKER"),
    "nvl": ("NVL", "TICKER"),
    "novaland": ("NVL", "TICKER"),
    "tập đoàn novaland": ("NVL", "TICKER"),
}


def normalize_entity_text(value: str) -> str:
    """Chuẩn hóa chuỗi văn bản thực thể để tìm kiếm và so khớp."""
    if not value:
        return ""
    import re
    cleaned = value.strip().lower()
    cleaned = re.sub(r"[,\-_/\\.]+", " ", cleaned)
    return " ".join(cleaned.split())


from sag_api.sag.universe_registry import EntityResolutionResult, universe_registry


def resolve_canonical_entity(name: str, entity_type: str | None = None) -> tuple[str, str]:
    """Chuẩn hóa một thực thể về Canonical Ticker hoặc Tên chuẩn trong hệ thống.
    
    Sử dụng CanonicalEntityRegistry toàn diện với phân loại Company/Brand/Subsidiary/Project.
    Returns:
        (canonical_name, canonical_type)
    """
    raw_name = (name or "").strip()
    if not raw_name:
        return "", entity_type or "ENTITY"

    # 1. Tra cứu qua Dynamic Canonical Entity Registry
    res = universe_registry.resolve(raw_name)
    if res and res.confidence >= 0.70:
        return res.primary_ticker or res.canonical_name, res.entity_type

    # 2. Fallback cho chuỗi 3 ký tự viết hoa toàn bộ -> Coi là TICKER
    if len(raw_name) == 3 and raw_name.isalpha() and raw_name.isupper():
        return raw_name, "TICKER"

    return raw_name, entity_type or "COMPANY"


def extract_fiscal_metadata(text_or_title: str, reference_year: int = 2026) -> dict[str, Any]:
    """Bóc tách metadata tài chính (Năm, Quý, Loại BCTC, Ticker, Staleness Tier) từ tiêu đề hoặc text."""
    import re
    if not text_or_title:
        return {}

    text_clean = text_or_title.strip()
    # Thay thế dấu gạch dưới, gạch ngang, dấu chấm bằng khoảng trắng để regex nhận diện word boundaries chuẩn xác
    text_normalized = re.sub(r"[_./\\-]+", " ", text_clean)
    text_lower = text_normalized.lower()

    # 1. Bóc tách Ticker qua Universe Registry
    ticker = None
    res = universe_registry.resolve(text_normalized)
    if res and res.confidence >= 0.70:
        ticker = res.primary_ticker
    else:
        ticker_match = re.search(r"\b([A-Z]{3})\b", text_normalized)
        if ticker_match:
            cand = ticker_match.group(1)
            cand_res = universe_registry.resolve(cand)
            if cand_res and cand_res.confidence >= 0.70:
                ticker = cand_res.primary_ticker

    # 2. Bóc tách Năm tài chính (2010 -> 2030)
    year_match = re.search(r"\b(20[1-2][0-9]|2030)\b", text_normalized)
    fiscal_year = int(year_match.group(1)) if year_match else None

    # 3. Bóc tách Quý tài chính (Q1, Q2, Q3, Q4, Quý 1, Quý I...)
    fiscal_quarter = None
    q_match = re.search(r"\b(?:q|quý|quy)\s*([1-4]|i{1,3}|iv)\b", text_lower)
    if q_match:
        q_val = q_match.group(1).lower()
        q_map = {"1": 1, "2": 2, "3": 3, "4": 4, "i": 1, "ii": 2, "iii": 3, "iv": 4}
        fiscal_quarter = q_map.get(q_val)

    # 4. Bóc tách Loại Báo cáo (Hợp nhất / Riêng / Soát xét / Kiểm toán)
    report_scope = "CONSOLIDATED"  # Mặc định hợp nhất nếu không ghi rõ
    if any(k in text_lower for k in ("riêng", "mẹ", "công ty mẹ", "cong ty me", "separate", "parent")):
        report_scope = "SEPARATE"
    elif any(k in text_lower for k in ("hợp nhất", "hop nhat", "consolidated")):
        report_scope = "CONSOLIDATED"

    is_audited = any(k in text_lower for k in ("kiểm toán", "kiem toan", "audited"))
    is_reviewed = any(k in text_lower for k in ("soát xét", "soat xet", "reviewed", "giữa niên độ", "giua nien do"))

    period_label = ""
    if fiscal_quarter and fiscal_year:
        period_label = f"Q{fiscal_quarter}.{fiscal_year}"
    elif fiscal_year:
        period_label = f"Năm {fiscal_year}"

    # 5. Phân loại Vòng đời Tài liệu (Staleness Tier & Freshness Score)
    # Quy định: Tài liệu > 1 năm (4 quý) sẽ mất tính chất thời sự, chuyển sang HISTORICAL
    staleness_tier = "UNKNOWN"
    freshness_score = 0.70

    if fiscal_year:
        year_diff = reference_year - fiscal_year
        if year_diff == 0:
            # Cùng năm hiện hành
            staleness_tier = "FRESH"
            freshness_score = 1.0
        elif year_diff == 1:
            # 1 năm trước (4 quý gần nhất)
            staleness_tier = "ACTIVE"
            freshness_score = 0.85
        elif 2 <= year_diff <= 3:
            # 2 đến 3 năm (Dữ liệu Lịch sử đối chiếu)
            staleness_tier = "HISTORICAL"
            freshness_score = 0.40
        else:
            # > 3 năm (Hết hạn hiệu lực phân tích hiện tại)
            staleness_tier = "EXPIRED"
            freshness_score = 0.10

    return {
        "ticker": ticker,
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
        "report_scope": report_scope,
        "is_audited": is_audited,
        "is_reviewed": is_reviewed,
        "period_label": period_label,
        "staleness_tier": staleness_tier,
        "freshness_score": freshness_score,
    }


