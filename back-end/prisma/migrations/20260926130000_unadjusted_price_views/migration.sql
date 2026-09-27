CREATE OR REPLACE VIEW market_data_daily_calculation AS
WITH price_basis AS (
    SELECT md.*,
           CASE
               WHEN md.close_unadj > 0 THEN md.close_unadj / NULLIF(md.close_adj, 0)
               WHEN md.ticker IN ('VNINDEX', 'VN-INDEX', 'VN30', 'HNXINDEX', 'UPCOMINDEX') AND md.close_adj > 0 THEN 1.0
               ELSE NULL
           END AS raw_factor,
           CASE
               WHEN md.close_unadj > 0 THEN md.close_unadj
               WHEN md.ticker IN ('VNINDEX', 'VN-INDEX', 'VN30', 'HNXINDEX', 'UPCOMINDEX') AND md.close_adj > 0 THEN md.close_adj
               ELSE NULL
           END AS raw_close
    FROM market_data_daily md
)
SELECT ticker, date,
       open_adj * raw_factor AS open,
       high_adj * raw_factor AS high,
       low_adj * raw_factor AS low,
       raw_close AS close,
       vwap * raw_factor AS vwap,
       volume_continuous, volume_atc, volume_ato, volume_total,
       foreign_buy_vol, foreign_sell_vol, foreign_net_vol,
       is_etf_rebalance_day,
       AVG(volume_continuous) OVER (
           PARTITION BY ticker ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
       ) AS adtv20_continuous,
       market_cap * raw_factor AS market_cap,
       raw_factor AS raw_factor,
       data_source
FROM price_basis
WHERE raw_factor IS NOT NULL;

CREATE OR REPLACE VIEW ohlcv_unadjusted AS
SELECT md.date AS time, md.ticker AS symbol,
       md.open, md.high, md.low, md.close,
       md.volume_total AS volume
FROM market_data_daily_calculation md;
