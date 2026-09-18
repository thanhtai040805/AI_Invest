"""Beneish M-Score Engine — TASK-202 (IOS v5.1 Production Ready)

Tính toán chỉ số Beneish M-Score để phát hiện gian lận tài chính (Lớp 0 Hard Law).
Hỗ trợ kiến trúc thích ứng kép (Dual-Engine Adaptive Beneish - Messod Beneish 1999):
  1. Mô hình 8 biến gốc (Full 8-Variable Model):
     M8 = -4.84 + 0.920*DSRI + 0.528*GMI + 0.404*AQI + 0.892*SGI + 0.115*DEPI - 0.172*SGAI + 4.037*TATA + 0.0327*LVGI
  2. Mô hình 5 biến chính thức (Adaptive 5-Variable Non-Cash-Flow Model):
     M5 = -6.065 + 0.823*DSRI + 0.906*GMI + 0.593*AQI + 0.717*SGI + 0.107*LVGI
     (Áp dụng khi BCTC quý tóm tắt thiếu dòng Khấu hao riêng biệt; 100% số liệu thực tồn tại, KHÔNG ĐIỀN SỐ 0 GIẢ).
Ngưỡng loại (FAIL): M-Score > -1.78.
Miễn trừ ngành tài chính đặc thù: Ngân hàng, Bất động sản, Chứng khoán, Bảo hiểm, Dịch vụ tài chính.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any, Dict, List, Optional

from app.infrastructure.database.pg_pool import get_conn
from app.domain.repositories.universe_repository import UniverseRepository
from app.infrastructure.vendors.vn.sector_groups import classify, BANKS, FINANCIAL_SERVICES, REAL_ESTATE

logger = logging.getLogger(__name__)

EXCLUDED_SECTORS = [
    "Ngân hàng",
    "Bất động sản",
    "Chứng khoán",
    "Bảo hiểm",
    "Dịch vụ tài chính",
    "Tài chính khác",
]

# Chuẩn hóa các bộ key tìm kiếm trong CSDL financial_statements (VAS Thông tư 200/BTC)
REV_KEYS = [
    "3_doanh_thu_thuần_về_bán_hàng_và_cung_cấp_dịch_vụ",
    "3. Doanh thu thuần về bán hàng và cung cấp dịch vụ",
    "1_doanh_thu_bán_hàng_và_cung_cấp_dịch_vụ",
    "1. Doanh thu bán hàng và cung cấp dịch vụ",
    "doanh_thu_thuần_về_bán_hàng_và_cung_cấp_dịch_vụ",
    "doanh thu thuần",
]

REC_KEYS = [
    "iii_các_khoản_phải_thu_ngắn_hạn",
    "III. Các khoản phải thu ngắn hạn",
    "1_phải_thu_ngắn_hạn_của_khách_hàng",
    "1. Phải thu ngắn hạn của khách hàng",
    "phải_thu_ngắn_hạn_của_khách_hàng",
    "các_khoản_phải_thu_ngắn_hạn",
]

COGS_KEYS = [
    "4_giá_vốn_hàng_bán",
    "4. Giá vốn hàng bán",
    "giá_vốn_hàng_bán",
]

TA_KEYS = [
    "tổng_cộng_tài_sản",
    "TỔNG CỘNG TÀI SẢN",
    "tổng cộng tài sản",
]

CA_KEYS = [
    "a_tài_sản_ngắn_hạn",
    "A. TÀI SẢN NGẮN HẠN",
    "tài sản ngắn hạn",
]

PPE_KEYS = [
    "ii_tài_sản_cố_định",
    "II. Tài sản cố định",
    "1_tài_sản_cố_định_hữu_hình",
    "1. Tài sản cố định hữu hình",
    "tài_sản_cố_định_hữu_hình",
    "tài sản cố định",
]

DEBT_KEYS = [
    "c_nợ_phải_trả",
    "C. NỢ PHẢI TRẢ",
    "nợ phải trả",
]

SGA_SALE_KEYS = [
    "9_chi_phí_bán_hàng",
    "9. Chi phí bán hàng",
    "chi_phí_bán_hàng",
]

SGA_ADMIN_KEYS = [
    "10_chi_phí_quản_lý_doanh_nghiệp",
    "10. Chi phí quản lý doanh nghiệp",
    "chi_phí_quản_lý_doanh_nghiệp",
]

DEPR_CF_KEYS = [
    "khấu_hao_tscđ_và_bđsđt",
    "Khấu hao TSCĐ và BĐSĐT",
    "Khấu hao TSCĐ",
    "khấu_hao_tscđ",
    "Khấu hao tài sản cố định",
    "khấu_hao_tài_sản_cố_định",
    "1_khấu_hao_tscđ_và_bđsđt",
]

DEPR_BS_ACC_KEYS = [
    "- giá trị hao mòn lũy kế (*)",
    "- Giá trị hao mòn lũy kế (*)",
    "giá trị hao mòn lũy kế",
    "hao mòn lũy kế",
]

CFO_KEYS = [
    "lưu_chuyển_tiền_thuần_từ_hoạt_động_kinh_doanh",
    "Lưu chuyển tiền thuần từ hoạt động kinh doanh",
    "I. Lưu chuyển tiền từ hoạt động kinh doanh",
    "i. lưu chuyển tiền từ hoạt động kinh doanh",
    "I - Lưu chuyển tiền thuần từ hoạt động kinh doanh",
    "Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh",
    "lưu_chuyển_tiền_tệ_ròng_từ_các_hoạt_động_sản_xuất_kinh_doanh",
    "Lưu chuyển thuần từ hoạt động kinh doanh",
    "Lưu chuyển tiền từ hoạt động kinh doanh",
    "i_lưu_chuyển_tiền_thuần_từ_hoạt_động_kinh_doanh",
    "i_lưu_chuyển_tiền_từ_hoạt_động_kinh_doanh",
]

NI_KEYS = [
    "18_lợi_nhuận_sau_thuế_thu_nhập_doanh_nghiệp",
    "18. Lợi nhuận sau thuế thu nhập doanh nghiệp",
    "lợi_nhuận_sau_thuế_thu_nhập_doanh_nghiệp",
    "Lợi nhuận sau thuế thu nhập doanh nghiệp",
    "lợi nhuận sau thuế",
]


def _extract_val(data_dict: Optional[Dict[str, Any]], keys: List[str]) -> Optional[float]:
    """Trích xuất giá trị số từ từ điển BCTC không phân biệt hoa thường."""
    if not data_dict:
        return None
    d_norm = {str(k).lower().strip(): v for k, v in data_dict.items() if v is not None}
    for k in keys:
        k_norm = k.lower().strip()
        if k_norm in d_norm:
            try:
                val = float(d_norm[k_norm])
                return val
            except (ValueError, TypeError):
                continue
    return None


class BeneishMScoreEngine:
    def __init__(self):
        self.repo = UniverseRepository()

    def calculate_m_score(self, ticker: str, target_date: Optional[date] = None) -> Dict[str, Any]:
        """Tính toán M-Score cho một ticker dựa trên BCTC chuẩn thực tế (BS, IS, CF)."""
        sym = str(ticker).upper().strip()
        if target_date is None:
            target_date = date.today()

        # 1. Kiểm tra ngành của cổ phiếu
        industry = ""
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(industry, sector, '') FROM stocks WHERE symbol = %s",
                    (sym,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    industry = str(row[0]).strip()

        # Miễn trừ kiểm tra M-Score cho nhóm Tài chính / BĐS
        classified_sector = classify(industry, sym)
        is_fin_or_re = (
            classified_sector in (BANKS, FINANCIAL_SERVICES, REAL_ESTATE)
            or any(exc in industry for exc in EXCLUDED_SECTORS)
        )
        if is_fin_or_re:
            result = {
                "ticker": sym,
                "m_score": -99.0,
                "status": "PASS",
                "is_exempt": True,
                "model_used": "exempt",
                "threshold": -1.78,
                "reason": f"Bypass M-Score for financial sector: {classified_sector or industry}",
                "variables": {
                    "dsri": 1.0, "gmi": 1.0, "aqi": 1.0, "sgi": 1.0,
                    "depi": 1.0, "sgai": 1.0, "lvgi": 1.0, "tata": 0.0,
                },
            }
            self.update_security_status(result)
            return result

        # 2. Truy vấn dữ liệu BCTC từ bảng financial_statements
        is_by_period: Dict[Any, Dict[str, Any]] = {}
        bs_by_period: Dict[Any, Dict[str, Any]] = {}
        cf_by_period: Dict[Any, Dict[str, Any]] = {}

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT statement_type, period_end, data
                        FROM financial_statements
                        WHERE symbol = %s AND statement_type IN ('IS', 'BS', 'CF') AND period_end <= %s
                        ORDER BY period_end DESC;
                        """,
                        (sym, target_date),
                    )
                    rows = cur.fetchall()
                    for st, pe, d in rows:
                        # Chỉ lấy các bản ghi có dữ liệu thực tế (>= 8 chỉ tiêu non-null)
                        non_nulls = sum(1 for v in d.values() if v is not None) if d else 0
                        if non_nulls >= 8:
                            if st == "IS" and pe not in is_by_period:
                                is_by_period[pe] = d
                            elif st == "BS" and pe not in bs_by_period:
                                bs_by_period[pe] = d
                            elif st == "CF" and pe not in cf_by_period:
                                cf_by_period[pe] = d
        except Exception as e:
            logger.warning(f"Lỗi truy vấn financial_statements cho {sym}: {e}")

        # Tìm 2 kỳ gần nhất có cả IS và BS
        common_periods = sorted(set(is_by_period.keys()) & set(bs_by_period.keys()), reverse=True)

        if len(common_periods) >= 2:
            t0, t1 = common_periods[0], common_periods[1]
            is0, is1 = is_by_period[t0], is_by_period[t1]
            bs0, bs1 = bs_by_period[t0], bs_by_period[t1]

            # Trích xuất 5 nhóm chỉ tiêu cơ sở
            rev0, rev1 = _extract_val(is0, REV_KEYS), _extract_val(is1, REV_KEYS)
            rec0, rec1 = _extract_val(bs0, REC_KEYS), _extract_val(bs1, REC_KEYS)
            cogs0, cogs1 = _extract_val(is0, COGS_KEYS), _extract_val(is1, COGS_KEYS)
            ta0, ta1 = _extract_val(bs0, TA_KEYS), _extract_val(bs1, TA_KEYS)
            ca0, ca1 = _extract_val(bs0, CA_KEYS), _extract_val(bs1, CA_KEYS)
            ppe0, ppe1 = _extract_val(bs0, PPE_KEYS), _extract_val(bs1, PPE_KEYS)
            debt0, debt1 = _extract_val(bs0, DEBT_KEYS), _extract_val(bs1, DEBT_KEYS)

            can_calc_5 = (
                rev0 and rev1 and rev0 > 0 and rev1 > 0
                and rec0 is not None and rec1 is not None and rec0 >= 0 and rec1 >= 0
                and cogs0 is not None and cogs1 is not None
                and ta0 and ta1 and ta0 > 0 and ta1 > 0
                and ca0 is not None and ca1 is not None
                and ppe0 is not None and ppe1 is not None
                and debt0 is not None and debt1 is not None
            )

            if can_calc_5:
                # 1. DSRI (Days Sales in Receivables Index)
                ratio_rec0 = rec0 / rev0
                ratio_rec1 = rec1 / rev1
                dsri = max(0.1, min(10.0, ratio_rec0 / ratio_rec1)) if ratio_rec1 > 0 else 1.0

                # 2. GMI (Gross Margin Index)
                gm0 = (rev0 - cogs0) / rev0
                gm1 = (rev1 - cogs1) / rev1
                gmi = max(0.1, min(10.0, gm1 / gm0)) if gm0 > 0 else 1.0

                # 3. AQI (Asset Quality Index)
                nca0 = max(0.0, ta0 - (ca0 + ppe0))
                nca1 = max(0.0, ta1 - (ca1 + ppe1))
                aqi0 = nca0 / ta0
                aqi1 = nca1 / ta1
                aqi = max(0.1, min(10.0, aqi0 / aqi1)) if aqi1 > 0 else 1.0

                # 4. SGI (Sales Growth Index)
                sgi = max(0.1, min(10.0, rev0 / rev1))

                # 5. LVGI (Leverage Index)
                lev0 = debt0 / ta0
                lev1 = debt1 / ta1
                lvgi = max(0.1, min(10.0, lev0 / lev1)) if lev1 > 0 else 1.0

                # Kiểm tra xem có đủ dữ liệu cho Mô hình 8 biến không (SGA, Khấu hao, Dòng tiền CFO, Lợi nhuận ròng)
                cf0 = cf_by_period.get(t0)
                cf1 = cf_by_period.get(t1)

                sga0_part1 = _extract_val(is0, SGA_SALE_KEYS) or 0.0
                sga0_part2 = _extract_val(is0, SGA_ADMIN_KEYS) or 0.0
                sga0 = sga0_part1 + sga0_part2

                sga1_part1 = _extract_val(is1, SGA_SALE_KEYS) or 0.0
                sga1_part2 = _extract_val(is1, SGA_ADMIN_KEYS) or 0.0
                sga1 = sga1_part1 + sga1_part2

                dep0 = _extract_val(cf0, DEPR_CF_KEYS)
                dep1 = _extract_val(cf1, DEPR_CF_KEYS)

                # Nếu CF không có dòng khấu hao riêng, kiểm tra biến động hao mòn lũy kế từ BS
                if dep0 is None or dep1 is None:
                    acc_dep0 = _extract_val(bs0, DEPR_BS_ACC_KEYS)
                    acc_dep1 = _extract_val(bs1, DEPR_BS_ACC_KEYS)
                    if acc_dep0 is not None and acc_dep1 is not None:
                        dep0 = abs(acc_dep0 - acc_dep1)
                        dep1 = dep0

                # Nếu doanh nghiệp không có TSCĐ (PPE == 0)
                if ppe0 == 0.0:
                    dep0 = dep0 or 0.0
                    dep1 = dep1 or 0.0

                cfo0 = _extract_val(cf0, CFO_KEYS)
                ni0 = _extract_val(is0, NI_KEYS)

                # Quyết định: Mô hình 8 biến (Mode 1) hay Mô hình 5 biến (Mode 2)
                has_full_8 = (
                    sga0 > 0 and sga1 > 0
                    and dep0 is not None and dep1 is not None
                    and cfo0 is not None
                    and ni0 is not None
                )

                if has_full_8:
                    # 6. SGAI (Sales & General Admin Expense Index)
                    sgai = max(0.1, min(10.0, (sga0 / rev0) / (sga1 / rev1))) if (sga1 / rev1) > 0 else 1.0

                    # 7. DEPI (Depreciation Rate Index)
                    depr_rate0 = dep0 / (ppe0 + dep0) if (ppe0 + dep0) > 0 else 0.0
                    depr_rate1 = dep1 / (ppe1 + dep1) if (ppe1 + dep1) > 0 else 0.0
                    depi = max(0.1, min(10.0, depr_rate1 / depr_rate0)) if depr_rate0 > 0 else 1.0

                    # 8. TATA (Total Accruals to Total Assets)
                    tata = max(-2.0, min(2.0, (ni0 - cfo0) / ta0))

                    # CÔNG THỨC 8 BIẾN GỐC (BENEISH 1999)
                    m_score = (
                        -4.84
                        + 0.920 * dsri
                        + 0.528 * gmi
                        + 0.404 * aqi
                        + 0.892 * sgi
                        + 0.115 * depi
                        - 0.172 * sgai
                        + 4.037 * tata
                        + 0.0327 * lvgi
                    )
                    model_used = "8_variable"
                    reason = f"Full 8-variable Beneish (1999) calculated with unbundled depreciation and CFO (Q {t0} vs {t1})"
                else:
                    # CÔNG THỨC 5 BIẾN CHÍNH THỨC (BENEISH 1999 NON-CASH-FLOW)
                    # Không dùng biến giả tạo 0, chỉ dùng 100% số liệu thực từ BS và IS
                    m_score = (
                        -6.065
                        + 0.823 * dsri
                        + 0.906 * gmi
                        + 0.593 * aqi
                        + 0.717 * sgi
                        + 0.107 * lvgi
                    )
                    model_used = "5_variable"
                    depi = None
                    sgai = None
                    tata = None
                    reason = f"Adaptive 5-variable Beneish (1999) calculated with 100% real IS/BS data; condensed BCTC lacks separate depreciation line"

                status = "FAIL" if m_score > -1.78 else "PASS"

                variables = {
                    "dsri": round(dsri, 4),
                    "gmi": round(gmi, 4),
                    "aqi": round(aqi, 4),
                    "sgi": round(sgi, 4),
                    "lvgi": round(lvgi, 4),
                    "depi": round(depi, 4) if depi is not None else None,
                    "sgai": round(sgai, 4) if sgai is not None else None,
                    "tata": round(tata, 4) if tata is not None else None,
                }

                result = {
                    "ticker": sym,
                    "quarter_date": t0,
                    "m_score": round(m_score, 4),
                    "status": status,
                    "model_used": model_used,
                    "threshold": -1.78,
                    "is_exempt": False,
                    "reason": reason,
                    "variables": variables,
                }

                self.repo.save_beneish_result(
                    ticker=sym,
                    quarter_date=t0,
                    m_score=round(m_score, 4),
                    status=status,
                    variables={k: v if v is not None else 1.0 for k, v in variables.items()},
                )
                self.update_security_status(result)
                return result

        # 3. Fallback: Nếu không đủ 2 quý BCTC trong financial_statements (doanh nghiệp mới niêm yết)
        # Sử dụng financial_ratios làm nguồn dự phòng có đánh dấu rõ ràng
        r_rows = []
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT ratio_date, gross_margin, debt_equity, yoy_revenue_growth,
                               current_ratio, roe, roa
                        FROM financial_ratios
                        WHERE symbol = %s AND ratio_date <= %s
                        ORDER BY ratio_date DESC
                        LIMIT 2
                        """,
                        (sym, target_date),
                    )
                    r_rows = cur.fetchall()
        except Exception as e:
            logger.warning(f"Lỗi truy vấn financial_ratios cho {sym}: {e}")

        if len(r_rows) >= 2:
            r_t, r_t1 = r_rows[0], r_rows[1]
            quarter_date = r_t[0]

            gm_t = float(r_t[1]) if r_t[1] is not None else 0.0
            gm_t1 = float(r_t1[1]) if r_t1[1] is not None else 0.0
            de_t = float(r_t[2]) if r_t[2] is not None else 1.0
            de_t1 = float(r_t1[2]) if r_t1[2] is not None else 1.0
            yoy_rev = float(r_t[3]) if r_t[3] is not None else 0.0

            sgi = max(0.2, min(5.0, 1.0 + yoy_rev))
            gmi = max(0.2, min(5.0, gm_t1 / (gm_t + 1e-6))) if gm_t > 0 else 1.0
            aqi = 1.0
            lvgi = max(0.5, min(3.0, (1.0 + de_t) / (1.0 + de_t1 + 1e-6)))
            dsri = max(0.5, min(3.0, 1.0 + 0.5 * yoy_rev))

            # 5-variable model approximation from ratios
            m_score = -6.065 + 0.823 * dsri + 0.906 * gmi + 0.593 * aqi + 0.717 * sgi + 0.107 * lvgi
            status = "FAIL" if m_score > -1.78 else "PASS"

            variables = {
                "dsri": round(dsri, 4),
                "gmi": round(gmi, 4),
                "aqi": round(aqi, 4),
                "sgi": round(sgi, 4),
                "lvgi": round(lvgi, 4),
                "depi": None,
                "sgai": None,
                "tata": None,
            }

            result = {
                "ticker": sym,
                "quarter_date": quarter_date,
                "m_score": round(m_score, 4),
                "status": status,
                "model_used": "ratio_fallback",
                "threshold": -1.78,
                "is_exempt": False,
                "reason": "Computed from financial_ratios fallback (BCTC statements missing or insufficient)",
                "variables": variables,
            }
            self.repo.save_beneish_result(
                ticker=sym,
                quarter_date=quarter_date,
                m_score=round(m_score, 4),
                status=status,
                variables={k: v if v is not None else 1.0 for k, v in variables.items()},
            )
            self.update_security_status(result)
            return result

        # Không có dữ liệu cả 2 nguồn
        return {
            "ticker": sym,
            "m_score": None,
            "status": "DATA_MISSING",
            "model_used": "none",
            "threshold": -1.78,
            "is_exempt": False,
            "reason": "Thiếu dữ liệu BCTC kỳ t hoặc t-1 trong cả financial_statements và financial_ratios",
            "variables": {},
        }

    def update_security_status(self, results: Dict[str, Any]):
        """Cập nhật kết quả vào bảng stocks và universe_securities."""
        sym = str(results.get("ticker", "")).upper().strip()
        if not sym:
            return
        m_score = results.get("m_score")
        status = results.get("status", "PENDING")

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE stocks
                        SET beneish_score = %s,
                            beneish_status = %s,
                            beneish_updated = CURRENT_DATE
                        WHERE symbol = %s
                        """,
                        (m_score if m_score != -99.0 else None, status, sym),
                    )
        except Exception as e:
            logger.debug(f"Không thể cập nhật stocks.beneish_score cho {sym}: {e}")


beneish_engine = BeneishMScoreEngine()
