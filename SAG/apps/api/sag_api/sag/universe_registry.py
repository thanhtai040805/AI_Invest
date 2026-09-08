"""Canonical Entity Registry cho Financial RAG (IOS v5.1).

Kiến trúc:
1. Phân biệt rõ ràng thực thể:
   - COMPANY: Pháp nhân doanh nghiệp niêm yết (vd: CTCP Bán lẻ Kỹ thuật số FPT)
   - SECURITY: Mã cổ phiếu niêm yết (vd: FRT)
   - SUBSIDIARY: Công ty con / thành viên (vd: FPT Software, WinCommerce)
   - BRAND: Thương hiệu / Chuỗi bán lẻ / Sản phẩm (vd: Nhà thuốc Long Châu, Điện máy Xanh)
   - PROJECT: Dự án BĐS / Công trình trọng điểm (vd: Vinhomes Ocean Park, Cảng Gemalink, Lò cao Dung Quất)
2. Phân cấp tra cứu (Hierarchical Resolution Pipeline):
   Exact Ticker -> Exact Alias -> Normalized Exact -> Longest Token-Boundary Substring -> Fuzzy -> UNKNOWN
3. Trả về EntityResolutionResult với đầy đủ canonical_id, entity_type, ticker, parent_ticker, confidence, match_type.
4. Hỗ trợ nạp động từ Database hoặc JSON (Extensible Registry).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Literal


def strip_vietnamese_diacritics(text: str) -> str:
    """Loại bỏ dấu tiếng Việt để phục vụ so khớp không dấu."""
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = re.sub(r"[\u0300-\u036f]", "", text)
    text = text.replace("đ", "d").replace("Đ", "D")
    return text


def normalize_text_for_matching(text: str) -> str:
    """Chuẩn hóa chuỗi text: lowercase, bỏ dấu câu thừa, giữ khoảng trắng chuẩn."""
    if not text:
        return ""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return " ".join(cleaned.split())


@dataclass(frozen=True, slots=True)
class CanonicalEntity:
    """Định nghĩa một thực thể chuẩn hóa trong hệ thống."""
    canonical_id: str                          # ID duy nhất, vd: "COMPANY_FRT", "BRAND_LONG_CHAU", "PROJECT_VHM_OCEAN_PARK"
    canonical_name: str                        # Tên chuẩn hiển thị
    entity_type: Literal["COMPANY", "SECURITY", "SUBSIDIARY", "BRAND", "PROJECT", "FACILITY"]
    ticker: str | None                         # Mã cổ phiếu niêm yết chính thức
    parent_company_id: str | None = None       # ID của công ty mẹ (nếu là subsidiary/brand/project)
    parent_ticker: str | None = None           # Ticker của công ty mẹ
    industry: str | None = None                # Phân ngành
    exchange: str = "HOSE"                     # Sàn giao dịch
    aliases: tuple[str, ...] = ()              # Danh sách alias định danh


@dataclass(frozen=True, slots=True)
class EntityResolutionResult:
    """Kết quả phân giải thực thể với đầy đủ độ tin cậy và ngữ cảnh phân loại."""
    canonical_id: str
    canonical_name: str
    entity_type: str
    ticker: str | None                         # Ticker chính (nếu entity là company/security)
    parent_ticker: str | None                  # Ticker công ty mẹ (nếu entity là brand/subsidiary/project)
    primary_ticker: str | None                 # Ticker để query dữ liệu tài chính (ticker or parent_ticker)
    match_type: Literal[
        "EXACT_TICKER",
        "EXACT_ALIAS",
        "NORMALIZED_EXACT_ALIAS",
        "LONGEST_TOKEN_MATCH",
        "FUZZY_MATCH",
        "UNKNOWN"
    ]
    confidence: float                          # Điểm tin cậy (0.0 -> 1.0)
    matched_alias: str
    original_query: str


# Danh bạ Thực thể Mẫu ban đầu (Core Registry) - Phân tách rõ Company, Subsidiary, Brand, Project
CORE_CANONICAL_ENTITIES: tuple[CanonicalEntity, ...] = (
    # ── 1. FPT RETAIL (FRT) & CÁC THƯƠNG HIỆU / CHUỖI ──
    CanonicalEntity(
        canonical_id="COMPANY_FRT",
        canonical_name="Công ty Cổ phần Bán lẻ Kỹ thuật số FPT",
        entity_type="COMPANY",
        ticker="FRT",
        industry="Bán lẻ",
        aliases=("frt", "fpt retail", "bán lẻ fpt", "ctcp bán lẻ kỹ thuật số fpt", "fpt digital retail")
    ),
    CanonicalEntity(
        canonical_id="BRAND_LONG_CHAU",
        canonical_name="Chuỗi Nhà thuốc FPT Long Châu",
        entity_type="BRAND",
        ticker=None,
        parent_company_id="COMPANY_FRT",
        parent_ticker="FRT",
        industry="Bán lẻ dược phẩm",
        aliases=("nhà thuốc long châu", "long châu", "long chau", "fpt long chau", "chuỗi long châu", "dược phẩm long châu")
    ),
    CanonicalEntity(
        canonical_id="BRAND_FPT_SHOP",
        canonical_name="Hệ thống FPT Shop",
        entity_type="BRAND",
        ticker=None,
        parent_company_id="COMPANY_FRT",
        parent_ticker="FRT",
        industry="Bán lẻ ICT",
        aliases=("fpt shop", "fptshop", "cửa hàng fpt shop")
    ),

    # ── 2. FPT CORPORATION (FPT) & CÔNG TY CON ──
    CanonicalEntity(
        canonical_id="COMPANY_FPT",
        canonical_name="Công ty Cổ phần FPT",
        entity_type="COMPANY",
        ticker="FPT",
        industry="Công nghệ thông tin",
        aliases=("fpt", "tập đoàn fpt", "ctcp fpt", "fpt corporation")
    ),
    CanonicalEntity(
        canonical_id="SUBSIDIARY_FPT_SOFTWARE",
        canonical_name="Công ty TNHH Phần mềm FPT (FPT Software)",
        entity_type="SUBSIDIARY",
        ticker=None,
        parent_company_id="COMPANY_FPT",
        parent_ticker="FPT",
        industry="Xuất khẩu phần mềm",
        aliases=("fpt software", "fsoft", "phần mềm fpt")
    ),
    CanonicalEntity(
        canonical_id="SUBSIDIARY_FPT_TELECOM",
        canonical_name="Công ty Cổ phần Viễn thông FPT (FPT Telecom)",
        entity_type="SUBSIDIARY",
        ticker="FOX",
        parent_company_id="COMPANY_FPT",
        parent_ticker="FPT",
        industry="Viễn thông",
        aliases=("fpt telecom", "viễn thông fpt", "fpt internet")
    ),

    # ── 3. THẾ GIỚI DI ĐỘNG (MWG) & CÁC CHUỖI BÁN LẺ ──
    CanonicalEntity(
        canonical_id="COMPANY_MWG",
        canonical_name="Công ty Cổ phần Đầu tư Thế Giới Di Động",
        entity_type="COMPANY",
        ticker="MWG",
        industry="Bán lẻ",
        aliases=("mwg", "thế giới di động", "tập đoàn mwg", "mobile world")
    ),
    CanonicalEntity(
        canonical_id="BRAND_BACH_HOA_XANH",
        canonical_name="Chuỗi Bách Hóa Xanh",
        entity_type="BRAND",
        ticker=None,
        parent_company_id="COMPANY_MWG",
        parent_ticker="MWG",
        industry="Bán lẻ thực phẩm tiêu dùng",
        aliases=("bách hóa xanh", "bach hoa xanh", "bhx", "chuỗi bách hóa xanh")
    ),
    CanonicalEntity(
        canonical_id="BRAND_DIEN_MAY_XANH",
        canonical_name="Chuỗi Điện Máy Xanh",
        entity_type="BRAND",
        ticker=None,
        parent_company_id="COMPANY_MWG",
        parent_ticker="MWG",
        industry="Bán lẻ điện máy",
        aliases=("điện máy xanh", "dien may xanh", "dmx")
    ),
    CanonicalEntity(
        canonical_id="BRAND_AN_KHANG",
        canonical_name="Nhà thuốc An Khang",
        entity_type="BRAND",
        ticker=None,
        parent_company_id="COMPANY_MWG",
        parent_ticker="MWG",
        industry="Bán lẻ dược phẩm",
        aliases=("nhà thuốc an khang", "an khang", "an khang pharmacy")
    ),

    # ── 4. MASAN GROUP (MSN) & WINCOMMERCE / WINMART ──
    CanonicalEntity(
        canonical_id="COMPANY_MSN",
        canonical_name="Công ty Cổ phần Tập đoàn Masan",
        entity_type="COMPANY",
        ticker="MSN",
        industry="Thực phẩm & Bán lẻ",
        aliases=("msn", "masan", "tập đoàn masan", "masan group")
    ),
    CanonicalEntity(
        canonical_id="BRAND_WINMART",
        canonical_name="Hệ thống WinMart / WinMart+ (WinCommerce)",
        entity_type="BRAND",
        ticker=None,
        parent_company_id="COMPANY_MSN",
        parent_ticker="MSN",
        industry="Bán lẻ tiêu dùng",
        aliases=("winmart", "winmart+", "wincommerce", "vinmart", "chuỗi winmart", "siêu thị winmart")
    ),
    CanonicalEntity(
        canonical_id="SUBSIDIARY_MASAN_CONSUMER",
        canonical_name="Công ty Cổ phần Hàng tiêu dùng Masan",
        entity_type="SUBSIDIARY",
        ticker="MCH",
        parent_company_id="COMPANY_MSN",
        parent_ticker="MSN",
        industry="Hàng tiêu dùng nhanh",
        aliases=("masan consumer", "mch", "hàng tiêu dùng masan")
    ),

    # ── 5. VINGROUP (VIC) & VINHOMES, VINFAST ──
    CanonicalEntity(
        canonical_id="COMPANY_VIC",
        canonical_name="Tập đoàn Vingroup - Công ty CP",
        entity_type="COMPANY",
        ticker="VIC",
        industry="Đa ngành",
        aliases=("vic", "vingroup", "tập đoàn vingroup")
    ),
    CanonicalEntity(
        canonical_id="SUBSIDIARY_VINFAST",
        canonical_name="Công ty TNHH Sản xuất và Kinh doanh VinFast",
        entity_type="SUBSIDIARY",
        ticker="VFS",
        parent_company_id="COMPANY_VIC",
        parent_ticker="VIC",
        industry="Ô tô điện",
        aliases=("vinfast", "xe điện vinfast", "ô tô vinfast")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_VHM",
        canonical_name="Công ty Cổ phần Vinhomes",
        entity_type="COMPANY",
        ticker="VHM",
        parent_company_id="COMPANY_VIC",
        parent_ticker="VIC",
        industry="Bất động sản",
        aliases=("vhm", "vinhomes", "bất động sản vinhomes")
    ),
    CanonicalEntity(
        canonical_id="PROJECT_VHM_OCEAN_PARK",
        canonical_name="Đại đô thị Vinhomes Ocean Park",
        entity_type="PROJECT",
        ticker=None,
        parent_company_id="COMPANY_VHM",
        parent_ticker="VHM",
        industry="Bất động sản",
        aliases=("vinhomes ocean park", "ocean park", "ocean park 1", "ocean park 2", "ocean park 3")
    ),
    CanonicalEntity(
        canonical_id="PROJECT_VHM_GRAND_PARK",
        canonical_name="Đại đô thị Vinhomes Grand Park",
        entity_type="PROJECT",
        ticker=None,
        parent_company_id="COMPANY_VHM",
        parent_ticker="VHM",
        industry="Bất động sản",
        aliases=("vinhomes grand park", "grand park quận 9", "grand park")
    ),

    # ── 6. HÒA PHÁT (HPG) & DỰ ÁN DUNG QUẤT ──
    CanonicalEntity(
        canonical_id="COMPANY_HPG",
        canonical_name="Công ty Cổ phần Tập đoàn Hòa Phát",
        entity_type="COMPANY",
        ticker="HPG",
        industry="Thép & Vật liệu",
        aliases=("hpg", "hòa phát", "tập đoàn hòa phát", "thép hòa phát", "hoa phat group")
    ),
    CanonicalEntity(
        canonical_id="PROJECT_HPG_DUNG_QUAT",
        canonical_name="Khu liên hợp Gang thép Hòa Phát Dung Quất (1 & 2)",
        entity_type="FACILITY",
        ticker=None,
        parent_company_id="COMPANY_HPG",
        parent_ticker="HPG",
        industry="Sản xuất thép",
        aliases=("dung quất 2", "hòa phát dung quất", "dung quat 2", "lò cao dung quất", "dự án dung quất 2")
    ),

    # ── 7. GEMADEPT (GMD) & CẢNG GEMALINK ──
    CanonicalEntity(
        canonical_id="COMPANY_GMD",
        canonical_name="Công ty Cổ phần Gemadept",
        entity_type="COMPANY",
        ticker="GMD",
        industry="Cảng biển & Logistics",
        aliases=("gmd", "gemadept", "tập đoàn gemadept")
    ),
    CanonicalEntity(
        canonical_id="PROJECT_GEMALINK",
        canonical_name="Cảng nước sâu Gemalink Cái Mép",
        entity_type="PROJECT",
        ticker=None,
        parent_company_id="COMPANY_GMD",
        parent_ticker="GMD",
        industry="Cảng nước sâu",
        aliases=("cảng gemalink", "gemalink", "gemalink port", "cái mép gemalink")
    ),
    CanonicalEntity(
        canonical_id="PROJECT_NAM_DINH_VU",
        canonical_name="Cảng Nam Đình Vũ (Hải Phòng)",
        entity_type="PROJECT",
        ticker=None,
        parent_company_id="COMPANY_GMD",
        parent_ticker="GMD",
        industry="Cảng biển",
        aliases=("cảng nam đình vũ", "nam đình vũ", "nam dinh vu port")
    ),

    # ── 8. DẦU KHÍ & HÓA CHẤT (GAS, PVD, PVS, PLX, DGC, DPM...) ──
    CanonicalEntity(
        canonical_id="COMPANY_GAS",
        canonical_name="Tổng Công ty Khí Việt Nam - CTCP",
        entity_type="COMPANY",
        ticker="GAS",
        industry="Dầu khí",
        aliases=("gas", "pv gas", "khí việt nam", "tổng công ty khí việt nam")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_PVT",
        canonical_name="Tổng Công ty Cổ phần Vận tải Dầu khí",
        entity_type="COMPANY",
        ticker="PVT",
        industry="Vận tải dầu khí",
        aliases=("pvt", "pvtrans", "vận tải dầu khí", "tổng công ty pvtrans")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_BSR",
        canonical_name="Công ty Cổ phần Lọc Hóa dầu Bình Sơn",
        entity_type="COMPANY",
        ticker="BSR",
        industry="Lọc hóa dầu",
        aliases=("bsr", "lọc dầu bình sơn", "lọc hóa dầu bình sơn", "nhà máy lọc dầu dung quất")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_DGC",
        canonical_name="Công ty Cổ phần Tập đoàn Hóa chất Đức Giang",
        entity_type="COMPANY",
        ticker="DGC",
        industry="Hóa chất",
        aliases=("dgc", "hóa chất đức giang", "đức giang", "tập đoàn đức giang", "phốt pho đức giang")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_DPM",
        canonical_name="Tổng Công ty Phân bón và Hóa chất Dầu khí - CTCP",
        entity_type="COMPANY",
        ticker="DPM",
        industry="Phân bón",
        aliases=("dpm", "đạm phú mỹ", "phân bón dầu khí", "phú mỹ")
    ),

    # ── 9. NGÂN HÀNG & CHỨNG KHOÁN (VCB, TCB, MBB, SSI, VND...) ──
    CanonicalEntity(
        canonical_id="COMPANY_VCB",
        canonical_name="Ngân hàng TMCP Ngoại thương Việt Nam",
        entity_type="COMPANY",
        ticker="VCB",
        industry="Ngân hàng",
        aliases=("vcb", "vietcombank", "ngân hàng ngoại thương")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_TCB",
        canonical_name="Ngân hàng TMCP Kỹ thương Việt Nam",
        entity_type="COMPANY",
        ticker="TCB",
        industry="Ngân hàng",
        aliases=("tcb", "techcombank", "ngân hàng kỹ thương")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_MBB",
        canonical_name="Ngân hàng TMCP Quân đội",
        entity_type="COMPANY",
        ticker="MBB",
        industry="Ngân hàng",
        aliases=("mbb", "mbbank", "mb bank", "ngân hàng quân đội")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_SSI",
        canonical_name="Công ty Cổ phần Chứng khoán SSI",
        entity_type="COMPANY",
        ticker="SSI",
        industry="Dịch vụ tài chính",
        aliases=("ssi", "chứng khoán ssi", "ssi securities")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_FTS",
        canonical_name="Công ty Cổ phần Chứng khoán FPT",
        entity_type="COMPANY",
        ticker="FTS",
        industry="Dịch vụ tài chính",
        aliases=("fts", "chứng khoán fpt", "fpt securities", "fpst")
    ),

    # ── 10. NGÂN HÀNG BỔ SUNG (HDB, STB, TPB, VPB, LPB, SHB, EIB, VIB, OCB, MSB, SSB) ──
    CanonicalEntity(
        canonical_id="COMPANY_VPB",
        canonical_name="Ngân hàng TMCP Việt Nam Thịnh Vượng",
        entity_type="COMPANY",
        ticker="VPB",
        industry="Ngân hàng",
        aliases=("vpb", "vpbank", "ngân hàng việt nam thịnh vượng")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_HDB",
        canonical_name="Ngân hàng TMCP Phát triển Thành phố Hồ Chí Minh",
        entity_type="COMPANY",
        ticker="HDB",
        industry="Ngân hàng",
        aliases=("hdb", "hdbank", "ngân hàng phát triển tp hcm")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_STB",
        canonical_name="Ngân hàng TMCP Sài Gòn Thương Tín",
        entity_type="COMPANY",
        ticker="STB",
        industry="Ngân hàng",
        aliases=("stb", "sacombank", "ngân hàng sài gòn thương tín")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_TPB",
        canonical_name="Ngân hàng TMCP Tiên Phong",
        entity_type="COMPANY",
        ticker="TPB",
        industry="Ngân hàng",
        aliases=("tpb", "tpbank", "ngân hàng tiên phong")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_ACB",
        canonical_name="Ngân hàng TMCP Á Châu",
        entity_type="COMPANY",
        ticker="ACB",
        industry="Ngân hàng",
        aliases=("acb", "ngân hàng á châu")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_BID",
        canonical_name="Ngân hàng TMCP Đầu tư và Phát triển Việt Nam",
        entity_type="COMPANY",
        ticker="BID",
        industry="Ngân hàng",
        aliases=("bid", "bidv", "ngân hàng đầu tư và phát triển")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_CTG",
        canonical_name="Ngân hàng TMCP Công Thương Việt Nam",
        entity_type="COMPANY",
        ticker="CTG",
        industry="Ngân hàng",
        aliases=("ctg", "vietinbank", "ngân hàng công thương")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_LPB",
        canonical_name="Ngân hàng TMCP Bưu điện Liên Việt",
        entity_type="COMPANY",
        ticker="LPB",
        industry="Ngân hàng",
        aliases=("lpb", "lienvietpostbank", "ngân hàng bưu điện liên việt")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_SHB",
        canonical_name="Ngân hàng TMCP Sài Gòn - Hà Nội",
        entity_type="COMPANY",
        ticker="SHB",
        industry="Ngân hàng",
        aliases=("shb", "ngân hàng sài gòn hà nội")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_EIB",
        canonical_name="Ngân hàng TMCP Xuất Nhập khẩu Việt Nam",
        entity_type="COMPANY",
        ticker="EIB",
        industry="Ngân hàng",
        aliases=("eib", "eximbank", "ngân hàng xuất nhập khẩu")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_VIB",
        canonical_name="Ngân hàng TMCP Quốc tế Việt Nam",
        entity_type="COMPANY",
        ticker="VIB",
        industry="Ngân hàng",
        aliases=("vib", "ngân hàng quốc tế", "vietnam international bank")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_OCB",
        canonical_name="Ngân hàng TMCP Phương Đông",
        entity_type="COMPANY",
        ticker="OCB",
        industry="Ngân hàng",
        aliases=("ocb", "ngân hàng phương đông")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_MSB",
        canonical_name="Ngân hàng TMCP Hàng Hải Việt Nam",
        entity_type="COMPANY",
        ticker="MSB",
        industry="Ngân hàng",
        aliases=("msb", "maritime bank", "ngân hàng hàng hải")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_SSB",
        canonical_name="Ngân hàng TMCP Đông Nam Á",
        entity_type="COMPANY",
        ticker="SSB",
        industry="Ngân hàng",
        aliases=("ssb", "seabank", "ngân hàng đông nam á")
    ),

    # ── 11. CHỨNG KHOÁN BỔ SUNG (VND, VCI, HCM, MBS, CTS, SHS) ──
    CanonicalEntity(
        canonical_id="COMPANY_VND",
        canonical_name="Công ty Cổ phần Chứng khoán VNDirect",
        entity_type="COMPANY",
        ticker="VND",
        industry="Dịch vụ tài chính",
        aliases=("vnd", "vndirect", "chứng khoán vndirect")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_VCI",
        canonical_name="Công ty Cổ phần Chứng khoán Vietcap",
        entity_type="COMPANY",
        ticker="VCI",
        industry="Dịch vụ tài chính",
        aliases=("vci", "vietcap", "chứng khoán bản việt")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_HCM",
        canonical_name="Công ty Cổ phần Chứng khoán Thành phố Hồ Chí Minh",
        entity_type="COMPANY",
        ticker="HCM",
        industry="Dịch vụ tài chính",
        aliases=("hcm", "hsc", "chứng khoán tp hcm", "chứng khoán hcm")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_MBS",
        canonical_name="Công ty Cổ phần Chứng khoán MB",
        entity_type="COMPANY",
        ticker="MBS",
        industry="Dịch vụ tài chính",
        aliases=("mbs", "chứng khoán mb")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_SHS",
        canonical_name="Công ty Cổ phần Chứng khoán Sài Gòn - Hà Nội",
        entity_type="COMPANY",
        ticker="SHS",
        industry="Dịch vụ tài chính",
        aliases=("shs", "chứng khoán sài gòn hà nội")
    ),

    # ── 12. BẤT ĐỘNG SẢN BỔ SUNG (NVL, NLG, KDH, KBC, BCM, DIG, PDR, DXG, BCG, HDG) ──
    CanonicalEntity(
        canonical_id="COMPANY_NVL",
        canonical_name="Công ty Cổ phần Tập đoàn Đầu tư Địa ốc No Va",
        entity_type="COMPANY",
        ticker="NVL",
        industry="Bất động sản",
        aliases=("nvl", "novaland", "tập đoàn novaland")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_NLG",
        canonical_name="Công ty Cổ phần Đầu tư Nam Long",
        entity_type="COMPANY",
        ticker="NLG",
        industry="Bất động sản",
        aliases=("nlg", "nam long", "đầu tư nam long")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_KDH",
        canonical_name="Công ty Cổ phần Đầu tư và Kinh doanh Nhà Khang Điền",
        entity_type="COMPANY",
        ticker="KDH",
        industry="Bất động sản",
        aliases=("kdh", "khang điền", "nhà khang điền")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_KBC",
        canonical_name="Công ty Cổ phần Phát triển Đô thị Kinh Bắc",
        entity_type="COMPANY",
        ticker="KBC",
        industry="Khu công nghiệp",
        aliases=("kbc", "kinh bắc", "đô thị kinh bắc")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_BCM",
        canonical_name="Tổng Công ty Đầu tư và Phát triển Công nghiệp - CTCP (Becamex IDC)",
        entity_type="COMPANY",
        ticker="BCM",
        industry="Khu công nghiệp",
        aliases=("bcm", "becamex", "becamex idc")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_DIG",
        canonical_name="Công ty Cổ phần Đầu tư Phát triển Xây dựng (DIC Group)",
        entity_type="COMPANY",
        ticker="DIG",
        industry="Bất động sản",
        aliases=("dig", "dic group", "đầu tư phát triển xây dựng")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_PDR",
        canonical_name="Công ty Cổ phần Phát triển Bất động sản Phát Đạt",
        entity_type="COMPANY",
        ticker="PDR",
        industry="Bất động sản",
        aliases=("pdr", "phát đạt", "bất động sản phát đạt")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_DXG",
        canonical_name="Công ty Cổ phần Tập đoàn Đất Xanh",
        entity_type="COMPANY",
        ticker="DXG",
        industry="Bất động sản",
        aliases=("dxg", "đất xanh", "tập đoàn đất xanh")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_HDG",
        canonical_name="Công ty Cổ phần Tập đoàn Hà Đô",
        entity_type="COMPANY",
        ticker="HDG",
        industry="Bất động sản",
        aliases=("hdg", "hà đô", "tập đoàn hà đô")
    ),

    # ── 13. DẦU KHÍ BỔ SUNG (PVD, PVS, PLX, OIL) ──
    CanonicalEntity(
        canonical_id="COMPANY_PVD",
        canonical_name="Tổng Công ty Cổ phần Khoan và Dịch vụ Khoan Dầu khí",
        entity_type="COMPANY",
        ticker="PVD",
        industry="Dầu khí",
        aliases=("pvd", "pv drilling", "khoan dầu khí")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_PVS",
        canonical_name="Tổng Công ty Cổ phần Dịch vụ Kỹ thuật Dầu khí Việt Nam",
        entity_type="COMPANY",
        ticker="PVS",
        industry="Dầu khí",
        aliases=("pvs", "ptsc", "dịch vụ kỹ thuật dầu khí")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_PLX",
        canonical_name="Tập đoàn Xăng dầu Việt Nam (Petrolimex)",
        entity_type="COMPANY",
        ticker="PLX",
        industry="Dầu khí",
        aliases=("plx", "petrolimex", "xăng dầu", "tập đoàn xăng dầu việt nam")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_OIL",
        canonical_name="Tổng Công ty Dầu Việt Nam - CTCP (PVOil)",
        entity_type="COMPANY",
        ticker="OIL",
        industry="Dầu khí",
        aliases=("oil", "pvoil", "dầu việt nam")
    ),

    # ── 14. PHÂN BÓN BỔ SUNG (DCM) ──
    CanonicalEntity(
        canonical_id="COMPANY_DCM",
        canonical_name="Công ty Cổ phần Phân bón Dầu khí Cà Mau",
        entity_type="COMPANY",
        ticker="DCM",
        industry="Phân bón",
        aliases=("dcm", "đạm cà mau", "phân bón cà mau")
    ),

    # ── 15. ĐIỆN & NĂNG LƯỢNG (POW, REE, PC1, NT2) ──
    CanonicalEntity(
        canonical_id="COMPANY_POW",
        canonical_name="Tổng Công ty Điện lực Dầu khí Việt Nam - CTCP",
        entity_type="COMPANY",
        ticker="POW",
        industry="Điện lực",
        aliases=("pow", "pv power", "điện lực dầu khí")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_REE",
        canonical_name="Công ty Cổ phần Cơ Điện Lạnh",
        entity_type="COMPANY",
        ticker="REE",
        industry="Điện lực & Cơ điện",
        aliases=("ree", "cơ điện lạnh")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_PC1",
        canonical_name="Công ty Cổ phần Tập đoàn PC1",
        entity_type="COMPANY",
        ticker="PC1",
        industry="Điện lực & Xây dựng điện",
        aliases=("pc1", "power construction 1", "tập đoàn pc1")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_NT2",
        canonical_name="Công ty Cổ phần Điện lực Dầu khí Nhơn Trạch 2",
        entity_type="COMPANY",
        ticker="NT2",
        industry="Điện lực",
        aliases=("nt2", "nhiệt điện nhơn trạch 2")
    ),

    # ── 16. HÀNG KHÔNG & DU LỊCH (HVN, VJC) ──
    CanonicalEntity(
        canonical_id="COMPANY_HVN",
        canonical_name="Tổng Công ty Hàng không Việt Nam - CTCP",
        entity_type="COMPANY",
        ticker="HVN",
        industry="Hàng không",
        aliases=("hvn", "vietnam airlines", "hàng không việt nam")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_VJC",
        canonical_name="Công ty Cổ phần Hàng không Vietjet",
        entity_type="COMPANY",
        ticker="VJC",
        industry="Hàng không",
        aliases=("vjc", "vietjet", "vietjet air", "hàng không vietjet")
    ),

    # ── 17. BÁN LẺ BỔ SUNG (PNJ, SAB, FRT) ──
    CanonicalEntity(
        canonical_id="COMPANY_PNJ",
        canonical_name="Công ty Cổ phần Vàng bạc Đá quý Phú Nhuận",
        entity_type="COMPANY",
        ticker="PNJ",
        industry="Bán lẻ trang sức",
        aliases=("pnj", "phú nhuận", "vàng bạc đá quý phú nhuận")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_SAB",
        canonical_name="Tổng Công ty Cổ phần Bia - Rượu - Nước giải khát Sài Gòn",
        entity_type="COMPANY",
        ticker="SAB",
        industry="Đồ uống",
        aliases=("sab", "sabeco", "bia sài gòn")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_FRT",
        canonical_name="Công ty Cổ phần Bán lẻ Kỹ thuật số FPT",
        entity_type="COMPANY",
        ticker="FRT",
        industry="Bán lẻ",
        aliases=("frt", "fpt retail", "bán lẻ fpt", "ctcp bán lẻ kỹ thuật số fpt", "fpt digital retail")
    ),

    # ── 18. THÉP BỔ SUNG (HSG, NKG, TLH, POM) ──
    CanonicalEntity(
        canonical_id="COMPANY_HSG",
        canonical_name="Công ty Cổ phần Tập đoàn Hoa Sen",
        entity_type="COMPANY",
        ticker="HSG",
        industry="Thép & Vật liệu",
        aliases=("hsg", "hoa sen", "tập đoàn hoa sen")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_NKG",
        canonical_name="Công ty Cổ phần Thép Nam Kim",
        entity_type="COMPANY",
        ticker="NKG",
        industry="Thép & Vật liệu",
        aliases=("nkg", "nam kim", "thép nam kim")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_TLH",
        canonical_name="Công ty Cổ phần Tập đoàn Thép Tiến Lên",
        entity_type="COMPANY",
        ticker="TLH",
        industry="Thép & Vật liệu",
        aliases=("tlh", "thép tiến lên")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_POM",
        canonical_name="Công ty Cổ phần Thép Pomina",
        entity_type="COMPANY",
        ticker="POM",
        industry="Thép & Vật liệu",
        aliases=("pom", "pomina", "thép pomina")
    ),

    # ── 19. NÔNG NGHIỆP & THỦY SẢN & CAO SU (VHC, HAG, DBC, GVR, PHR) ──
    CanonicalEntity(
        canonical_id="COMPANY_VHC",
        canonical_name="Công ty Cổ phần Vĩnh Hoàn",
        entity_type="COMPANY",
        ticker="VHC",
        industry="Thủy sản",
        aliases=("vhc", "vĩnh hoàn")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_HAG",
        canonical_name="Công ty Cổ phần Hoàng Anh Gia Lai",
        entity_type="COMPANY",
        ticker="HAG",
        industry="Nông nghiệp",
        aliases=("hag", "hagl", "hoàng anh gia lai")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_DBC",
        canonical_name="Công ty Cổ phần Tập đoàn Dabaco Việt Nam",
        entity_type="COMPANY",
        ticker="DBC",
        industry="Nông nghiệp",
        aliases=("dbc", "dabaco", "tập đoàn dabaco")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_GVR",
        canonical_name="Tập đoàn Công nghiệp Cao su Việt Nam - CTCP",
        entity_type="COMPANY",
        ticker="GVR",
        industry="Cao su",
        aliases=("gvr", "cao su việt nam", "tập đoàn cao su việt nam")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_PHR",
        canonical_name="Công ty Cổ phần Cao su Phước Hòa",
        entity_type="COMPANY",
        ticker="PHR",
        industry="Cao su",
        aliases=("phr", "cao su phước hòa")
    ),

    # ── 20. DƯỢC PHẨM (DHG, IMP, TRA) ──
    CanonicalEntity(
        canonical_id="COMPANY_DHG",
        canonical_name="Công ty Cổ phần Dược Hậu Giang",
        entity_type="COMPANY",
        ticker="DHG",
        industry="Dược phẩm",
        aliases=("dhg", "dược hậu giang", "dược phẩm hậu giang")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_IMP",
        canonical_name="Công ty Cổ phần Dược phẩm Imexpharm",
        entity_type="COMPANY",
        ticker="IMP",
        industry="Dược phẩm",
        aliases=("imp", "imexpharm", "dược phẩm imexpharm")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_TRA",
        canonical_name="Công ty Cổ phần Traphaco",
        entity_type="COMPANY",
        ticker="TRA",
        industry="Dược phẩm",
        aliases=("tra", "traphaco", "dược phẩm traphaco")
    ),

    # ── 21. DỆT MAY (TCM, MSH) ──
    CanonicalEntity(
        canonical_id="COMPANY_TCM",
        canonical_name="Công ty Cổ phần Dệt may - Đầu tư - Thương mại Thành Công",
        entity_type="COMPANY",
        ticker="TCM",
        industry="Dệt may",
        aliases=("tcm", "dệt may thành công")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_MSH",
        canonical_name="Công ty Cổ phần May Sông Hồng",
        entity_type="COMPANY",
        ticker="MSH",
        industry="Dệt may",
        aliases=("msh", "may sông hồng")
    ),

    # ── 22. XÂY DỰNG (CTD, HBC, VCG, VGC) ──
    CanonicalEntity(
        canonical_id="COMPANY_CTD",
        canonical_name="Công ty Cổ phần Xây dựng Coteccons",
        entity_type="COMPANY",
        ticker="CTD",
        industry="Xây dựng",
        aliases=("ctd", "coteccons", "xây dựng coteccons")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_HBC",
        canonical_name="Công ty Cổ phần Tập đoàn Xây dựng Hòa Bình",
        entity_type="COMPANY",
        ticker="HBC",
        industry="Xây dựng",
        aliases=("hbc", "hòa bình", "xây dựng hòa bình")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_VCG",
        canonical_name="Tổng Công ty Cổ phần Xuất nhập khẩu và Xây dựng Việt Nam (Vinaconex)",
        entity_type="COMPANY",
        ticker="VCG",
        industry="Xây dựng",
        aliases=("vcg", "vinaconex", "tổng công ty vinaconex")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_VGC",
        canonical_name="Tổng Công ty Viglacera - CTCP",
        entity_type="COMPANY",
        ticker="VGC",
        industry="Vật liệu xây dựng & KCN",
        aliases=("vgc", "viglacera", "tổng công ty viglacera")
    ),

    # ── 23. BẢO HIỂM (BVH, PVI) ──
    CanonicalEntity(
        canonical_id="COMPANY_BVH",
        canonical_name="Tập đoàn Bảo Việt",
        entity_type="COMPANY",
        ticker="BVH",
        industry="Bảo hiểm",
        aliases=("bvh", "bảo việt", "tập đoàn bảo việt")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_PVI",
        canonical_name="Công ty Cổ phần Bảo hiểm PVI",
        entity_type="COMPANY",
        ticker="PVI",
        industry="Bảo hiểm",
        aliases=("pvi", "bảo hiểm pvi")
    ),

    # ── 24. VINCOM RETAIL & VEAM ──
    CanonicalEntity(
        canonical_id="COMPANY_VRE",
        canonical_name="Công ty Cổ phần Vincom Retail",
        entity_type="COMPANY",
        ticker="VRE",
        industry="Bán lẻ BĐS",
        aliases=("vre", "vincom retail", "trung tâm thương mại vincom")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_VEA",
        canonical_name="Tổng Công ty Máy Động lực và Máy Nông nghiệp Việt Nam (VEAM)",
        entity_type="COMPANY",
        ticker="VEA",
        industry="Cơ khí & Ô tô",
        aliases=("vea", "veam", "máy động lực và máy nông nghiệp")
    ),

    # ── 25. KHU CÔNG NGHIỆP BỔ SUNG (SIP, SZC, LHG) ──
    CanonicalEntity(
        canonical_id="COMPANY_SIP",
        canonical_name="Công ty TNHH Một Thành viên KCN Sài Gòn",
        entity_type="COMPANY",
        ticker="SIP",
        industry="Khu công nghiệp",
        aliases=("sip", "khu công nghiệp sài gòn")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_SZC",
        canonical_name="Công ty Cổ phần Sonadezi Châu Đức",
        entity_type="COMPANY",
        ticker="SZC",
        industry="Khu công nghiệp",
        aliases=("szc", "sonadezi châu đức")
    ),

    # ── 26. THỰC PHẨM & ĐỒ UỐNG BỔ SUNG (VNM, BHN, KDC, QNS, SBT) ──
    CanonicalEntity(
        canonical_id="COMPANY_VNM",
        canonical_name="Công ty Cổ phần Sữa Việt Nam",
        entity_type="COMPANY",
        ticker="VNM",
        industry="Sữa & Thực phẩm",
        aliases=("vnm", "vinamilk", "sữa việt nam")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_BHN",
        canonical_name="Tổng Công ty Cổ phần Bia - Rượu - Nước giải khát Hà Nội",
        entity_type="COMPANY",
        ticker="BHN",
        industry="Đồ uống",
        aliases=("bhn", "habeco", "bia hà nội")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_KDC",
        canonical_name="Công ty Cổ phần Tập đoàn KIDO",
        entity_type="COMPANY",
        ticker="KDC",
        industry="Thực phẩm",
        aliases=("kdc", "kido", "tập đoàn kido")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_QNS",
        canonical_name="Công ty Cổ phần Đường Quảng Ngãi",
        entity_type="COMPANY",
        ticker="QNS",
        industry="Thực phẩm & Đường",
        aliases=("qns", "đường quảng ngãi", "sữa đậu nành việt nam")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_SBT",
        canonical_name="Công ty Cổ phần Thành Thành Công - Biên Hòa",
        entity_type="COMPANY",
        ticker="SBT",
        industry="Đường & Mía đường",
        aliases=("sbt", "thành thành công biên hòa", "mía đường ttc")
    ),

    # ── 27. CẢNG BIỂN & LOGISTICS BỔ SUNG (PHP, VTP, SCS) ──
    CanonicalEntity(
        canonical_id="COMPANY_PHP",
        canonical_name="Công ty Cổ phần Cảng Hải Phòng",
        entity_type="COMPANY",
        ticker="PHP",
        industry="Cảng biển",
        aliases=("php", "cảng hải phòng")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_VTP",
        canonical_name="Công ty Cổ phần Bưu chính Viettel",
        entity_type="COMPANY",
        ticker="VTP",
        industry="Logistics",
        aliases=("vtp", "viettel post", "bưu chính viettel")
    ),
    CanonicalEntity(
        canonical_id="COMPANY_SCS",
        canonical_name="Công ty Cổ phần Dịch vụ Hàng hóa Sài Gòn",
        entity_type="COMPANY",
        ticker="SCS",
        industry="Logistics hàng không",
        aliases=("scs", "dịch vụ hàng hóa sài gòn")
    ),

    # ── 28. CMC, FOX, FPT TELECOM ──
    CanonicalEntity(
        canonical_id="COMPANY_CMG",
        canonical_name="Công ty Cổ phần Tập đoàn Công nghệ CMC",
        entity_type="COMPANY",
        ticker="CMG",
        industry="Công nghệ thông tin",
        aliases=("cmg", "cmc", "tập đoàn cmc")
    ),
)



class CanonicalEntityRegistry:
    """Registry quản lý danh bạ thực thể có khả năng mở rộng (Extensible Single Source of Truth)."""

    def __init__(self, entities: tuple[CanonicalEntity, ...] | None = None) -> None:
        self._entities: dict[str, CanonicalEntity] = {}              # canonical_id -> CanonicalEntity
        self._ticker_map: dict[str, CanonicalEntity] = {}            # TICKER -> CanonicalEntity
        self._alias_index: list[tuple[str, str, CanonicalEntity]] = []  # [(alias_norm, alias_no_accent, entity)]
        
        # Nạp thực thể ban đầu
        initial = entities if entities is not None else CORE_CANONICAL_ENTITIES
        for e in initial:
            self.register(e)

    def register(self, entity: CanonicalEntity) -> None:
        """Đăng ký mới hoặc cập nhật một thực thể vào Registry."""
        self._entities[entity.canonical_id] = entity
        if entity.ticker:
            self._ticker_map[entity.ticker.upper()] = entity

        # Đăng ký aliases
        all_aliases = set(entity.aliases)
        if entity.ticker:
            all_aliases.add(entity.ticker.lower())
        all_aliases.add(entity.canonical_name.lower())

        for a in all_aliases:
            norm = normalize_text_for_matching(a)
            no_accent = strip_vietnamese_diacritics(norm)
            if norm and len(norm) >= 2:
                self._alias_index.append((norm, no_accent, entity))

        # Sắp xếp index theo độ dài alias giảm dần (Longest Match First)
        self._alias_index.sort(key=lambda item: len(item[0]), reverse=True)

    def register_from_db_stocks(self, stocks: list[dict[str, Any]]) -> int:
        """Nạp động toàn bộ cổ phiếu từ database `stocks` table."""
        count = 0
        for s in stocks:
            symbol = str(s.get("symbol", "")).upper().strip()
            name = str(s.get("name", "")).strip()
            industry = s.get("industry")
            exchange = s.get("exchange", "HOSE")
            if not symbol or not name:
                continue
            
            c_entity = CanonicalEntity(
                canonical_id=f"COMPANY_{symbol}",
                canonical_name=name,
                entity_type="COMPANY",
                ticker=symbol,
                industry=industry,
                exchange=exchange,
                aliases=(symbol.lower(), name.lower())
            )
            self.register(c_entity)
            count += 1
        return count

    def resolve(self, query_text: str, min_confidence: float = 0.70) -> EntityResolutionResult | None:
        """Phân giải thực thể theo Quy trình Chặt chẽ (Hierarchical Resolution Pipeline).
        
        Thứ tự ưu tiên:
        1. Exact Ticker Match (Khi câu hỏi chỉ là 3 ký tự in hoa)
        2. Exact Alias Match (Toàn bộ câu hỏi trùng khớp 100% với một alias)
        3. Longest Token-Boundary Substring Match (Ưu tiên alias cụ thể, dài nhất trước)
        4. Standalone Ticker In-Query Match (Nếu không có alias nào dài hơn khớp)
        5. Strict Fuzzy Match (Độ tương đồng >= 0.88 với query ngắn)
        """
        if not query_text:
            return None

        raw_text = query_text.strip()
        norm_query = normalize_text_for_matching(raw_text)
        no_accent_query = strip_vietnamese_diacritics(norm_query)

        # ── 1. EXACT STANDALONE TICKER MATCH (VD: query = "HPG", "FRT") ──
        if len(raw_text) == 3 and raw_text.isupper() and raw_text in self._ticker_map:
            e = self._ticker_map[raw_text]
            prim_ticker = e.parent_ticker if e.entity_type in ("BRAND", "SUBSIDIARY", "PROJECT", "FACILITY") else (e.ticker or e.parent_ticker)
            return EntityResolutionResult(
                canonical_id=e.canonical_id,
                canonical_name=e.canonical_name,
                entity_type=e.entity_type,
                ticker=e.ticker,
                parent_ticker=e.parent_ticker,
                primary_ticker=prim_ticker,
                match_type="EXACT_TICKER",
                confidence=1.00,
                matched_alias=raw_text,
                original_query=raw_text
            )

        # ── 2. EXACT FULL ALIAS MATCH ──
        for alias_norm, alias_no_accent, entity in self._alias_index:
            if norm_query == alias_norm or no_accent_query == alias_no_accent:
                prim_ticker = entity.parent_ticker if entity.entity_type in ("BRAND", "SUBSIDIARY", "PROJECT", "FACILITY") and entity.parent_ticker else (entity.ticker or entity.parent_ticker)
                return EntityResolutionResult(
                    canonical_id=entity.canonical_id,
                    canonical_name=entity.canonical_name,
                    entity_type=entity.entity_type,
                    ticker=entity.ticker,
                    parent_ticker=entity.parent_ticker,
                    primary_ticker=prim_ticker,
                    match_type="EXACT_ALIAS",
                    confidence=0.98,
                    matched_alias=alias_norm,
                    original_query=raw_text
                )

        # ── 3. LONGEST TOKEN-BOUNDARY SUBSTRING MATCH (Ưu tiên alias cụ thể, dài nhất) ──
        # Index đã được sort sẵn theo len(alias_norm) giảm dần
        for alias_norm, alias_no_accent, entity in self._alias_index:
            if len(alias_norm) < 3:
                continue
            pattern = rf"(?:\b|^){re.escape(alias_norm)}(?:\b|$)"
            pattern_no_accent = rf"(?:\b|^){re.escape(alias_no_accent)}(?:\b|$)"
            
            if re.search(pattern, norm_query) or re.search(pattern_no_accent, no_accent_query):
                len_ratio = min(1.0, len(alias_norm) / max(1, len(norm_query)))
                confidence = max(0.80, min(0.95, 0.75 + len_ratio * 0.20))
                prim_ticker = entity.parent_ticker if entity.entity_type in ("BRAND", "SUBSIDIARY", "PROJECT", "FACILITY") and entity.parent_ticker else (entity.ticker or entity.parent_ticker)
                
                return EntityResolutionResult(
                    canonical_id=entity.canonical_id,
                    canonical_name=entity.canonical_name,
                    entity_type=entity.entity_type,
                    ticker=entity.ticker,
                    parent_ticker=entity.parent_ticker,
                    primary_ticker=prim_ticker,
                    match_type="LONGEST_TOKEN_MATCH",
                    confidence=confidence,
                    matched_alias=alias_norm,
                    original_query=raw_text
                )

        # ── 4. STANDALONE TICKER IN-QUERY MATCH (Nếu câu chứa mã ticker nhưng không khớp alias dài hơn) ──
        ticker_match = re.search(r"\b([A-Z]{3})\b", raw_text)
        if ticker_match:
            cand = ticker_match.group(1).upper()
            if cand in self._ticker_map:
                e = self._ticker_map[cand]
                prim_ticker = e.parent_ticker if e.entity_type in ("BRAND", "SUBSIDIARY", "PROJECT", "FACILITY") and e.parent_ticker else (e.ticker or e.parent_ticker)
                return EntityResolutionResult(
                    canonical_id=e.canonical_id,
                    canonical_name=e.canonical_name,
                    entity_type=e.entity_type,
                    ticker=e.ticker,
                    parent_ticker=e.parent_ticker,
                    primary_ticker=prim_ticker,
                    match_type="EXACT_TICKER",
                    confidence=0.90,
                    matched_alias=cand,
                    original_query=raw_text
                )

        # ── 5. STRICT FUZZY MATCH (Chỉ áp dụng với query ngắn < 35 ký tự) ──
        if len(norm_query) <= 35:
            best_match: tuple[float, CanonicalEntity, str] | None = None
            for alias_norm, alias_no_accent, entity in self._alias_index:
                if len(alias_norm) < 4:
                    continue
                ratio = max(
                    SequenceMatcher(None, norm_query, alias_norm).ratio(),
                    SequenceMatcher(None, no_accent_query, alias_no_accent).ratio()
                )
                if ratio >= 0.88:
                    if best_match is None or ratio > best_match[0]:
                        best_match = (ratio, entity, alias_norm)

            if best_match and best_match[0] >= min_confidence:
                ratio, entity, matched_alias = best_match
                prim_ticker = entity.parent_ticker if entity.entity_type in ("BRAND", "SUBSIDIARY", "PROJECT", "FACILITY") and entity.parent_ticker else (entity.ticker or entity.parent_ticker)
                return EntityResolutionResult(
                    canonical_id=entity.canonical_id,
                    canonical_name=entity.canonical_name,
                    entity_type=entity.entity_type,
                    ticker=entity.ticker,
                    parent_ticker=entity.parent_ticker,
                    primary_ticker=prim_ticker,
                    match_type="FUZZY_MATCH",
                    confidence=round(ratio * 0.90, 2),
                    matched_alias=matched_alias,
                    original_query=raw_text
                )

        return None


# Khởi tạo singleton Registry
universe_registry = CanonicalEntityRegistry()
