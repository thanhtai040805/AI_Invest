import sys
import os

def test_imports():
    print("=== VERIFYING IMPORTS AFTER PHASE 2 RESTRUCTURE ===")
    
    # Add project root to path
    sys.path.append(os.path.abspath("."))

    # 1. Main entrypoint
    try:
        print("Importing app.main...")
        import app.main
        print(" Successfully imported app.main.")
    except Exception as e:
        print(f"FAILED importing app.main: {e}")
        import traceback
        traceback.print_exc()
        return False

    # 2. Data pipelines
    try:
        print("Importing daily_etl...")
        from app.infrastructure.data_pipelines.daily_etl import DailyETLPipeline
        print(" Successfully imported DailyETLPipeline.")
    except Exception as e:
        print(f"FAILED importing DailyETLPipeline: {e}")
        return False

    # 3. Market rules
    try:
        print("Importing hmm_classifier...")
        from app.domain.rules.market.hmm_classifier import hmm_classifier
        print(" Successfully imported hmm_classifier.")
    except Exception as e:
        print(f"FAILED importing hmm_classifier: {e}")
        return False


    # 5. VN Vendors (new infrastructure/vendors target)
    try:
        print("Importing VN vendors factor_scores...")
        from app.infrastructure.vendors.vn.factor_scores import refresh_all
        print(" Successfully imported VN vendors factor_scores.")
    except Exception as e:
        print(f"FAILED importing VN vendors factor_scores: {e}")
        return False

    # 6. Quant services (new domain/services/quant target)
    try:
        print("Importing quant vn_ic_tester...")
        from app.domain.services.quant.vn_ic_tester import VN_FACTORS
        print(" Successfully imported quant vn_ic_tester.")
    except Exception as e:
        print(f"FAILED importing quant vn_ic_tester: {e}")
        return False

    print("=== ALL CLEAN ARCHITECTURE IMPORTS VERIFIED SUCCESSFULLY ===")
    return True

if __name__ == "__main__":
    success = test_imports()
    sys.exit(0 if success else 1)
