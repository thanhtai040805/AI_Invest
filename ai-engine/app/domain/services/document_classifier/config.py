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
class MinerUConfig:
    enabled: bool = True
    api_key_env: str = "MINERU_API_KEY"
    api_base_url: str = "https://mineru.net/api/v4"
    timeout_seconds: int = 180
    fallback_to_modal: bool = True


@dataclass
class PipelineConfig:
    max_cpu_workers: int = 10


@dataclass
class FinancialProfileConfig:
    version: str = "1.0"
    page_classifier: PageClassifierConfig = field(default_factory=PageClassifierConfig)
    region_classifier: RegionClassifierConfig = field(default_factory=RegionClassifierConfig)
    mineru: MinerUConfig = field(default_factory=MinerUConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)



def load_profile(yaml_path: str = "financial_profile.yaml") -> FinancialProfileConfig:
    """Tải cấu hình lọc trang và vùng từ file YAML."""
    module_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        yaml_path if os.path.isabs(yaml_path) else os.path.join(module_dir, yaml_path),
        os.path.join(os.getcwd(), yaml_path),
        "/root/app/financial_profile.yaml",
    ]

    target_path = None
    for cand in candidates:
        if os.path.exists(cand):
            target_path = cand
            break

    if not target_path:
        print(f"[!] Profile file '{yaml_path}' not found, using defaults.")
        return FinancialProfileConfig()

    with open(target_path, "r", encoding="utf-8") as f:
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

    m_data = data.get("mineru", {})
    mineru_config = MinerUConfig(
        enabled=bool(m_data.get("enabled", True)),
        api_key_env=str(m_data.get("api_key_env", "MINERU_API_KEY")),
        api_base_url=str(m_data.get("api_base_url", "https://mineru.net/api/v4")),
        timeout_seconds=int(m_data.get("timeout_seconds", 180)),
        fallback_to_modal=bool(m_data.get("fallback_to_modal", True))
    )

    pip_data = data.get("pipeline", {})
    pipeline_config = PipelineConfig(
        max_cpu_workers=int(pip_data.get("max_cpu_workers", 10))
    )

    return FinancialProfileConfig(
        version=str(data.get("version", "1.0")),
        page_classifier=page_config,
        region_classifier=region_config,
        mineru=mineru_config,
        pipeline=pipeline_config
    )


