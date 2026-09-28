SELECT
  date AS "time",
  ticker AS symbol,
  OPEN,
  high,
  low,
  close,
  volume_total AS volume
FROM
  market_data_daily_calculation md;