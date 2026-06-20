import os
import shutil
import re

def move_file(src, dst):
    if os.path.exists(src):
        dst_dir = os.path.dirname(dst)
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"Moved file: {src} -> {dst}")
        os.remove(src)
    else:
        print(f"Warning: File not found: {src}")

def move_dir(src, dst):
    if os.path.exists(src):
        os.makedirs(dst, exist_ok=True)
        for root, dirs, files in os.walk(src):
            for file in files:
                src_file = os.path.join(root, file)
                rel_path = os.path.relpath(src_file, src)
                dst_file = os.path.join(dst, rel_path)
                os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                shutil.copy2(src_file, dst_file)
        shutil.rmtree(src)
        print(f"Moved folder: {src} -> {dst}")
    else:
        print(f"Warning: Folder not found: {src}")

def main():
    print("=== STARTING PHASE 2 RESTRUCTURING & CLEANUP ===")

    # 1. Move unique files from app/ml, app/llm, app/dataflows
    move_file("app/ml/test_calibration.py", "tests/unit/test_calibration.py")
    move_file("app/llm/redesign.py", "app/infrastructure/llm/redesign.py")
    move_file("app/llm/test_guardrail.py", "tests/unit/test_guardrail.py")
    move_file("app/dataflows/default_config.py", "app/infrastructure/external_api/default_config.py")
    
    # Move vendors folder from app/dataflows/vendors to app/infrastructure/vendors
    move_dir("app/dataflows/vendors", "app/infrastructure/vendors")

    # 2. Move unique files from app/quant
    move_file("app/quant/data/mlops.py", "app/domain/services/quant/data/mlops.py")
    move_file("app/quant/data/pit_fundamentals.py", "app/domain/services/quant/data/pit_fundamentals.py")
    move_file("app/quant/data/universe.py", "app/domain/services/quant/data/universe.py")
    move_file("app/quant/data/test_corporate_actions.py", "tests/unit/quant/test_corporate_actions.py")
    move_file("app/quant/data/test_mlops.py", "tests/unit/quant/test_mlops.py")
    move_file("app/quant/data/test_pit_fundamentals.py", "tests/unit/quant/test_pit_fundamentals.py")
    move_file("app/quant/data/test_universe.py", "tests/unit/quant/test_universe.py")
    
    move_file("app/quant/hypotheses/registry.py", "app/domain/services/quant/hypotheses/registry.py")
    move_file("app/quant/hypotheses/run_all.py", "app/domain/services/quant/hypotheses/run_all.py")
    move_file("app/quant/hypotheses/test_base.py", "tests/unit/quant/test_base.py")
    move_file("app/quant/hypotheses/test_foreign_flow.py", "tests/unit/quant/test_foreign_flow.py")
    move_file("app/quant/hypotheses/test_insider.py", "tests/unit/quant/test_insider.py")
    move_file("app/quant/hypotheses/test_tet.py", "tests/unit/quant/test_tet.py")
    
    move_file("app/quant/research/factor_research.py", "app/domain/services/quant/research/factor_research.py")
    move_file("app/quant/research/test_factor_research.py", "tests/unit/quant/test_factor_research.py")
    
    move_file("app/quant/risk/evaluation.py", "app/domain/services/quant/risk/evaluation.py")
    move_file("app/quant/risk/live_safety.py", "app/domain/services/quant/risk/live_safety.py")
    move_file("app/quant/risk/portfolio.py", "app/domain/services/quant/risk/portfolio.py")
    move_file("app/quant/risk/risk_model.py", "app/domain/services/quant/risk/risk_model.py")
    move_file("app/quant/risk/test_evaluation.py", "tests/unit/quant/test_evaluation.py")
    move_file("app/quant/risk/test_live_safety.py", "tests/unit/quant/test_live_safety.py")
    move_file("app/quant/risk/test_portfolio.py", "tests/unit/quant/test_portfolio.py")
    move_file("app/quant/risk/test_risk_model.py", "tests/unit/quant/test_risk_model.py")
    
    move_file("app/quant/skills.py", "app/domain/services/quant/skills.py")
    move_dir("app/quant/skills_data", "app/domain/services/quant/skills_data")

    # 3. Clean up deprecated directories completely
    folders_to_delete = ["app/ports", "app/ml", "app/llm", "app/dataflows", "app/quant", "app/database"]
    for f in folders_to_delete:
        if os.path.exists(f):
            shutil.rmtree(f)
            print(f"Deleted deprecated folder: {f}")

    # 4. Import replacements mapping
    import_replacements = {
        r'app\.ports': 'app.application.ports',
        r'app\.ml': 'app.domain.services.ml',
        r'app\.llm': 'app.infrastructure.llm',
        r'app\.dataflows\.config': 'app.infrastructure.external_api.config',
        r'app\.dataflows\.default_config': 'app.infrastructure.external_api.default_config',
        r'app\.dataflows\.stockstats_utils': 'app.infrastructure.external_api.stockstats_utils',
        r'app\.dataflows\.reddit': 'app.infrastructure.external_api.social.reddit',
        r'app\.dataflows\.stocktwits': 'app.infrastructure.external_api.social.stocktwits',
        r'app\.dataflows\.y_finance': 'app.infrastructure.external_api.y_finance',
        r'app\.dataflows\.interface': 'app.application.ports.data_provider',
        r'app\.dataflows\.vendors': 'app.infrastructure.vendors',
        r'app\.quant\.skills_data': 'app.domain.services.quant.skills_data',
        r'app\.quant\.factors': 'app.domain.services.quant',
        r'app\.quant': 'app.domain.services.quant',
        r'app\.database': 'app.infrastructure.database',
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
                    
                    # Special skills_dir reference update
                    if "skills.py" in file:
                        new_content = new_content.replace(
                            'Path(__file__).resolve().parents[2] / "quant" / "skills_data"',
                            'Path(__file__).resolve().parents[3] / "domain" / "services" / "quant" / "skills_data"'
                        )
                    
                    if new_content != content:
                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(new_content)
                        print(f"Updated imports: {file_path}")
                        count_updated += 1

    print(f"=== PHASE 2 RESTRUCTURING DONE. Updated imports in {count_updated} files ===")

if __name__ == "__main__":
    main()
