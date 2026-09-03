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
