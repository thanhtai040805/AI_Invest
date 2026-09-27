-- Keep actual audit write time. Legacy rows have unknown replay provenance.
ALTER TABLE "log_investment_thesis"
  ADD COLUMN "analysis_date" DATE,
  ADD COLUMN "is_replay" BOOLEAN;
