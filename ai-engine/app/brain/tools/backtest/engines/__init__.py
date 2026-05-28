"""Backtest engines - Vietnam stock market only.

Engines:
  - BaseEngine: ABC for bar-by-bar execution with market rules
  - VietnamEquityEngine: VN market (T+2, HOSE/HNX/UPCoM, lot 100)

Inheritance:
  BaseEngine
  └── VietnamEquityEngine
"""
