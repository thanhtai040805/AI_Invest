ALTER TABLE order_executions
  ADD COLUMN gross_value NUMERIC(18,2),
  ADD COLUMN brokerage_fee NUMERIC(18,6),
  ADD COLUMN transfer_tax NUMERIC(18,6),
  ADD COLUMN cash_delta NUMERIC(18,2);
