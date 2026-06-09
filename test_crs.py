from app.brain.risk.composite_scorer import VNCompositeRiskScorer
scorer = VNCompositeRiskScorer()
ls = {
    'layer1_quant': {'risk_score': 0.3, 'flags': ['CVAR_MEDIUM'], 'detail': {}},
    'layer2_fundamental': {'risk_score': 0.2, 'flags': [], 'detail': {}},
    'layer3_market_vn': {'risk_score': 0.1, 'flags': [], 'detail': {}},
    'layer4_macro_vn': {'risk_score': 0.4, 'flags': ['RATE_RISING'], 'detail': {}},
    'layer5_global': {'risk_score': 0.2, 'flags': [], 'detail': {}},
    'layer6_regulatory': {'risk_score': 0.15, 'flags': [], 'detail': {}},
    'layer7_behavioral': {'risk_score': 0.1, 'flags': [], 'detail': {}},
}
r = scorer.compute('ACB', 'BANKS', ls)
print(f'Banking CRS={r["crs_score"]} rec={r["recommendation"]}')
print(f'Weights: {scorer._get_weights("BANKS")}')
ls2 = dict(ls)
ls2['layer6_regulatory'] = {'risk_score': 1.0, 'flags': ['CRITICAL_REGULATORY_ACTION'], 'detail': {}}
r2 = scorer.compute('VIC', 'REAL_ESTATE', ls2)
print(f'Hard block CRS={r2["crs_score"]} rec={r2["recommendation"]}')
ls3 = {k: {'risk_score': 0.05, 'flags': [], 'detail': {}} for k in ls}
r3 = scorer.compute('FPT', 'TECHNOLOGY', ls3)
print(f'Low risk CRS={r3["crs_score"]} rec={r3["recommendation"]}')
print('All tests passed')
