ALTER TABLE standalone_ml_predictions
  ADD COLUMN feature_date DATE,
  ADD COLUMN decision VARCHAR(16),
  ADD COLUMN decision_reason VARCHAR(64),
  ADD COLUMN order_id VARCHAR(64),
  ADD COLUMN model_version VARCHAR(64);
CREATE INDEX standalone_ml_predictions_order_id_idx ON standalone_ml_predictions(order_id);
