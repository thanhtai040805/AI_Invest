CREATE TABLE portfolio_nav_history (
  account_id VARCHAR(255) NOT NULL,
  date DATE NOT NULL,
  total_nav NUMERIC(18,2) NOT NULL,
  cash_balance NUMERIC(18,2) NOT NULL,
  PRIMARY KEY (account_id, date)
);
