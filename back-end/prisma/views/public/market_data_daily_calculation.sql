WITH price_basis AS (
  SELECT
    md.ticker,
    md.date,
    md.open_adj,
    md.high_adj,
    md.low_adj,
    md.close_adj,
    md.close_unadj,
    md.vwap,
    md.volume_continuous,
    md.volume_atc,
    md.volume_ato,
    md.volume_total,
    md.foreign_buy_vol,
    md.foreign_sell_vol,
    md.foreign_net_vol,
    md.is_etf_rebalance_day,
    md.adtv20_continuous,
    md.market_cap,
    md.adj_factor,
    md.data_source,
    md.created_at,
    CASE
      WHEN (md.close_unadj > (0) :: double precision) THEN (
        md.close_unadj / NULLIF(md.close_adj, (0) :: double precision)
      )
      WHEN (
        (
          md.ticker = ANY (
            ARRAY ['VNINDEX'::text, 'VN-INDEX'::text, 'VN30'::text, 'HNXINDEX'::text, 'UPCOMINDEX'::text]
          )
        )
        AND (md.close_adj > (0) :: double precision)
      ) THEN (1.0) :: double precision
      ELSE NULL :: double precision
    END AS raw_factor,
    CASE
      WHEN (md.close_unadj > (0) :: double precision) THEN md.close_unadj
      WHEN (
        (
          md.ticker = ANY (
            ARRAY ['VNINDEX'::text, 'VN-INDEX'::text, 'VN30'::text, 'HNXINDEX'::text, 'UPCOMINDEX'::text]
          )
        )
        AND (md.close_adj > (0) :: double precision)
      ) THEN md.close_adj
      ELSE NULL :: double precision
    END AS raw_close
  FROM
    market_data_daily md
)
SELECT
  ticker,
  date,
  (open_adj * raw_factor) AS OPEN,
  (high_adj * raw_factor) AS high,
  (low_adj * raw_factor) AS low,
  raw_close AS close,
  (vwap * raw_factor) AS vwap,
  volume_continuous,
  volume_atc,
  volume_ato,
  volume_total,
  foreign_buy_vol,
  foreign_sell_vol,
  foreign_net_vol,
  is_etf_rebalance_day,
  avg(volume_continuous) OVER (
    PARTITION BY ticker
    ORDER BY
      date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
  ) AS adtv20_continuous,
  (market_cap * raw_factor) AS market_cap,
  raw_factor,
  data_source
FROM
  price_basis
WHERE
  (raw_factor IS NOT NULL);