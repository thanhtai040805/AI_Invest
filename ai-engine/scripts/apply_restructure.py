import os
import shutil
import re

def main():
    print("=== STARTING CODEBASE RESTRUCTURING ===")

    # 1. Unique services mapping
    services_to_move = {
        "app/services/ai_service.py": "app/application/use_cases/ai_service.py",
        "app/services/portfolio_service.py": "app/domain/services/portfolio_service.py",
        "app/services/position_helpers.py": "app/domain/services/position_helpers.py",
        "app/services/regime_service.py": "app/domain/services/regime_service.py",
        "app/services/trade_execution_service.py": "app/domain/services/trade_execution_service.py",
        "app/services/trading_rules.py": "app/domain/services/trading_rules.py",
    }

    # Move unique services
    for src, dst in services_to_move.items():
        if os.path.exists(src):
            dst_dir = os.path.dirname(dst)
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"Copied: {src} -> {dst}")
        else:
            print(f"Warning: Source service does not exist: {src}")

    # 2. Unique routers mapping
    routers_dir = "app/routers"
    presentation_api_dir = "app/presentation/api"

    if os.path.exists(routers_dir):
        for root, dirs, files in os.walk(routers_dir):
            for file in files:
                if file.endswith('.py'):
                    src_file = os.path.join(root, file)
                    # Compute relative path from app/routers
                    rel_path = os.path.relpath(src_file, routers_dir)
                    dst_file = os.path.join(presentation_api_dir, rel_path)
                    
                    # Create parent directory if needed
                    os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                    
                    # Copy if it doesn't exist, or if it is different
                    if not os.path.exists(dst_file):
                        shutil.copy2(src_file, dst_file)
                        print(f"Copied router: {src_file} -> {dst_file}")
                    else:
                        print(f"Router already exists in presentation: {dst_file}")
    else:
        print("Warning: app/routers directory does not exist.")

    # 3. Delete extensionless garbage files
    garbage_files = [
        "app/domain/services/portfolio",
        "app/domain/services/trading"
    ]
    for g in garbage_files:
        if os.path.exists(g) and os.path.isfile(g):
            os.remove(g)
            print(f"Removed garbage file: {g}")

    # 4. Remove deprecated directories app/services/ and app/routers/
    if os.path.exists(routers_dir):
        shutil.rmtree(routers_dir)
        print(f"Deleted folder: {routers_dir}")
    if os.path.exists("app/services"):
        shutil.rmtree("app/services")
        print("Deleted folder: app/services")

    # 5. Global Import Replacements
    # Mapping old import paths to new Clean Architecture paths
    import_replacements = {
        r'app\.services\.pg_pool': 'app.infrastructure.database.pg_pool',
        r'app\.services\.market_data_service': 'app.infrastructure.external_api.market_data_service',
        r'app\.services\.daily_etl': 'app.infrastructure.data_pipelines.daily_etl',
        r'app\.services\.risk_assessment_etl': 'app.infrastructure.data_pipelines.risk_assessment_etl',
        r'app\.services\.volatility_etl': 'app.infrastructure.data_pipelines.volatility_etl',
        r'app\.services\.beta_alpha_etl': 'app.infrastructure.data_pipelines.beta_alpha_etl',
        r'app\.services\.ohlcv_backfill': 'app.infrastructure.data_pipelines.ohlcv_backfill',
        r'app\.services\.financial_etl': 'app.infrastructure.data_pipelines.financial_etl',
        r'app\.services\.financial_etl_alphastock': 'app.infrastructure.data_pipelines.financial_etl_alphastock',
        r'app\.services\.backfill_service': 'app.infrastructure.data_pipelines.backfill_service',
        r'app\.services\.scraper_insider': 'app.infrastructure.data_pipelines.scraper_insider',
        r'app\.services\.job_state_service': 'app.infrastructure.monitoring.job_state_service',
        r'app\.services\.monitoring': 'app.infrastructure.monitoring.monitoring',
        r'app\.services\.news_event_store': 'app.infrastructure.llm.news_event_store',
        r'app\.services\.news_rag': 'app.infrastructure.llm.news_rag',
        
        r'app\.services\.factor_service': 'app.domain.services.factor_service',
        r'app\.services\.instrument_service': 'app.domain.services.instrument_service',
        r'app\.services\.screener_service': 'app.domain.services.screener_service',
        r'app\.services\.seasonality': 'app.domain.services.seasonality',
        r'app\.services\.sentiment_scorer': 'app.domain.services.sentiment_scorer',
        r'app\.services\.time_series_forecast': 'app.domain.services.time_series_forecast',
        r'app\.services\.ml_alpha_predictor': 'app.domain.services.ml.ml_alpha_predictor',
        
        r'app\.services\.ai_service': 'app.application.use_cases.ai_service',
        r'app\.services\.portfolio_service': 'app.domain.services.portfolio_service',
        r'app\.services\.position_helpers': 'app.domain.services.position_helpers',
        r'app\.services\.regime_service': 'app.domain.services.regime_service',
        r'app\.services\.trade_execution_service': 'app.domain.services.trade_execution_service',
        r'app\.services\.trading_rules': 'app.domain.services.trading_rules',
        
        r'app\.routers': 'app.presentation.api',
    }

    # Files in these directories will have imports replaced
    dirs_to_update = ["app", "tests", "scripts", "workflows"]
    
    count_updated = 0
    for folder in dirs_to_update:
        if not os.path.exists(folder):
            continue
        for root, _, files in os.walk(folder):
            for file in files:
                if file.endswith('.py'):
                    file_path = os.path.join(root, file)
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    new_content = content
                    for pattern, replacement in import_replacements.items():
                        new_content = re.sub(pattern, replacement, new_content)
                    
                    if new_content != content:
                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(new_content)
                        print(f"Updated imports: {file_path}")
                        count_updated += 1

    print(f"=== RESTRUCTURING DONE. Updated imports in {count_updated} files ===")

if __name__ == "__main__":
    main()
