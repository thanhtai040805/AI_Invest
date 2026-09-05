-- ====================================================================
-- MIGRATION 003: CLEANUP REDUNDANT TABLES & UNIFY PORTFOLIO SCHEMA
-- ====================================================================
-- 1. Xóa bỏ bảng thừa portfolio_weights (legacy dead code).
-- 2. Xóa bỏ bảng trùng lặp portfolio_positions (thống nhất dùng bảng 'positions').
-- ====================================================================

-- 1. DROP BẢNG THỪA HOÀN TOÀN
DROP TABLE IF EXISTS portfolio_weights CASCADE;

-- 2. DROP BẢNG TRÙNG LẶP (THỐNG NHẤT HỆ THỐNG VÀO BẢNG 'positions')
DROP TABLE IF EXISTS portfolio_positions CASCADE;
