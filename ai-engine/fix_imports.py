import os
import re
replacements = {
    r'app\.core\.risk\.advanced_metrics': 'app.domain.rules.risk.advanced_metrics',
    r'app\.core\.quality\.corporate_action': 'app.domain.rules.risk.corporate_action',
    r'app\.core\.quality\.data_quality': 'app.domain.rules.risk.data_quality',
    r'app\.core\.quality\.beneish': 'app.domain.rules.beneish',
    r'app\.core\.quality\.graph_intelligence': 'app.domain.rules.graph_intelligence',
    r'app\.core\.decision\.counter_thesis': 'app.domain.rules.counter_thesis',
    r'app\.core\.position_sizing\.kelly_sizer': 'app.domain.rules.kelly_sizer',
    r'app\.core\.position_sizing\.optimizer': 'app.domain.rules.optimizer',
    r'app\.core\.regime\.hmm_classifier': 'app.domain.rules.market.hmm_classifier',
    r'app\.core\.risk\.garch_engine': 'app.domain.rules.market.garch_engine',
    r'app\.core\.risk\.hard_laws': 'app.domain.rules.hard_laws',
    r'app\.core\.risk\.stop_loss': 'app.domain.rules.stop_loss',
    r'app\.core\.risk\.failsafe': 'app.domain.rules.failsafe',
    r'app\.core\.execution': 'app.domain.rules.execution',
    r'app\.services\.ohlcv_ingestion_service': 'app.infrastructure.data_pipelines.ohlcv_ingestion_service',
    r'app\.services\.financial_ingestion_service': 'app.infrastructure.data_pipelines.financial_ingestion_service',
    r'app\.services\.universe_manager': 'app.domain.rules.universe_manager',
    r'app\.services\.market_data_service': 'app.infrastructure.external_api.market_data_service',
    r'app\.services\.pg_pool': 'app.infrastructure.database.pg_pool',
    r'app\.database\.models': 'app.infrastructure.database.models',
    r'app\.database\.session': 'app.infrastructure.database.session',
    r'app\.dataflows\.reddit': 'app.infrastructure.external_api.social.reddit',
    r'app\.dataflows\.stocktwits': 'app.infrastructure.external_api.social.stocktwits',
    r'app\.dataflows\.config': 'app.infrastructure.external_api.config',
    r'app\.dataflows\.y_finance': 'app.infrastructure.external_api.y_finance',
    r'app\.dataflows\.stockstats_utils': 'app.infrastructure.external_api.stockstats_utils',
    r'app\.dataflows\.interface': 'app.application.ports.data_provider',
    r'app\.brain\.agents': 'app.application.agents',
    r'app\.risk_vn\.layers': 'app.domain.rules.risk',
    r'from app\.domain\.rules\.hmm_classifier': 'from app.domain.rules.market.hmm_classifier',
    r'import app\.domain\.rules\.hmm_classifier': 'import app.domain.rules.market.hmm_classifier',
}

def fix_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original = content
    for pattern, replacement in replacements.items():
        content = re.sub(pattern, replacement, content)
    
    if content != original:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Fixed: {path}")

for root, dirs, files in os.walk('.'):
    if 'node_modules' in root or '.git' in root or '.venv' in root:
        continue
    for file in files:
        if file.endswith('.py'):
            fix_file(os.path.join(root, file))
