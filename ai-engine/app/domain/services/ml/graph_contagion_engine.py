"""
Graph Intelligence & Ecosystem Contagion Engine for HOSE.
Implements Directed Graph Shock Propagation, Leader-Follower Divergence Catch-up,
and Conglomerate Cluster Spillover Momentum.
"""

import numpy as np
import pandas as pd
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 1. GRANULAR SECTOR TAXONOMY FOR HOSE TOP UNIVERSE
SECTOR_MAP: Dict[str, str] = {
    # Banking
    "VCB": "BANKING", "BID": "BANKING", "CTG": "BANKING", "TCB": "BANKING",
    "MBB": "BANKING", "VPB": "BANKING", "ACB": "BANKING", "STB": "BANKING",
    "HDB": "BANKING", "TPB": "BANKING", "VIB": "BANKING", "MSB": "BANKING",
    "OCB": "BANKING", "LPB": "BANKING", "EIB": "BANKING", "SHB": "BANKING",
    "SSB": "BANKING",
    
    # Securities / Brokerage
    "SSI": "SECURITIES", "VND": "SECURITIES", "VCI": "SECURITIES", "HCM": "SECURITIES",
    "VIX": "SECURITIES", "FTS": "SECURITIES", "BSI": "SECURITIES", "CTS": "SECURITIES",
    "AGR": "SECURITIES", "ORS": "SECURITIES", "VDS": "SECURITIES", "APG": "SECURITIES",
    
    # Real Estate (Residential & Commercial)
    "VHM": "REAL_ESTATE", "NVL": "REAL_ESTATE", "PDR": "REAL_ESTATE", "DIG": "REAL_ESTATE",
    "DXG": "REAL_ESTATE", "KDH": "REAL_ESTATE", "NLG": "REAL_ESTATE", "KBC": "REAL_ESTATE",
    "HDC": "REAL_ESTATE", "TCH": "REAL_ESTATE", "SCR": "REAL_ESTATE", "VIC": "REAL_ESTATE",
    "VRE": "REAL_ESTATE", "NBB": "REAL_ESTATE", "DXS": "REAL_ESTATE", "KHG": "REAL_ESTATE",
    "HQC": "REAL_ESTATE", "IJC": "REAL_ESTATE", "LDG": "REAL_ESTATE", "AGG": "REAL_ESTATE",
    "CEO": "REAL_ESTATE", "PDR": "REAL_ESTATE",
    
    # Steel & Materials
    "HPG": "STEEL", "HSG": "STEEL", "NKG": "STEEL", "TLH": "STEEL",
    
    # Oil & Gas / Energy
    "GAS": "ENERGY", "PLX": "ENERGY", "PVD": "ENERGY", "PVT": "ENERGY",
    "POW": "ENERGY", "NT2": "ENERGY", "GEG": "ENERGY", "PC1": "ENERGY",
    "REE": "ENERGY", "HDG": "ENERGY", "PVS": "ENERGY",
    
    # Retail & Consumer Goods
    "MWG": "RETAIL", "FRT": "RETAIL", "PNJ": "RETAIL", "DGW": "RETAIL",
    "MSN": "CONSUMER", "VNM": "CONSUMER", "SAB": "CONSUMER", "KDC": "CONSUMER",
    "SBT": "CONSUMER", "BAF": "CONSUMER", "DBC": "CONSUMER",
    
    # Technology & Telecom
    "FPT": "TECH", "CTR": "TECH", "CMG": "TECH", "ELC": "TECH", "VTP": "TECH",
    
    # Chemicals, Fertilizer & Rubber
    "DGC": "CHEMICALS", "DCM": "CHEMICALS", "DPM": "CHEMICALS", "CSV": "CHEMICALS",
    "BFC": "CHEMICALS", "GVR": "RUBBER", "PHR": "RUBBER", "DPR": "RUBBER",
    
    # Industrial Parks & Logistics
    "SZC": "INDUSTRIAL_PARK", "BCM": "INDUSTRIAL_PARK", "SIP": "INDUSTRIAL_PARK",
    "GMD": "LOGISTICS", "HAH": "LOGISTICS", "VSC": "LOGISTICS", "VOS": "LOGISTICS",
    
    # Construction & Infrastructure
    "VCG": "CONSTRUCTION", "CTD": "CONSTRUCTION", "HHV": "CONSTRUCTION",
    "CII": "CONSTRUCTION", "LCG": "CONSTRUCTION", "FCN": "CONSTRUCTION",
    "KSB": "CONSTRUCTION", "HT1": "CONSTRUCTION", "BCC": "CONSTRUCTION"
}

# 2. CONGLOMERATE / SYNDICATE ECOSYSTEM GROUPS (Đặc thù thị trường VN)
ECOSYSTEM_MAP: Dict[str, str] = {
    # VinGroup Ecosystem
    "VIC": "VIN_GROUP", "VHM": "VIN_GROUP", "VRE": "VIN_GROUP", "VPL": "VIN_GROUP",
    
    # Gelex Ecosystem
    "GEX": "GELEX_GROUP", "VIX": "GELEX_GROUP", "VGC": "GELEX_GROUP", "GEE": "GELEX_GROUP",
    
    # DGC Ecosystem
    "DGC": "DGC_GROUP", "CSV": "DGC_GROUP", "PAT": "DGC_GROUP",
    
    # Tuấn Mượt / EIB Ecosystem
    "EIB": "EXIM_ECOSYSTEM", "NVL": "NOVALAND_ECOSYSTEM",
    
    # Hoang Huy Group
    "TCH": "HOANG_HUY", "HHS": "HOANG_HUY",
    
    # CII Infrastructure
    "CII": "CII_GROUP", "NBB": "CII_GROUP"
}

# 3. DESIGNATED HUB / LEADER NODES
SECTOR_LEADERS: Dict[str, str] = {
    "BANKING": "VCB",
    "SECURITIES": "SSI",
    "STEEL": "HPG",
    "REAL_ESTATE": "DIG",
    "ENERGY": "PVD",
    "RETAIL": "MWG",
    "TECH": "FPT",
    "CHEMICALS": "DGC",
    "LOGISTICS": "GMD",
    "CONSTRUCTION": "VCG"
}

ECOSYSTEM_LEADERS: Dict[str, str] = {
    "VIN_GROUP": "VIC",
    "GELEX_GROUP": "GEX",
    "DGC_GROUP": "DGC",
    "HOANG_HUY": "TCH",
    "CII_GROUP": "CII"
}


class GraphContagionEngine:
    """
    Graph Contagion & Lead-Lag Engine (EXP-011):
    Constructs high-dimensional graph propagation features across sectors and conglomerates.
    """
    
    def __init__(self):
        self.sector_map = SECTOR_MAP
        self.ecosystem_map = ECOSYSTEM_MAP
        self.sector_leaders = SECTOR_LEADERS
        self.ecosystem_leaders = ECOSYSTEM_LEADERS

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
            sector = self.sector_map.get(ticker, "OTHER")
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
            # If Sector or Ecosystem has gained +5% in 3d, but this stock is lagging behind (consolidating),
            # this represents a coiled spring breakout opportunity.
            if sector in sector_mean_df.columns:
                sec_cum_3d = (1 + sector_mean_df[sector]).rolling(3).apply(np.prod, raw=True) - 1
                stock_cum_3d = (1 + ret).rolling(3).apply(np.prod, raw=True) - 1
                # Lag by 1 to make it a pure predictive signal
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
                
                # Ecosystem divergence
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
