"""V3 Epistemic Knowledge Foundation & Value-Gradient Propagation Engine (`epistemic_foundation.py`).

Pure deterministic Python module (`0` LLM calls, `0` ORM/FastAPI dependencies) that upgrades SAG from
a Homophilic Echo Graph (`A ≈ B` surface similarity) into a **Heterophilic Causal-Teleological Graph**
(`A --[mechanism]--> B`):

1. **Accent-Insensitive Discourse State Machine (`build_document_discourse_contexts`)**:
   - Tracks multi-turn Shareholder Q&A exchanges (`Câu hỏi số k` / `Question k` -> consecutive Executive
     replies, even under OCR diacritic loss like `"Trà lòi:"` or `"Ông Truong Gia Bình cho biết:"`) and
     Strategic Proposals (`Đề án`, `Thông điệp Chủ tịch/TGĐ`, `Định hướng chiến lược`).
   - Binds Question + Executive Answers under a shared `discourse_id` and propagates subject entities &
     economic facets from questions/proposals down to every reply paragraph (resolving implicit
     coreference like `"ông Toshikazu Nambu"` -> `"PCT Tập đoàn lớn nhất Nhật Bản"` or `"Masterise"` /
     `"Yeah1"` -> Vietnamese executive answers).
2. **4-Tier Epistemic Role & Insight Potential Scorer (`I(u) in [0.0, 0.98]`)**:
   - `Tier 0` (`PROCEDURAL_NOISE`, `0.0`): Ballot tallying (`99,9245%`), vote sealing, charter templates,
     hypothetical ESOP examples (`Nguyễn Văn A`), PR trophy lists, and duplicate English mirror blocks.
   - `Tier 1` (`ACCOUNTING_SYMPTOM`, `0.25..0.44`): Raw financial figures & standard segment definitions.
   - `Tier 2` (`OPERATIONAL_FACT`, `0.58..0.78`): Commercial leading indicators (new bookings/backlog,
     store openings, capacity, ownership transfers, specific legal decrees/dispatches).
   - `Tier 3` (`CAUSAL_MECHANISM`, `0.80..0.98`): Substantive executive Q&A testimony, competitive moat
     wedges, strategic capital allocation & new business bets.
3. **8 Universal Bipartite Economic Facets (`ECONOMIC_FACETS`)**:
   - Bridges Accounting Outcome vocabulary (`doanh thu bộ phận`, `CASA`, `biên lợi nhuận`, `phí bảo lãnh`,
     `công ty liên kết`) with Concrete Operational/Strategic Mechanism vocabulary (`hợp đồng Nhật Bản`,
     `bầu HĐQT quốc tế`, `xMainframe COBOL`, `đơn giá nhân công`, `concert Yeah1`, `Masterise`, `bảo hiểm`).
4. **Value-Gradient Personalized PageRank (`propagate_epistemic_value_ppr`) & `select_company_story_spine`**:
   - Climbs upward along `I(v) * Novelty(v | Seed)` without requiring target mechanisms to repeat the
     seed's surface accounting entity names, and extracts orthogonal company-wide narrative peaks via
     Submodular Facility-Location / MMR.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

EPISTEMIC_SCHEMA_VERSION = "epistemic-v3.0"

FACET_IDS: tuple[str, ...] = (
    "F1_GROWTH_BACKLOG_MARKET",
    "F2_MARGIN_PRICING_COST",
    "F3_FUNDING_CASA_LIQUIDITY",
    "F4_FEE_ECOSYSTEM_IB",
    "F5_ASSET_CREDIT_RISK",
    "F6_CONSOLIDATION_MNA_DIVIDEND",
    "F7_CAPEX_TECH_NEW_BET",
    "F8_GOVERNANCE_HUMAN_CAPITAL",
)

# Universal bipartite vocabulary (accent-stripped lowercase ASCII phrases) across HOSE sectors:
# Each facet unites Outcome/Accounting cues with Operational/Strategic Mechanism cues.
_FACET_ASCII_PATTERNS: dict[str, re.Pattern[str]] = {
    "F1_GROWTH_BACKLOG_MARKET": re.compile(
        r"(?:\bdoanh\s+thu\b|cntt\s+nuoc\s+ngoai|xuat\s+khau|thi\s+phan|tong\s+thu\s+nhap|"
        r"ky\s+moi|backlog|hop\s+dong\s+lon|mega\s*deal|nhat\s+ban|han\s+quoc|chaebol|"
        r"hoa\s+ky|chau\s+au|apac|mo\s+moi|cua\s+hang|nha\s+thuoc|long\s+chau|bach\s+hoa\s+xanh|"
        r"sieu\s+thi|omni[\s-]*channel|khach\s+hang\s+lon|doi\s+tac\s+chien\s+luoc|bao\s+chung|"
        r"fdi|mang\s+luoi\s+cua\s+hang)",
        re.IGNORECASE,
    ),
    "F2_MARGIN_PRICING_COST": re.compile(
        r"(?:bien\s+loi\s+nhuan|loi\s+nhuan\s+gop|bien\s+gop|bien\s+bo\s+phan|ket\s+qua.*bo\s+phan|"
        r"gia\s+von|chi\s+phi\s+hoat\s+dong|\bcir\b|cntt\s+trong\s+nuoc|don\s+gia\s+nhan\s+cong|"
        r"luong\s+co\s+ban|dinh\s+muc|nghi\s+quyet\s+57|dich\s+vu\s+cong|thi\s+hanh\s+an|"
        r"managed\s+services|time\s*&\s*materials|tron\s+goi|\bcobol\b|mainframe|xmainframe|"
        r"tu\s+dong\s+hoa|nang\s+suat|toi\s+uu\s+chi\s+phi|gia\s+dau\s+vao|gia\s+nguyen\s+lieu)",
        re.IGNORECASE,
    ),
    "F3_FUNDING_CASA_LIQUIDITY": re.compile(
        r"(?:\bcasa\b|tien\s+gui\s+khong\s+ky\s+han|chi\s+phi\s+lai|chi\s+phi\s+von|\bcof\b|"
        r"\bnim\b|bien\s+lai\s+thuan|giay\s+to\s+co\s+gia|tien\s+gui\s+co\s+ky\s+han|"
        r"dau\s+tu\s+tai\s+chinh\s+ngan\s+han|huy\s+dong\s+von|thu\s+hut\s+khach\s+hang|"
        r"concert|yeah1|thuong\s+hieu|ngan\s+hang\s+giao\s+dich|khach\s+hang\s+ca\s+nhan)",
        re.IGNORECASE,
    ),
    "F4_FEE_ECOSYSTEM_IB": re.compile(
        r"(?:thu\s+nhap\s+dich\s+vu|lai\s+thuan.*dich\s+vu|bao\s+lanh\s+phat\s+hanh|"
        r"thu\s+nhap\s+ngoai\s+lai|bancassurance|phi\s+bao\s+hiem|cong\s+ty\s+bao\s+hiem|"
        r"bao\s+hiem\s+phi\s+nhan\s+tho|acb\s+insurance|thu\s+xep\s+von|trai\s+phieu\s+doanh\s+nghiep|"
        r"quan\s+ly\s+gia\s+san|beerich|ban\s+cheo|ngan\s+hang\s+dau\s+tu)",
        re.IGNORECASE,
    ),
    "F5_ASSET_CREDIT_RISK": re.compile(
        r"(?:no\s+xau|\bnpl\b|no\s+nhom\s+2|bao\s+phu\s+no\s+xau|\bllr\b|du\s+phong\s+rui\s+ro|"
        r"chi\s+phi\s+tin\s+dung|tang\s+truong\s+tin\s+dung|han\s+muc\s+tin\s+dung|room\s+tin\s+dung|"
        r"du\s+no\s+cho\s+vay|bat\s+dong\s+san|masterise|tham\s+dinh|phan\s+tan\s+rui\s+ro|"
        r"ton\s+kho|phai\s+thu\s+kho\s+doi|xung\s+dot|dut\s+gay\s+chuoi\s+cung\s+ung)",
        re.IGNORECASE,
    ),
    "F6_CONSOLIDATION_MNA_DIVIDEND": re.compile(
        r"(?:hop\s+nhaat|hop\s+nhat|cong\s+ty\s+con|cong\s+ty\s+lien\s+ket|lien\s+doanh|"
        r"phuong\s+phap\s+von\s+chu|phan\s+lai\s+trong\s+cong\s+ty\s+lien\s+ket|co\s+tuc|"
        r"chuyen\s+giao.*von|scic|bo\s+cong\s+an|\bbca\b|cong\s+van\s*(?:so\s*)?1906|"
        r"quyen\s+kiem\s+soat|dai\s+dien\s+chu\s+so\s+huu|thoai\s+von|m&a|bang\s+can\s+doi)",
        re.IGNORECASE,
    ),
    "F7_CAPEX_TECH_NEW_BET": re.compile(
        r"(?:tai\s+san\s+co\s+dinh|xdcb\s+do\s+dang|\bcapex\b|khau\s+hao|nha\s+may\s+ai|"
        r"ai\s+factory|ai\s+first|trung\s+tam\s+du\s+lieu|data\s+center|fornix|tu\s+rack|"
        r"\bgpu\b|\bram\b|\bcpu\b|ban\s+dan|vi\s+mach|\bchip\b|\buav\b|duong\s+sat|luong\s+tu|"
        r"tai\s+san\s+so|kinh\s+doanh\s+vang|to\s+hop\s+hoa\s+chat|nghi\s+son|cong\s+suat|"
        r"chu\s+quyen\s+cong\s+nghe|de\s+an\s+06)",
        re.IGNORECASE,
    ),
    "F8_GOVERNANCE_HUMAN_CAPITAL": re.compile(
        r"(?:bau\s+bo\s+sung|de\s+cu.*hdqt|thanh\s+vien\s+hdqt|chuyen\s+giao\s+the\s+he|"
        r"ke\s+thua|tuyen\s+dung|nguon\s+nhan\s+luc|dao\s+tao\s+ai|\besop\b|chinh\s+sach\s+nhan\s+su)",
        re.IGNORECASE,
    ),
}

# Procedural / Ballot / Hypothetical / Award / Charter noise patterns (accent-insensitive ASCII)
_BALLOT_OR_QUORUM_ASCII_RE = re.compile(
    r"(?:tong\s+so\s+to\s+phieu|tong\s+so\s+phieu\s+(?:khong\s+)?tham\s+gia\s+bieu\s+quyet|"
    r"bien\s+ban\s+kiem\s+phieu|ket\s+qua\s+kiem\s+phieu|"
    r"so\s+phieu\s+hop\s+le|so\s+phieu\s+khong\s+hop\s+le|"
    r"phieu\s+tan\s+thanh|phieu\s+khong\s+tan\s+thanh|phieu\s+khong\s+co\s+y\s+kien|"
    r"dong\s+goi,\s*niem\s+phong.*phieu\s+bieu\s+quyet|kiem\s+tra\s+tu\s+cach\s+(?:co\s+dong|dai\s+bieu)|"
    r"bau\s+ban\s+kiem\s+phieu|ban\s+thu\s+ky\s+dai\s+hoi|thong\s+qua\s+quy\s+che\s+lam\s+viec\s+tai\s+dai\s+hoi)",
    re.IGNORECASE,
)
_CHARTER_OR_PR_NOISE_ASCII_RE = re.compile(
    r"(?:dieu\s+le\s+mau\s+cong\s+ty\s+dai\s+chung|thong\s+tu\s+116/2020/tt-btc|"
    r"ban\s+de\s+xuat\s+sua\s+doi\s+quy\s+che\s+(?:noi\s+bo|hoat\s+dong\s+cua\s+hdqt)|"
    r"de\s+xuat\s+lua\s+chon\s+cong\s+ty\s+kiem\s+toan|danh\s+sach\s+cong\s+ty\s+kiem\s+toan\s+doc\s+lap)",
    re.IGNORECASE,
)
_AWARD_SECTION_ASCII_RE = re.compile(
    r"(?:giai\s+thuong\s+tieu\s+bieu|danh\s+hieu\s+va\s+giai\s+thuong|thanh\s+tich\s+va\s+giai\s+thuong)",
    re.IGNORECASE,
)
_HYPOTHETICAL_EXAMPLE_ASCII_RE = re.compile(
    r"(?:>\s*vi\s+du\s*:?\s*$|so\s+luong\s+co\s+phieu.*ong\s+nguyen\s+van\s+a\b|"
    r"gia\s+su\s+ong\s+nguyen\s+van\s+a\b)",
    re.IGNORECASE,
)
_POLITE_ACK_ONLY_ASCII_RE = re.compile(
    r"^[^\n]{0,45}(?:tran\s+trong\s+ghi\s+nhan|chan\s+thanh\s+cam\s+on\s+su\s+ung\s+ho)[^\n]{0,120}$",
    re.IGNORECASE,
)
_PURE_ACCOUNTING_DEF_ASCII_RE = re.compile(
    r"^(?:[\-\•\*]\s*)?(?:mot\s+bo\s+phan\s+la\s+mot\s+hop\s+phan\s+co\s+the\s+xac\s+dinh|"
    r"san\s+pham\s+va\s+dich\s+vu\s+[^\n:]{3,50}:\s*bao\s+gom\s+hoat\s+dong|"
    r"hoat\s+dong\s+chinh\s+cua\s+(?:tap\s+doan|cong\s+ty|ngan\s+hang)\s+la\s+cung\s+cap|"
    r"nhung\s+giao\s+dich\s+lam\s+thay\s+doi\s+ty\s+le\s+so\s+huu\s+cua\s+(?:tap\s+doan|cong\s+ty|ngan\s+hang)\s+trong\s+cong\s+ty\s+con|"
    r"truong\s+hop\s+gia\s+ban\s+no\s+(?:thap|cao)\s+hon\s+gia\s+tri\s+khoan\s+no|"
    r"bao\s+cao\s+tai\s+chinh\s+hop\s+nhat\s+duoc\s+lap\s+tren\s+co\s+so)",
    re.IGNORECASE,
)
_ACCOUNTING_POLICY_SECTION_ASCII_RE = re.compile(
    r"(?:chinh\s+sach\s+ke\s+toan\s+chu\s+yeu|tom\s+tat\s+cac\s+chinh\s+sach\s+ke\s+toan|"
    r"co\s+so\s+lap\s+bao\s+cao\s+tai\s+chinh|nguyen\s+tac\s+lap\s+va\s+trinh\s+bay|"
    r"co\s+so\s+hop\s+nhat|don\s+vi\s+tien\s+te\s+ke\s+toan|"
    r"ap\s+dung\s+cac\s+chuan\s+muc\s+ke\s+toan)",
    re.IGNORECASE,
)

# Discourse Q&A & Strategic Section State-Machine Patterns (accent-insensitive ASCII!)
_QA_ZONE_ENTER_ASCII_RE = re.compile(
    r"(?:\bthao\s+luan\b|hoi\s*[-–/]\s*dap|y\s+kien\s+co\s+dong|phien\s+thao\s+luan|"
    r"\bcau\s+hoi\s*(?:so\s*)?\d+\b|\bquestion\s*\d+\b)",
    re.IGNORECASE,
)
_QA_ZONE_EXIT_ASCII_RE = re.compile(
    r"(?:ket\s+qua\s+bieu\s+quyet|kiem\s+phieu\s+bieu\s+quyet|thong\s+qua\s+bien\s+ban|"
    r"be\s+mac\s+dai\s+hoi|phu\s+luc\s+\d+|ban\s+de\s+xuat\s+sua\s+doi)",
    re.IGNORECASE,
)
_QUESTION_START_ASCII_RE = re.compile(
    r"(?:^\s*(?:[\-\•\*]\s*)?(?:cau\s+hoi\s*(?:so\s*)?\d+|question\s*\d+)\b|"
    r"\bco\s+dong\s+(?:dat\s+cau\s+hoi|de\s+nghi\s+ban\s+lanh\s+dao\s+lam\s+ro)\b)",
    re.IGNORECASE,
)
# Matches `"Trả lời:"`, OCR-glitched `"Trà lòi:"`, `"Ông Truong Gia Bình cho biết:"`, `"Bà ... phát biểu:"`
_ANSWER_TURN_ASCII_RE = re.compile(
    r"(?:(?:^|>)\s*tra\s+loi\s*:?|"
    r"^\s*(?:ong|ba|chu\s+toa|doan\s+chu\s+tich|ban\s+lanh\s+dao|dai\s+dien)\s+[a-z\s\.]{2,42}:"
    r"|\b(?:ong|ba)\s+[a-z][a-z\s]{3,38}\s+(?:cho\s+biet|chia\s+se|tra\s+loi|giai\s+dap|phat\s+bieu)\b)",
    re.IGNORECASE,
)
_STRATEGIC_SECTION_ASCII_RE = re.compile(
    r"(?:thong\s+diep\s+(?:chu\s+tich|tong\s+giam\s+doc|ban\s+lanh\s+dao)|"
    r"chien\s+luoc\s+(?:phat\s+trien|kinh\s+doanh)|dinh\s+huong\s+(?:chien\s+luoc|phat\s+trien|kinh\s+doanh)|"
    r"cong\s+nghe\s+chien\s+luoc|de\s+an\s+thanh\s+lap|phan\s+tich\s+ket\s+qua\s+kinh\s+doanh\s+theo\s+khoi|"
    r"chuyen\s+giao\s+quyen\s+dai\s+dien)",
    re.IGNORECASE,
)
_CAUSAL_MECHANISM_CUES_ASCII_RE = re.compile(
    r"(?:\bnho\b|\bgiup\b|nguyen\s+nhan|dong\s+luc|loi\s+the\s+canh\s+tranh|bao\s+chung|"
    r"giai\s+quyet\s+(?:bai\s+toan|thach\s+thuc)|loi\s+giai|thay\s+doi\s+cach\s+tinh|"
    r"chuyen\s+tu\s+.*sang\s+|uu\s+tien\s+khai\s+thac|khong\s+anh\s+huong|phuong\s+phap\s+von\s+chu|"
    r"chuyen\s+giao.*bo\s+cong\s+an|cong\s+van\s*(?:so\s*)?1906|nghi\s+quyet\s+\d+)",
    re.IGNORECASE,
)

_EN_STOPWORDS_RE = re.compile(
    r"\b(?:the|and|of|to|in|for|with|shall|that|from|under|during|shareholders|company|board|approval|context)\b",
    re.IGNORECASE,
)
_PROPER_PHRASE_RE = re.compile(
    r"\b(?:[A-ZÀ-Ỹ][a-zà-ỹA-ZÀ-Ỹ0-9&\-]{1,25}(?:\s+[A-ZÀ-Ỹ0-9][a-zà-ỹA-ZÀ-Ỹ0-9&\-]{1,25}){1,4}"
    r"|[A-Z]{2,10}\d{0,3}|xMainframe|Masterise|Yeah1|BeeRich|Fornix)\b"
)
_GENERIC_PROPER_STOP_ASCII = frozenset(
    {
        "cong ty",
        "tap doan",
        "ngan hang",
        "hoi dong quan tri",
        "ban kiem soat",
        "ban giam doc",
        "ban lanh dao",
        "dai hoi dong",
        "co dong",
        "bao cao tai chinh",
        "bao cao thuong nien",
        "thuyet minh",
        "viet nam",
        "chu tich",
        "tong giam doc",
        "cau hoi",
        "tra loi",
        "dieu le",
        "nghi quyet",
        "to trinh",
    }
)


def norm_vn_ascii(text: str) -> str:
    """Accent-insensitive, OCR-glitch-tolerant lowercase ASCII normalizer."""
    if not text:
        return ""
    s = unicodedata.normalize("NFD", str(text)).replace("đ", "d").replace("Đ", "d")
    ascii_s = s.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", ascii_s).strip()


def is_residual_english_mirror(text: str) -> bool:
    """Detect untranslated/duplicate English blocks inside bilingual filings (`0` LLM)."""
    clean = str(text or "").strip()
    if len(clean) < 80:
        return False
    alpha_chars = [ch for ch in clean if ch.isalpha()]
    if len(alpha_chars) < 40:
        return False
    non_ascii_alpha = sum(1 for ch in alpha_chars if ord(ch) > 127)
    if (non_ascii_alpha / len(alpha_chars)) >= 0.015:
        return False
    en_hits = len(_EN_STOPWORDS_RE.findall(clean))
    return en_hits >= 4


def extract_salient_proper_anchors(text: str) -> list[str]:
    """Extract proper names, partner brands, products, decrees & geographies for discourse inheritance."""
    if not text:
        return []
    found: list[str] = []
    seen_norm: set[str] = set()
    for m in _PROPER_PHRASE_RE.finditer(text):
        cand = m.group(0).strip(" ,.;:()[]{}\"'")
        n_cand = norm_vn_ascii(cand)
        if len(n_cand) < 3 or n_cand in _GENERIC_PROPER_STOP_ASCII:
            continue
        if any(n_cand.startswith(p + " ") and len(n_cand) <= len(p) + 3 for p in _GENERIC_PROPER_STOP_ASCII):
            continue
        if n_cand not in seen_norm:
            seen_norm.add(n_cand)
            found.append(cand)
        if len(found) >= 10:
            break
    return found


def extract_economic_facets(text_ascii: str) -> list[str]:
    """Map passage onto universal Bipartite Economic Facets (`F1..F8`)."""
    if not text_ascii:
        return []
    return [fid for fid, pat in _FACET_ASCII_PATTERNS.items() if pat.search(text_ascii)]


@dataclass(frozen=True, slots=True)
class BlockDiscourseContext:
    block_id: str
    discourse_id: str | None = None
    discourse_role: str = "STANDALONE"
    inherited_entities: tuple[str, ...] = ()
    inherited_facets: tuple[str, ...] = ()


def build_document_discourse_contexts(
    blocks: Sequence[Any],
    *,
    version_id: str,
    sub_doc_slot: str = "",
) -> dict[str, BlockDiscourseContext]:
    """Run deterministic stateful Discourse & Section tracking across ordered `CanonicalBlock`s (`0` LLM).

    Solves implicit coreference & Q&A splitting:
    - Links `Câu hỏi số k` / `Question k` with all consecutive Executive Reply blocks (`Trả lời:`,
      `Trà lòi:`, `Ông ... cho biết:`) under a single `discourse_id`.
    - Propagates question anchors (`Toshikazu Nambu`, `Nhật Bản`, `Masterise`, `Yeah1`, `COBOL`, `FTEL`)
      and economic facets onto every reply block in that exchange — even if the question header in a
      bilingual AGM was in English!
    """
    out: dict[str, BlockDiscourseContext] = {}
    in_qa_zone = False
    qa_seq = 0
    in_answer_phase = False
    active_qa_entities: list[str] = []
    active_qa_facets: set[str] = set()

    active_prop_slug: str | None = None
    active_prop_entities: list[str] = []
    active_prop_facets: set[str] = set()
    slot_up = str(sub_doc_slot or "").strip().upper()

    for blk in blocks:
        bid = str(getattr(blk, "id", "") or "")
        btype = str(getattr(blk, "block_type", "") or "")
        raw_txt = str(getattr(blk, "normalized_text", "") or "").strip()
        sec_path = str(getattr(blk, "section_path", "") or "").strip()
        if not bid or not raw_txt or btype == "table_card":
            continue

        sec_ascii = norm_vn_ascii(sec_path)
        txt_ascii = norm_vn_ascii(raw_txt)
        comb_ascii = f"{sec_ascii} > {txt_ascii}"

        # 1) Update Q&A Zone state
        if _QA_ZONE_EXIT_ASCII_RE.search(sec_ascii) or (
            in_qa_zone and _BALLOT_OR_QUORUM_ASCII_RE.search(txt_ascii)
        ):
            in_qa_zone = False
            in_answer_phase = False
        elif _QA_ZONE_ENTER_ASCII_RE.search(sec_ascii) or _QUESTION_START_ASCII_RE.search(txt_ascii):
            in_qa_zone = True

        if in_qa_zone:
            is_q_start = bool(_QUESTION_START_ASCII_RE.search(txt_ascii))
            is_ans_Cue = bool(_ANSWER_TURN_ASCII_RE.search(sec_ascii) or _ANSWER_TURN_ASCII_RE.search(txt_ascii))

            if is_q_start:
                qa_seq += 1
                in_answer_phase = False
                active_qa_entities = extract_salient_proper_anchors(f"{sec_path} {raw_txt}")
                active_qa_facets = set(extract_economic_facets(comb_ascii))
                disc_id = f"disc:{version_id}:qa:{qa_seq}"
                out[bid] = BlockDiscourseContext(
                    block_id=bid,
                    discourse_id=disc_id,
                    discourse_role="PROMPT_QUESTION",
                    inherited_entities=tuple(active_qa_entities[:8]),
                    inherited_facets=tuple(sorted(active_qa_facets)),
                )
                continue

            if qa_seq > 0:
                cur_ents = extract_salient_proper_anchors(raw_txt)
                cur_facets = extract_economic_facets(comb_ascii)
                if is_ans_Cue or in_answer_phase:
                    in_answer_phase = True
                    merged_ents = list(dict.fromkeys([*active_qa_entities, *cur_ents]))[:10]
                    merged_facets = sorted(active_qa_facets | set(cur_facets))
                    out[bid] = BlockDiscourseContext(
                        block_id=bid,
                        discourse_id=f"disc:{version_id}:qa:{qa_seq}",
                        discourse_role="EXECUTIVE_ANSWER",
                        inherited_entities=tuple(merged_ents),
                        inherited_facets=tuple(merged_facets),
                    )
                    continue
                else:
                    # Multi-paragraph shareholder question continuation (e.g. Q3 / Q5 sub-bullets before reply!)
                    for e in cur_ents:
                        if e not in active_qa_entities:
                            active_qa_entities.append(e)
                    active_qa_facets.update(cur_facets)
                    out[bid] = BlockDiscourseContext(
                        block_id=bid,
                        discourse_id=f"disc:{version_id}:qa:{qa_seq}",
                        discourse_role="PROMPT_QUESTION",
                        inherited_entities=tuple(active_qa_entities[:8]),
                        inherited_facets=tuple(sorted(active_qa_facets)),
                    )
                    continue

        # 2) Strategic Proposal / Executive Message / Segment MD&A Section Continuity
        leaf_sec = sec_ascii.split(">")[-1].strip() if sec_ascii else ""
        if _STRATEGIC_SECTION_ASCII_RE.search(sec_ascii):
            slug = re.sub(r"[^a-z0-9]+", "_", leaf_sec)[:32].strip("_") or "strat"
            if slug != active_prop_slug:
                active_prop_slug = slug
                active_prop_entities = extract_salient_proper_anchors(f"{sec_path} {raw_txt}")
                active_prop_facets = set(extract_economic_facets(comb_ascii))
            else:
                for e in extract_salient_proper_anchors(raw_txt)[:4]:
                    if len(active_prop_entities) < 8 and e not in active_prop_entities:
                        active_prop_entities.append(e)
                active_prop_facets.update(extract_economic_facets(comb_ascii))
            role = "STRATEGIC_PROPOSAL" if slot_up == "AGM_TARGETS" else "NARRATIVE_ANALYSIS"
            out[bid] = BlockDiscourseContext(
                block_id=bid,
                discourse_id=f"disc:{version_id}:prop:{slug}",
                discourse_role=role,
                inherited_entities=tuple(active_prop_entities[:8]),
                inherited_facets=tuple(sorted(active_prop_facets)),
            )
            continue

        active_prop_slug = None
        out[bid] = BlockDiscourseContext(block_id=bid)

    return out


def compute_epistemic_profile(
    text: str,
    section_path: str = "",
    *,
    sub_doc_slot: str = "",
    block_type: str = "paragraph",
    structural_signals: Sequence[str] = (),
    discourse_ctx: BlockDiscourseContext | None = None,
    is_boilerplate: bool = False,
) -> dict[str, Any]:
    """Compute 4-Tier Epistemic Role (`0..3`), `insight_score` (`[0.0..0.98]`), and `facets` (`0` LLM)."""
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    sec_ascii = norm_vn_ascii(section_path)
    txt_ascii = norm_vn_ascii(clean)
    comb_ascii = f"{sec_ascii} :: {txt_ascii}"
    slot_up = str(sub_doc_slot or "").strip().upper()
    d_role = discourse_ctx.discourse_role if discourse_ctx is not None else "STANDALONE"
    d_id = discourse_ctx.discourse_id if discourse_ctx is not None else None

    # ── Tier 0: Hard Procedural, Ballot, Charter, PR Trophy, Hypothetical & English Mirror Filter ──
    if (
        is_boilerplate
        or len(clean) < 35
        or (bool(slot_up) and is_residual_english_mirror(clean))
        or _BALLOT_OR_QUORUM_ASCII_RE.search(comb_ascii)
        or _CHARTER_OR_PR_NOISE_ASCII_RE.search(comb_ascii)
        or _AWARD_SECTION_ASCII_RE.search(sec_ascii)
        or _HYPOTHETICAL_EXAMPLE_ASCII_RE.search(comb_ascii)
    ):
        return {
            "v": EPISTEMIC_SCHEMA_VERSION,
            "tier": 0,
            "role": "PROCEDURAL_NOISE",
            "insight_score": 0.0,
            "facets": [],
            "discourse_id": d_id,
            "discourse_role": d_role,
        }

    own_facets = set(extract_economic_facets(comb_ascii))
    if discourse_ctx is not None and discourse_ctx.inherited_facets:
        own_facets.update(discourse_ctx.inherited_facets)
    facets = sorted(own_facets)

    # Polite one-liner acknowledgements in AGM ("cảm ơn sự ủng hộ quý báu...") are low-value
    if d_role == "EXECUTIVE_ANSWER" and _POLITE_ACK_ONLY_ASCII_RE.match(txt_ascii):
        return {
            "v": EPISTEMIC_SCHEMA_VERSION,
            "tier": 1,
            "role": "POLITE_ACK",
            "insight_score": 0.20,
            "facets": facets,
            "discourse_id": d_id,
            "discourse_role": d_role,
        }

    # Pure boilerplate segment/activity definitions & Note 2/3 VAS accounting-policy textbook rules
    is_specific_mna_event = bool(
        re.search(r"(?:1906|bo\s+cong\s+an|\bscic\b|\d+[.,]?\d*\s*ty\b)", txt_ascii)
    )
    if not is_specific_mna_event and (
        (_PURE_ACCOUNTING_DEF_ASCII_RE.match(txt_ascii) and not _CAUSAL_MECHANISM_CUES_ASCII_RE.search(txt_ascii))
        or _ACCOUNTING_POLICY_SECTION_ASCII_RE.search(sec_ascii)
    ):
        return {
            "v": EPISTEMIC_SCHEMA_VERSION,
            "tier": 1,
            "role": "ACCOUNTING_SYMPTOM",
            "insight_score": 0.28,
            "facets": facets,
            "discourse_id": d_id,
            "discourse_role": d_role,
        }

    has_causal_cue = bool(_CAUSAL_MECHANISM_CUES_ASCII_RE.search(comb_ascii))
    has_quant = bool(re.search(r"\d+(?:[.,]\d+)?\s*(?:%|tỷ|triệu|usd|bps|cửa hàng|nhà thuốc|hợp đồng)", clean.lower()))
    sig_set = set(structural_signals or ())

    # ── Tier 3: Executive Q&A Testimony, Strategic Proposals, Legal Ownership Shifts & Moat Mechanisms ──
    if d_role == "EXECUTIVE_ANSWER" and len(clean) >= 55:
        score = min(0.98, 0.88 + (0.05 if has_causal_cue else 0.0) + (0.04 if has_quant else 0.0))
        return {
            "v": EPISTEMIC_SCHEMA_VERSION,
            "tier": 3,
            "role": "CAUSAL_MECHANISM",
            "insight_score": round(score, 3),
            "facets": facets,
            "discourse_id": d_id,
            "discourse_role": d_role,
        }

    if d_role == "PROMPT_QUESTION" and len(clean) >= 45:
        score = min(0.86, 0.76 + (0.06 if facets else 0.0))
        return {
            "v": EPISTEMIC_SCHEMA_VERSION,
            "tier": 2,
            "role": "DISCOURSE_QUESTION",
            "insight_score": round(score, 3),
            "facets": facets,
            "discourse_id": d_id,
            "discourse_role": d_role,
        }

    if (
        (d_role in ("STRATEGIC_PROPOSAL", "NARRATIVE_ANALYSIS") and (has_causal_cue or has_quant))
        or ("CONSOLIDATION_SCOPE" in sig_set and (has_causal_cue or "1906" in txt_ascii or "bo cong an" in txt_ascii))
        or (slot_up in ("BCTN_CORE", "AGM_TARGETS") and has_causal_cue and bool(facets) and len(clean) >= 90)
    ):
        base_s = 0.83 if has_causal_cue else 0.79
        score = min(0.95, base_s + (0.05 if has_quant else 0.0) + (0.04 if len(facets) >= 2 else 0.0))
        tier_val = 3 if score >= 0.80 else 2
        return {
            "v": EPISTEMIC_SCHEMA_VERSION,
            "tier": tier_val,
            "role": "CAUSAL_MECHANISM" if tier_val == 3 else "OPERATIONAL_FACT",
            "insight_score": round(score, 3),
            "facets": facets,
            "discourse_id": d_id,
            "discourse_role": d_role,
        }

    # ── Tier 2: Operational Breakdowns, Ownership Disclosures, Segment MD&A, AGM Resolutions ──
    if sig_set or (facets and has_causal_cue) or (slot_up in ("BCTN_CORE", "AGM_TARGETS") and facets and has_quant):
        score = min(0.78, 0.62 + (0.06 if has_quant else 0.0) + (0.05 if has_causal_cue else 0.0))
        return {
            "v": EPISTEMIC_SCHEMA_VERSION,
            "tier": 2,
            "role": "OPERATIONAL_FACT",
            "insight_score": round(score, 3),
            "facets": facets,
            "discourse_id": d_id,
            "discourse_role": d_role,
        }

    # ── Tier 1: Standard Accounting Figures / Tables / Filing Mechanics ──
    base_t1 = 0.36 if (block_type == "table" or facets) else 0.26
    return {
        "v": EPISTEMIC_SCHEMA_VERSION,
        "tier": 1,
        "role": "ACCOUNTING_SYMPTOM",
        "insight_score": round(base_t1, 3),
        "facets": facets,
        "discourse_id": d_id,
        "discourse_role": d_role,
    }


def unit_epistemic_meta(unit: Any) -> tuple[int, float, list[str], str | None, str]:
    """Read `(tier, insight_score, facets, discourse_id, discourse_role)` from a KnowledgeUnit (with safe fallback)."""
    feats = getattr(unit, "features_json", None)
    if not isinstance(feats, Mapping):
        feats = {}
    prov = getattr(unit, "provenance_json", None)
    if not isinstance(prov, Mapping):
        prov = {}
    sec_norm = norm_vn_ascii(str(prov.get("section_path") or ""))
    txt_norm = norm_vn_ascii(str(getattr(unit, "text", "") or ""))
    if _BALLOT_OR_QUORUM_ASCII_RE.search(f"{sec_norm} :: {txt_norm}"):
        return (0, 0.0, [], None, "STANDALONE")
    ep = feats.get("epistemic")
    if isinstance(ep, Mapping) and "tier" in ep:
        return (
            int(ep.get("tier") or 0),
            float(ep.get("insight_score") or 0.0),
            [str(f) for f in (ep.get("facets") or ()) if f],
            str(ep["discourse_id"]) if ep.get("discourse_id") else None,
            str(ep.get("discourse_role") or "STANDALONE"),
        )
    comp = compute_epistemic_profile(
        str(getattr(unit, "text", "") or ""),
        str(prov.get("section_path") or ""),
        sub_doc_slot=str(prov.get("sub_doc_slot") or ""),
        block_type=str(prov.get("block_type") or "paragraph"),
        structural_signals=tuple(feats.get("structural_signals") or ()),
    )
    return (
        int(comp["tier"]),
        float(comp["insight_score"]),
        list(comp["facets"]),
        comp["discourse_id"],
        str(comp["discourse_role"]),
    )


def _token_set_ascii(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", norm_vn_ascii(text)) if not w.isdigit()}


def propagate_epistemic_value_ppr(
    *,
    seed_unit_ids: Sequence[str],
    units_by_id: Mapping[str, Any],
    adjacency: Mapping[str, Sequence[tuple[str, float]]],
    max_hops: int = 3,
    top_n: int = 6,
) -> list[tuple[float, int, str]]:
    """Value-Gradient Personalized PageRank (`0` LLM tokens, `O(|E_sub|)`).

    Propagates activation from initial query-matched `seed_unit_ids` (`Tier 1/2` symptoms) UPWARD
    along `Epistemic Upgrade` & `Discourse` edges toward high-insight `Tier 2/3` business mechanisms:

        W(u -> v) = edge_weight(u, v)
                    * (1 + 0.85 * max(0, I(v) - I(u)))
                    * (0.25 + 0.75 * I(v)^2)
                    * Novelty(v | Seed)

    Critically: NEVER requires `v` to share the surface accounting entity string of `Seed`, enabling
    autonomous discovery of `Toshikazu Nambu`, `xMainframe COBOL`, `Masterise`, `Yeah1`, `ACB Insurance`, etc.!
    """
    seeds = [uid for uid in seed_unit_ids if uid in units_by_id]
    if not seeds:
        return []

    seed_set = set(seeds)
    seed_vocab: set[str] = set()
    seed_facets: set[str] = set()
    for sid in seeds:
        su = units_by_id[sid]
        seed_vocab.update(_token_set_ascii(str(getattr(su, "text", "") or "")))
        _, _, s_facs, _, _ = unit_epistemic_meta(su)
        seed_facets.update(s_facs)

    scores: dict[str, float] = {sid: 1.0 / len(seeds) for sid in seeds}
    min_hop_seen: dict[str, int] = {sid: 0 for sid in seeds}
    frontier: dict[str, float] = dict(scores)
    damping = 0.78

    for hop in range(1, max_hops + 1):
        next_frontier: dict[str, float] = {}
        for u_id, u_mass in frontier.items():
            if u_mass < 1e-4:
                continue
            u_obj = units_by_id.get(u_id)
            if u_obj is None:
                continue
            u_tier, u_ins, _, u_disc, _ = unit_epistemic_meta(u_obj)
            neighbors = adjacency.get(u_id) or ()
            if not neighbors:
                continue

            trans_weights: list[tuple[str, float]] = []
            for v_id, e_w in neighbors:
                v_obj = units_by_id.get(v_id)
                if v_obj is None:
                    continue
                v_tier, v_ins, v_facs, v_disc, _ = unit_epistemic_meta(v_obj)
                if v_tier == 0 or v_ins <= 0.15:
                    continue

                # Discourse lock: Question <-> Executive Answer jumps with near-unity conductance!
                is_same_disc = bool(u_disc and v_disc and u_disc == v_disc)
                lift = max(0.0, v_ins - u_ins)
                upgrade_gain = 1.65 if is_same_disc else (1.0 + 0.95 * lift)
                intrinsic_factor = 0.22 + 0.78 * (v_ins * v_ins)

                v_toks = _token_set_ascii(str(getattr(v_obj, "text", "") or ""))
                if v_toks and seed_vocab:
                    overlap = len(v_toks & seed_vocab) / len(v_toks)
                    # Penalize echo clones (overlap > 0.75) while rewarding fresh causal information!
                    novelty = 0.30 if overlap > 0.78 else (1.18 - 0.45 * overlap)
                else:
                    novelty = 1.0

                facet_bonus = 1.18 if (seed_facets and (set(v_facs) & seed_facets)) else 1.0
                tw = float(e_w) * upgrade_gain * intrinsic_factor * novelty * facet_bonus
                if tw > 0.02:
                    trans_weights.append((v_id, tw))

            if not trans_weights:
                continue
            norm_z = sum(tw for _, tw in trans_weights)
            for v_id, tw in trans_weights:
                propagated = damping * u_mass * (tw / norm_z)
                if propagated > 1e-4:
                    next_frontier[v_id] = next_frontier.get(v_id, 0.0) + propagated
                    scores[v_id] = scores.get(v_id, 0.0) + propagated
                    if v_id not in min_hop_seen:
                        min_hop_seen[v_id] = hop
        frontier = next_frontier
        if not frontier:
            break

    # Rank non-seed candidates by accumulated epistemic PPR mass * intrinsic insight, diversified by MMR!
    candidates: list[tuple[float, int, str]] = []
    for uid, mass in scores.items():
        if uid in seed_set:
            continue
        u_obj = units_by_id[uid]
        tier, ins, _, _, _ = unit_epistemic_meta(u_obj)
        if tier < 2 or ins < 0.55:
            continue
        final_val = mass * (0.45 + 0.55 * ins)
        candidates.append((final_val, min_hop_seen.get(uid, 1), uid))

    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    selected: list[tuple[float, int, str]] = []
    selected_tok_sets: list[set[str]] = []
    covered_facets: set[str] = set()

    while candidates and len(selected) < top_n:
        best_idx = 0
        best_mmr = -1e9
        for idx, (raw_sc, _hop_d, uid) in enumerate(candidates[:24]):
            u_obj = units_by_id[uid]
            _, _, u_facs, _, _ = unit_epistemic_meta(u_obj)
            u_toks = _token_set_ascii(str(getattr(u_obj, "text", "") or ""))
            max_sim = max(
                (len(u_toks & prev_t) / max(1, len(u_toks | prev_t)) for prev_t in selected_tok_sets),
                default=0.0,
            )
            new_facet_boost = 1.22 if (set(u_facs) - covered_facets) else 1.0
            mmr_sc = raw_sc * new_facet_boost * (1.0 - 0.55 * max_sim)
            if mmr_sc > best_mmr:
                best_mmr = mmr_sc
                best_idx = idx
        chosen = candidates.pop(best_idx)
        _, _, c_uid = chosen
        c_obj = units_by_id[c_uid]
        _, _, c_facs, _, _ = unit_epistemic_meta(c_obj)
        covered_facets.update(c_facs)
        selected_tok_sets.append(_token_set_ascii(str(getattr(c_obj, "text", "") or "")))
        selected.append((round(min(0.98, 0.55 + chosen[0] * 2.2), 4), chosen[1], c_uid))

    return selected


def select_company_story_spine(
    units: Sequence[Any],
    *,
    max_peaks: int = 7,
) -> list[Any]:
    """Extract a company's Top-K Orthogonal Narrative Peaks (`Intrinsic Story Spine`, `0` LLM).

    Uses Submodular Facility-Location / Facet-Diversified MMR over `Tier 2/3` units so every core pillar
    of a company's story (`International Market / Key Board Member`, `Tech/Product Moat`, `Pricing/Policy
    Shift`, `Consolidation/Subsidiary Cashflow`, `CAPEX / New Business Bet`) is represented without
    clustering all slots onto a single topic.
    """
    eligible: list[tuple[float, Any, set[str], set[str], str | None]] = []
    for u in units:
        tier, ins, facs, disc_id, d_role = unit_epistemic_meta(u)
        if tier < 2 or ins < 0.62:
            continue
        # Prefer substantive EXECUTIVE_ANSWER over bare PROMPT_QUESTION when picking spine representatives
        role_mult = 1.08 if d_role == "EXECUTIVE_ANSWER" else (0.88 if d_role == "PROMPT_QUESTION" else 1.0)
        prov = getattr(u, "provenance_json", None) or {}
        slot = str(prov.get("sub_doc_slot") or "").upper()
        slot_mult = 1.06 if slot in ("AGM_TARGETS", "BCTN_CORE") else 1.0
        toks = _token_set_ascii(str(getattr(u, "text", "") or ""))
        eligible.append((ins * role_mult * slot_mult, u, set(facs), toks, disc_id))

    eligible.sort(key=lambda x: -x[0])
    chosen: list[Any] = []
    chosen_toks: list[set[str]] = []
    chosen_discs: set[str] = set()
    covered_facets: set[str] = set()
    slot_counts: dict[str, int] = {}

    pool = eligible[:80]
    while pool and len(chosen) < max_peaks:
        best_i = 0
        best_val = -1e9
        for i, (base_sc, u_obj, u_facs, u_toks, u_disc) in enumerate(pool):
            if u_disc and u_disc in chosen_discs:
                continue
            prov = getattr(u_obj, "provenance_json", None) or {}
            slot = str(prov.get("sub_doc_slot") or "").upper()
            max_sim = max(
                (len(u_toks & pt) / max(1, len(u_toks | pt)) for pt in chosen_toks),
                default=0.0,
            )
            if max_sim > 0.72:
                continue
            uncovered = len(u_facs - covered_facets)
            facet_gain = 1.0 + 0.20 * min(2, uncovered)
            slot_pen = math.pow(0.92, slot_counts.get(slot, 0))
            val = base_sc * facet_gain * slot_pen * (1.0 - 0.50 * max_sim)
            if val > best_val:
                best_val = val
                best_i = i
        if best_val < 0:
            break
        _, win_u, win_facs, win_toks, win_disc = pool.pop(best_i)
        chosen.append(win_u)
        chosen_toks.append(win_toks)
        covered_facets.update(win_facs)
        if win_disc:
            chosen_discs.add(win_disc)
        w_slot = str((getattr(win_u, "provenance_json", None) or {}).get("sub_doc_slot") or "").upper()
        slot_counts[w_slot] = slot_counts.get(w_slot, 0) + 1

    return chosen
