-- Portfolio account identifiers must fit the UUIDs used by the users table.
ALTER TABLE "portfolio_account"
  ALTER COLUMN "account_id" TYPE VARCHAR(64);
