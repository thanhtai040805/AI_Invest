import os
from dataclasses import dataclass, field
from typing import Dict, List
import yaml


@dataclass
class PageClassifierConfig:
    skip_page_types: List[str] = field(default_factory=lambda: [
        "balance_sheet", "income_statement", "cash_flow", "cover_page"
    ])
    keep_page_types: List[str] = field(default_factory=lambda: [
        "audit_report", "directors_report", "footnote", "governance"
    ])
    signatures: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class RegionClassifierConfig:
    skip_region_keywords: List[str] = field(default_factory=list)
    keep_region_keywords: List[str] = field(default_factory=list)


@dataclass
class FinancialProfileConfig:
    version: str = "1.0"
    page_classifier: PageClassifierConfig = field(default_factory=PageClassifierConfig)
    region_classifier: RegionClassifierConfig = field(default_factory=RegionClassifierConfig)


def load_profile(yaml_path: str = "financial_profile.yaml") -> FinancialProfileConfig:
    """Tải cấu hình lọc trang và vùng từ file YAML."""
    if not os.path.isabs(yaml_path):
        current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        yaml_path = os.path.join(current_dir, yaml_path)

    if not os.path.exists(yaml_path):
        # Modal: package có thể được mount ở nơi khác với file yaml (vd. /root/app)
        # nên đường dẫn tương đối theo __file__ có thể không trỏ tới yaml → thử các vị trí cố định.
        for candidate in ("/root/app/financial_profile.yaml", "financial_profile.yaml"):
            if os.path.exists(candidate):
                yaml_path = candidate
                break

    if not os.path.exists(yaml_path):
        print(f"[!] Profile file '{yaml_path}' not found, using defaults.")
        return FinancialProfileConfig()

    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    pc_data = data.get("page_classifier", {})
    page_config = PageClassifierConfig(
        skip_page_types=pc_data.get("skip_page_types", []),
        keep_page_types=pc_data.get("keep_page_types", []),
        signatures=pc_data.get("signatures", {})
    )

    rc_data = data.get("region_classifier", {})
    region_config = RegionClassifierConfig(
        skip_region_keywords=rc_data.get("skip_region_keywords", []),
        keep_region_keywords=rc_data.get("keep_region_keywords", [])
    )

    return FinancialProfileConfig(
        version=str(data.get("version", "1.0")),
        page_classifier=page_config,
        region_classifier=region_config
    )
