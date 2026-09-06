"""
Graph Intelligence & Ecosystem Contagion Engine for HOSE.
Implements Directed Graph Shock Propagation, Leader-Follower Divergence Catch-up,
and Conglomerate Cluster Spillover Momentum.
Covering 100% of HOSE Listed Stocks (406 symbols) with ICB Granular Taxonomies.
"""

import numpy as np
import pandas as pd
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 1. GRANULAR SECTOR TAXONOMY COVERING ALL 406 HOSE LISTED STOCKS
SECTOR_MAP: Dict[str, str] = {
    # --- AGRICULTURE (16 mã) ---
    "AAM": "AGRICULTURE", "AAN": "AGRICULTURE", "ABT": "AGRICULTURE", "ACL": "AGRICULTURE", "ANV": "AGRICULTURE", "ASM": "AGRICULTURE", "CMX": "AGRICULTURE", "DAT": "AGRICULTURE",
    "FMC": "AGRICULTURE", "HAG": "AGRICULTURE", "HPA": "AGRICULTURE", "HSL": "AGRICULTURE", "IDI": "AGRICULTURE", "NSC": "AGRICULTURE", "SSC": "AGRICULTURE", "VHC": "AGRICULTURE",

    # --- BANKING (23 mã) ---
    "ACB": "BANKING", "BID": "BANKING", "BVB": "BANKING", "CTG": "BANKING", "EIB": "BANKING", "EVF": "BANKING", "HDB": "BANKING", "KLB": "BANKING",
    "LPB": "BANKING", "MBB": "BANKING", "MSB": "BANKING", "NAB": "BANKING", "OCB": "BANKING", "SHB": "BANKING", "SSB": "BANKING", "STB": "BANKING",
    "TCB": "BANKING", "TPB": "BANKING", "VAB": "BANKING", "VBB": "BANKING", "VCB": "BANKING", "VIB": "BANKING", "VPB": "BANKING",

    # --- CHEMICALS (25 mã) ---
    "AAA": "CHEMICALS", "ABS": "CHEMICALS", "ADP": "CHEMICALS", "APH": "CHEMICALS", "BFC": "CHEMICALS", "BMP": "CHEMICALS", "CSV": "CHEMICALS", "DCM": "CHEMICALS",
    "DGC": "CHEMICALS", "DPM": "CHEMICALS", "DPR": "CHEMICALS", "DTT": "CHEMICALS", "GVR": "CHEMICALS", "HCD": "CHEMICALS", "HII": "CHEMICALS", "HRC": "CHEMICALS",
    "LIX": "CHEMICALS", "PHR": "CHEMICALS", "PLP": "CHEMICALS", "SFG": "CHEMICALS", "TDP": "CHEMICALS", "TNC": "CHEMICALS", "TPC": "CHEMICALS", "TRC": "CHEMICALS",
    "VPS": "CHEMICALS",

    # --- CONSTRUCTION (44 mã) ---
    "ACC": "CONSTRUCTION", "C47": "CONSTRUCTION", "CCC": "CONSTRUCTION", "CII": "CONSTRUCTION", "CRC": "CONSTRUCTION", "CTD": "CONSTRUCTION", "CTI": "CONSTRUCTION", "CVT": "CONSTRUCTION",
    "DC4": "CONSTRUCTION", "DPG": "CONSTRUCTION", "DTL": "CONSTRUCTION", "DXV": "CONSTRUCTION", "FCM": "CONSTRUCTION", "FCN": "CONSTRUCTION", "GMH": "CONSTRUCTION", "HAS": "CONSTRUCTION",
    "HHV": "CONSTRUCTION", "HT1": "CONSTRUCTION", "HTI": "CONSTRUCTION", "HTN": "CONSTRUCTION", "HUB": "CONSTRUCTION", "HVH": "CONSTRUCTION", "L10": "CONSTRUCTION", "LBM": "CONSTRUCTION",
    "LCG": "CONSTRUCTION", "LGC": "CONSTRUCTION", "LM8": "CONSTRUCTION", "PC1": "CONSTRUCTION", "PHC": "CONSTRUCTION", "PTC": "CONSTRUCTION", "REE": "CONSTRUCTION", "SC5": "CONSTRUCTION",
    "SRF": "CONSTRUCTION", "TCR": "CONSTRUCTION", "TEG": "CONSTRUCTION", "THG": "CONSTRUCTION", "TSA": "CONSTRUCTION", "TV2": "CONSTRUCTION", "VCA": "CONSTRUCTION", "VCG": "CONSTRUCTION",
    "VGC": "CONSTRUCTION", "VNE": "CONSTRUCTION", "VSI": "CONSTRUCTION", "YBM": "CONSTRUCTION",

    # --- CONSUMER (18 mã) ---
    "AFX": "CONSUMER", "ANT": "CONSUMER", "BAF": "CONSUMER", "BHN": "CONSUMER", "DBC": "CONSUMER", "KDC": "CONSUMER", "LAF": "CONSUMER", "LSS": "CONSUMER",
    "MCH": "CONSUMER", "MCM": "CONSUMER", "MSN": "CONSUMER", "NAF": "CONSUMER", "PAN": "CONSUMER", "SAB": "CONSUMER", "SBT": "CONSUMER", "SMB": "CONSUMER",
    "VCF": "CONSUMER", "VNM": "CONSUMER",

    # --- ENERGY (35 mã) ---
    "ASP": "ENERGY", "BSR": "ENERGY", "BTP": "ENERGY", "BWE": "ENERGY", "CHP": "ENERGY", "CLW": "ENERGY", "CNG": "ENERGY", "DRL": "ENERGY",
    "GAS": "ENERGY", "GEG": "ENERGY", "GHC": "ENERGY", "HID": "ENERGY", "HNA": "ENERGY", "KHP": "ENERGY", "NT2": "ENERGY", "PGC": "ENERGY",
    "PGD": "ENERGY", "PGV": "ENERGY", "POW": "ENERGY", "PPC": "ENERGY", "PVD": "ENERGY", "S4A": "ENERGY", "SBA": "ENERGY", "SHP": "ENERGY",
    "SIP": "ENERGY", "SJD": "ENERGY", "TBC": "ENERGY", "TDM": "ENERGY", "TDW": "ENERGY", "TMP": "ENERGY", "TTA": "ENERGY", "TTE": "ENERGY",
    "UIC": "ENERGY", "VPD": "ENERGY", "VSH": "ENERGY",

    # --- HEALTHCARE (12 mã) ---
    "DBD": "HEALTHCARE", "DBT": "HEALTHCARE", "DCL": "HEALTHCARE", "DHG": "HEALTHCARE", "DMC": "HEALTHCARE", "FIT": "HEALTHCARE", "IMP": "HEALTHCARE", "OPC": "HEALTHCARE",
    "SPM": "HEALTHCARE", "TNH": "HEALTHCARE", "TRA": "HEALTHCARE", "VDP": "HEALTHCARE",

    # --- INDUSTRIAL_GOODS (44 mã) ---
    "AAT": "INDUSTRIAL_GOODS", "ACG": "INDUSTRIAL_GOODS", "ADS": "INDUSTRIAL_GOODS", "BKG": "INDUSTRIAL_GOODS", "DHC": "INDUSTRIAL_GOODS", "DLG": "INDUSTRIAL_GOODS", "DQC": "INDUSTRIAL_GOODS", "EVE": "INDUSTRIAL_GOODS",
    "GDT": "INDUSTRIAL_GOODS", "GEE": "INDUSTRIAL_GOODS", "GEX": "INDUSTRIAL_GOODS", "GIL": "INDUSTRIAL_GOODS", "GTA": "INDUSTRIAL_GOODS", "HAP": "INDUSTRIAL_GOODS", "HHP": "INDUSTRIAL_GOODS", "HTG": "INDUSTRIAL_GOODS",
    "KMR": "INDUSTRIAL_GOODS", "MCP": "INDUSTRIAL_GOODS", "MSH": "INDUSTRIAL_GOODS", "MZG": "INDUSTRIAL_GOODS", "NAV": "INDUSTRIAL_GOODS", "NHH": "INDUSTRIAL_GOODS", "NHT": "INDUSTRIAL_GOODS", "PAC": "INDUSTRIAL_GOODS",
    "PTB": "INDUSTRIAL_GOODS", "RAL": "INDUSTRIAL_GOODS", "RYG": "INDUSTRIAL_GOODS", "SAM": "INDUSTRIAL_GOODS", "SAV": "INDUSTRIAL_GOODS", "SBG": "INDUSTRIAL_GOODS", "SBV": "INDUSTRIAL_GOODS", "SHA": "INDUSTRIAL_GOODS",
    "SHI": "INDUSTRIAL_GOODS", "STK": "INDUSTRIAL_GOODS", "SVD": "INDUSTRIAL_GOODS", "SVT": "INDUSTRIAL_GOODS", "TCM": "INDUSTRIAL_GOODS", "TLD": "INDUSTRIAL_GOODS", "TLG": "INDUSTRIAL_GOODS", "TMT": "INDUSTRIAL_GOODS",
    "TTF": "INDUSTRIAL_GOODS", "TVT": "INDUSTRIAL_GOODS", "TYA": "INDUSTRIAL_GOODS", "VTB": "INDUSTRIAL_GOODS",

    # --- LOGISTICS (35 mã) ---
    "ASG": "LOGISTICS", "AST": "LOGISTICS", "CLL": "LOGISTICS", "DVP": "LOGISTICS", "GMD": "LOGISTICS", "GSP": "LOGISTICS", "HAH": "LOGISTICS", "HTV": "LOGISTICS",
    "HVN": "LOGISTICS", "ILB": "LOGISTICS", "MHC": "LOGISTICS", "NCT": "LOGISTICS", "PDN": "LOGISTICS", "PDV": "LOGISTICS", "PJT": "LOGISTICS", "PVP": "LOGISTICS",
    "PVT": "LOGISTICS", "QNP": "LOGISTICS", "SCS": "LOGISTICS", "SFI": "LOGISTICS", "SGN": "LOGISTICS", "SKG": "LOGISTICS", "STG": "LOGISTICS", "TCL": "LOGISTICS",
    "TCO": "LOGISTICS", "TCT": "LOGISTICS", "TMS": "LOGISTICS", "VIP": "LOGISTICS", "VJC": "LOGISTICS", "VNL": "LOGISTICS", "VNS": "LOGISTICS", "VOS": "LOGISTICS",
    "VSC": "LOGISTICS", "VTO": "LOGISTICS", "VTP": "LOGISTICS",

    # --- OTHER_INDUSTRIALS (7 mã) ---
    "ADG": "OTHER_INDUSTRIALS", "CLC": "OTHER_INDUSTRIALS", "DAH": "OTHER_INDUSTRIALS", "DSN": "OTHER_INDUSTRIALS", "NVT": "OTHER_INDUSTRIALS", "VNG": "OTHER_INDUSTRIALS", "VPL": "OTHER_INDUSTRIALS",

    # --- REAL_ESTATE (60 mã) ---
    "AGG": "REAL_ESTATE", "BCE": "REAL_ESTATE", "BCM": "REAL_ESTATE", "CCL": "REAL_ESTATE", "CDC": "REAL_ESTATE", "CIG": "REAL_ESTATE", "CKG": "REAL_ESTATE", "CRE": "REAL_ESTATE",
    "CRV": "REAL_ESTATE", "D2D": "REAL_ESTATE", "DIG": "REAL_ESTATE", "DRH": "REAL_ESTATE", "DTA": "REAL_ESTATE", "DXG": "REAL_ESTATE", "DXS": "REAL_ESTATE", "EVG": "REAL_ESTATE",
    "FDC": "REAL_ESTATE", "FIR": "REAL_ESTATE", "HAR": "REAL_ESTATE", "HDC": "REAL_ESTATE", "HDG": "REAL_ESTATE", "HPX": "REAL_ESTATE", "HQC": "REAL_ESTATE", "HU1": "REAL_ESTATE",
    "IJC": "REAL_ESTATE", "ITC": "REAL_ESTATE", "KBC": "REAL_ESTATE", "KDH": "REAL_ESTATE", "KHG": "REAL_ESTATE", "KOS": "REAL_ESTATE", "LDG": "REAL_ESTATE", "LGL": "REAL_ESTATE",
    "LHG": "REAL_ESTATE", "NBB": "REAL_ESTATE", "NHA": "REAL_ESTATE", "NLG": "REAL_ESTATE", "NTC": "REAL_ESTATE", "NTL": "REAL_ESTATE", "NVL": "REAL_ESTATE", "PDR": "REAL_ESTATE",
    "PTL": "REAL_ESTATE", "QCG": "REAL_ESTATE", "SCR": "REAL_ESTATE", "SGR": "REAL_ESTATE", "SJS": "REAL_ESTATE", "SZC": "REAL_ESTATE", "SZL": "REAL_ESTATE", "TAL": "REAL_ESTATE",
    "TCH": "REAL_ESTATE", "TDC": "REAL_ESTATE", "TDH": "REAL_ESTATE", "TIP": "REAL_ESTATE", "TIX": "REAL_ESTATE", "TN1": "REAL_ESTATE", "VHM": "REAL_ESTATE", "VIC": "REAL_ESTATE",
    "VPH": "REAL_ESTATE", "VPI": "REAL_ESTATE", "VRC": "REAL_ESTATE", "VRE": "REAL_ESTATE",

    # --- RETAIL (34 mã) ---
    "BTT": "RETAIL", "CCI": "RETAIL", "CMV": "RETAIL", "COM": "RETAIL", "CTF": "RETAIL", "DGW": "RETAIL", "DMX": "RETAIL", "FRT": "RETAIL",
    "GEL": "RETAIL", "HAX": "RETAIL", "HHS": "RETAIL", "HMC": "RETAIL", "HTL": "RETAIL", "JVC": "RETAIL", "MWG": "RETAIL", "NO1": "RETAIL",
    "PET": "RETAIL", "PIT": "RETAIL", "PLX": "RETAIL", "PMG": "RETAIL", "PNC": "RETAIL", "PNJ": "RETAIL", "SFC": "RETAIL", "SMA": "RETAIL",
    "ST8": "RETAIL", "SVC": "RETAIL", "TDG": "RETAIL", "TNI": "RETAIL", "TSC": "RETAIL", "VFG": "RETAIL", "VID": "RETAIL", "VMD": "RETAIL",
    "VPG": "RETAIL", "VVS": "RETAIL",

    # --- SECURITIES (27 mã) ---
    "AGR": "SECURITIES", "APG": "SECURITIES", "BIC": "SECURITIES", "BMI": "SECURITIES", "BSI": "SECURITIES", "BVH": "SECURITIES", "CTS": "SECURITIES", "DSC": "SECURITIES",
    "DSE": "SECURITIES", "FTS": "SECURITIES", "HCM": "SECURITIES", "LPS": "SECURITIES", "MIG": "SECURITIES", "OGC": "SECURITIES", "ORS": "SECURITIES", "PGI": "SECURITIES",
    "SSI": "SECURITIES", "TCI": "SECURITIES", "TCX": "SECURITIES", "TVB": "SECURITIES", "TVS": "SECURITIES", "VCI": "SECURITIES", "VCK": "SECURITIES", "VDS": "SECURITIES",
    "VIX": "SECURITIES", "VND": "SECURITIES", "VPX": "SECURITIES",

    # --- STEEL (17 mã) ---
    "BMC": "STEEL", "BRC": "STEEL", "C32": "STEEL", "CSM": "STEEL", "DHA": "STEEL", "DHM": "STEEL", "DRC": "STEEL", "HPG": "STEEL",
    "HSG": "STEEL", "KSB": "STEEL", "MDG": "STEEL", "NKG": "STEEL", "NNC": "STEEL", "SMC": "STEEL", "SRC": "STEEL", "TLH": "STEEL",
    "TNT": "STEEL",

    # --- TECH (9 mã) ---
    "ABR": "TECH", "CMG": "TECH", "CTR": "TECH", "ELC": "TECH", "FPT": "TECH", "ICT": "TECH", "ITD": "TECH", "SGT": "TECH",
    "YEG": "TECH",

}

# 2. CONGLOMERATE / SYNDICATE ECOSYSTEM GROUPS (Đặc thù thị trường VN)
ECOSYSTEM_MAP: Dict[str, str] = {
    # VinGroup Ecosystem
    "VIC": "VIN_GROUP", "VHM": "VIN_GROUP", "VRE": "VIN_GROUP", "VPL": "VIN_GROUP",
    
    # Gelex Ecosystem
    "GEX": "GELEX_GROUP", "VIX": "GELEX_GROUP", "VGC": "GELEX_GROUP", "GEE": "GELEX_GROUP",
    
    # DGC Ecosystem
    "DGC": "DGC_GROUP", "CSV": "DGC_GROUP", "PAT": "DGC_GROUP",
    
    # Eximbank / Novaland Ecosystem
    "EIB": "EXIM_ECOSYSTEM", "NVL": "NOVALAND_ECOSYSTEM",
    
    # Hoang Huy Group
    "TCH": "HOANG_HUY", "HHS": "HOANG_HUY", "CRV": "HOANG_HUY",
    
    # CII Infrastructure
    "CII": "CII_GROUP", "NBB": "CII_GROUP",

    # Bamboo Capital
    "BCG": "BAMBOO_CAPITAL", "TCD": "BAMBOO_CAPITAL",

    # Becamex Group
    "BCM": "BECAMEX_GROUP", "IJC": "BECAMEX_GROUP", "TDC": "BECAMEX_GROUP",

    # Dat Xanh Group
    "DXG": "DAT_XANH_GROUP", "DXS": "DAT_XANH_GROUP",

    # Masan Group
    "MSN": "MASAN_GROUP", "MCH": "MASAN_GROUP", "MCM": "MASAN_GROUP",
}

# 3. DESIGNATED HUB / LEADER NODES FOR ALL 15 SECTORS
SECTOR_LEADERS: Dict[str, str] = {
    "BANKING": "VCB",
    "SECURITIES": "SSI",
    "REAL_ESTATE": "DIG",
    "STEEL": "HPG",
    "ENERGY": "PVD",
    "RETAIL": "MWG",
    "CONSUMER": "VNM",
    "TECH": "FPT",
    "CHEMICALS": "DGC",
    "LOGISTICS": "GMD",
    "CONSTRUCTION": "VCG",
    "AGRICULTURE": "VHC",
    "HEALTHCARE": "DHG",
    "INDUSTRIAL_GOODS": "GEX",
    "OTHER_INDUSTRIALS": "DSN",
}

ECOSYSTEM_LEADERS: Dict[str, str] = {
    "VIN_GROUP": "VIC",
    "GELEX_GROUP": "GEX",
    "DGC_GROUP": "DGC",
    "HOANG_HUY": "TCH",
    "CII_GROUP": "CII",
    "BAMBOO_CAPITAL": "BCG",
    "BECAMEX_GROUP": "BCM",
    "DAT_XANH_GROUP": "DXG",
    "MASAN_GROUP": "MSN",
}


class GraphContagionEngine:
    """
    Graph Contagion & Lead-Lag Engine (EXP-011):
    Constructs high-dimensional graph propagation features across sectors and conglomerates.
    """
    
    def __init__(self, dynamic_hydrate: bool = True):
        self.sector_map = dict(SECTOR_MAP)
        self.ecosystem_map = dict(ECOSYSTEM_MAP)
        self.sector_leaders = dict(SECTOR_LEADERS)
        self.ecosystem_leaders = dict(ECOSYSTEM_LEADERS)
        if dynamic_hydrate:
            self._hydrate_from_db()

    def _hydrate_from_db(self):
        """Tự động nạp bổ sung ngành từ PostgreSQL stocks table cho các mã mới niêm yết."""
        try:
            from app.infrastructure.database.pg_pool import get_conn
            from app.infrastructure.vendors.vn.sector_groups import classify
            ICB_TO_SECTOR = {
                'BANKS': 'BANKING',
                'FINANCIAL_SERVICES': 'SECURITIES',
                'REAL_ESTATE': 'REAL_ESTATE',
                'BASIC_RESOURCES': 'STEEL',
                'OIL_GAS': 'ENERGY',
                'RETAIL_TRADE': 'RETAIL',
                'FOOD_BEVERAGE': 'CONSUMER',
                'TECHNOLOGY': 'TECH',
                'CHEMICALS': 'CHEMICALS',
                'CONSTRUCTION': 'CONSTRUCTION',
                'CONSTRUCTION_MATERIALS': 'CONSTRUCTION',
                'TRANSPORTATION': 'LOGISTICS',
                'UTILITIES': 'ENERGY',
                'AGRICULTURE': 'AGRICULTURE',
                'HEALTHCARE': 'HEALTHCARE',
                'INDUSTRIAL_GOODS': 'INDUSTRIAL_GOODS',
                'OTHER_INDUSTRIALS': 'OTHER_INDUSTRIALS'
            }
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT symbol, industry FROM stocks WHERE exchange ILIKE 'HOSE' OR exchange ILIKE 'hose';")
                    for sym, ind in cur.fetchall():
                        if sym not in self.sector_map or self.sector_map[sym] == "OTHER":
                            icb = classify(ind, sym)
                            self.sector_map[sym] = ICB_TO_SECTOR.get(icb, "OTHER_INDUSTRIALS")
        except Exception as e:
            logger.debug(f"Dynamic sector hydration skipped: {e}")

    def extract_graph_contagion_signals(self, market_data_dict: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
        """
        Extracts 8 advanced graph propagation features for each ticker.
        Strictly applies shift(1) or shift(2) on all cross-sectional graph shocks to eliminate lookahead bias.
        """
        logger.info("Computing Graph Contagion & Lead-Lag Alpha features...")
        
        # 1. Build unified Returns and Volume Turnover DataFrames
        returns_dict = {}
        volumes_dict = {}
        turnover_dict = {}
        
        for ticker, df in market_data_dict.items():
            if 'close' in df.columns:
                returns_dict[ticker] = df['close'].pct_change()
                if 'volume' in df.columns:
                    volumes_dict[ticker] = df['volume']
                    # Relative volume ratio (vs 20d MA)
                    vol_ma20 = df['volume'].rolling(20, min_periods=5).mean() + 1e-8
                    turnover_dict[ticker] = df['volume'] / vol_ma20
                    
        df_returns = pd.DataFrame(returns_dict)
        df_vol_ratio = pd.DataFrame(turnover_dict)
        
        # 2. Compute Sector Aggregates (Mean Return & Volume Surge Breadth)
        sector_returns: Dict[str, List[pd.Series]] = {}
        sector_surges: Dict[str, List[pd.Series]] = {}
        
        for ticker, sector in self.sector_map.items():
            if ticker in df_returns.columns:
                if sector not in sector_returns:
                    sector_returns[sector] = []
                    sector_surges[sector] = []
                sector_returns[sector].append(df_returns[ticker])
                # Surge indicator = 1 if volume > 1.5x MA20 and return > 0
                if ticker in df_vol_ratio.columns:
                    is_surge = ((df_vol_ratio[ticker] >= 1.5) & (df_returns[ticker] > 0)).astype(float)
                    sector_surges[sector].append(is_surge)
                    
        sector_mean_df = pd.DataFrame()
        sector_surge_breadth_df = pd.DataFrame()
        
        for sector, s_list in sector_returns.items():
            if s_list:
                sector_mean_df[sector] = pd.concat(s_list, axis=1).mean(axis=1)
        for sector, surge_list in sector_surges.items():
            if surge_list:
                sector_surge_breadth_df[sector] = pd.concat(surge_list, axis=1).mean(axis=1)
                
        # 3. Compute Ecosystem Aggregates
        eco_returns: Dict[str, List[pd.Series]] = {}
        for ticker, eco in self.ecosystem_map.items():
            if ticker in df_returns.columns:
                if eco not in eco_returns:
                    eco_returns[eco] = []
                eco_returns[eco].append(df_returns[ticker])
                
        eco_mean_df = pd.DataFrame()
        for eco, e_list in eco_returns.items():
            if e_list:
                eco_mean_df[eco] = pd.concat(e_list, axis=1).mean(axis=1)

        # 4. Generate Graph Features Per Ticker
        graph_features: Dict[str, pd.DataFrame] = {}
        
        for ticker, df in market_data_dict.items():
            if ticker not in df_returns.columns:
                continue
                
            ret = df_returns[ticker]
            features = pd.DataFrame(index=df.index)
            sector = self.sector_map.get(ticker)
            if not sector:
                try:
                    from app.infrastructure.vendors.vn.sector_groups import classify
                    sector = classify(None, ticker)
                    self.sector_map[ticker] = sector
                except Exception:
                    sector = "OTHER_INDUSTRIALS"

            eco = self.ecosystem_map.get(ticker, "NONE")
            
            # --- FEATURE 1: Sector Relative Strength (5d & 20d) ---
            if sector in sector_mean_df.columns:
                sec_ret = sector_mean_df[sector]
                stock_cum_5d = (1 + ret).rolling(5).apply(np.prod, raw=True) - 1
                sec_cum_5d = (1 + sec_ret).rolling(5).apply(np.prod, raw=True) - 1
                features['sector_rs_5d'] = (stock_cum_5d - sec_cum_5d).fillna(0.0)
                
                stock_cum_20d = (1 + ret).rolling(20).apply(np.prod, raw=True) - 1
                sec_cum_20d = (1 + sec_ret).rolling(20).apply(np.prod, raw=True) - 1
                features['sector_rs_20d'] = (stock_cum_20d - sec_cum_20d).fillna(0.0)
            else:
                features['sector_rs_5d'] = 0.0
                features['sector_rs_20d'] = 0.0
                
            # --- FEATURE 2: Directed Sector Leader Shock Propagation (Lag 1 & Lag 2) ---
            sec_leader = self.sector_leaders.get(sector)
            if sec_leader and sec_leader in df_returns.columns and sec_leader != ticker:
                leader_ret = df_returns[sec_leader]
                leader_vr = df_vol_ratio[sec_leader] if sec_leader in df_vol_ratio.columns else 1.0
                leader_shock = leader_ret * np.log1p(np.maximum(0, leader_vr))
                features['sec_hub_shock_1d'] = leader_shock.shift(1).fillna(0.0)
                features['sec_hub_shock_2d'] = leader_shock.shift(2).fillna(0.0)
            else:
                features['sec_hub_shock_1d'] = 0.0
                features['sec_hub_shock_2d'] = 0.0
                
            # --- FEATURE 3: Leader-Follower Divergence Catch-up Potential (3d Catch-up) ---
            if sector in sector_mean_df.columns:
                sec_cum_3d = (1 + sector_mean_df[sector]).rolling(3).apply(np.prod, raw=True) - 1
                stock_cum_3d = (1 + ret).rolling(3).apply(np.prod, raw=True) - 1
                divergence = (sec_cum_3d - stock_cum_3d).shift(1)
                features['sector_divergence_catchup_3d'] = divergence.fillna(0.0)
            else:
                features['sector_divergence_catchup_3d'] = 0.0

            # --- FEATURE 4: Cluster Volume Surge Breadth (Lag 1) ---
            if sector in sector_surge_breadth_df.columns:
                features['cluster_volume_breadth_1d'] = sector_surge_breadth_df[sector].shift(1).fillna(0.0)
            else:
                features['cluster_volume_breadth_1d'] = 0.0

            # --- FEATURE 5: Conglomerate Ecosystem Spillover Impulse ---
            eco_leader = self.ecosystem_leaders.get(eco)
            if eco_leader and eco_leader in df_returns.columns and eco_leader != ticker:
                eco_lead_ret = df_returns[eco_leader]
                eco_lead_vr = df_vol_ratio[eco_leader] if eco_leader in df_vol_ratio.columns else 1.0
                eco_shock = eco_lead_ret * np.log1p(np.maximum(0, eco_lead_vr))
                features['ecosystem_hub_shock_1d'] = eco_shock.shift(1).fillna(0.0)
                
                eco_cum_3d = (1 + eco_mean_df[eco]).rolling(3).apply(np.prod, raw=True) - 1
                stock_cum_3d = (1 + ret).rolling(3).apply(np.prod, raw=True) - 1
                features['ecosystem_divergence_catchup'] = (eco_cum_3d - stock_cum_3d).shift(1).fillna(0.0)
            else:
                features['ecosystem_hub_shock_1d'] = 0.0
                features['ecosystem_divergence_catchup'] = 0.0

            # Fill any remaining NaNs
            features = features.replace([np.inf, -np.inf], 0.0).fillna(0.0)
            graph_features[ticker] = features

        return graph_features


graph_engine = GraphContagionEngine()
