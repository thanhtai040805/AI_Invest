-- Rollback script for `migrations/0009_session_context_ecce.sql`.
-- Drops derived SAG-ECCE triggers, functions, indexes, and tables in reverse dependency order.
-- Raw conversation history in `threads` and `messages` remains 100% intact.

BEGIN;

DROP TRIGGER IF EXISTS trg_session_decision_lineage_scope_guard ON session_decision_ledger;
DROP FUNCTION IF EXISTS fn_session_decision_lineage_scope_guard();

DROP TRIGGER IF EXISTS trg_session_unit_messages_cascade_unit ON session_unit_messages;
DROP FUNCTION IF EXISTS fn_session_unit_messages_cascade_unit();

DROP TRIGGER IF EXISTS trg_session_unit_messages_before_update_guard ON session_unit_messages;
DROP FUNCTION IF EXISTS fn_session_unit_messages_before_update_guard();

DROP TRIGGER IF EXISTS trg_session_units_after_insert_link ON session_units;
DROP FUNCTION IF EXISTS fn_session_units_after_insert_link();

DROP TRIGGER IF EXISTS trg_session_units_before_update_guard ON session_units;
DROP FUNCTION IF EXISTS fn_session_units_before_update_guard();

DROP INDEX IF EXISTS ix_session_graph_edges_target_unit;
DROP INDEX IF EXISTS ix_session_graph_edges_source_unit;
DROP INDEX IF EXISTS ix_session_graph_edges_target_node;
DROP INDEX IF EXISTS ix_session_graph_edges_source_node;
DROP INDEX IF EXISTS ix_session_graph_edges_thread_type;
DROP TABLE IF EXISTS session_graph_edges;

DROP INDEX IF EXISTS ix_session_decision_superseded_by;
DROP INDEX IF EXISTS ix_session_decision_supersedes;
DROP INDEX IF EXISTS ix_session_decision_project_status;
DROP INDEX IF EXISTS ix_session_decision_thread_slot_status;
DROP TABLE IF EXISTS session_decision_ledger;

DROP INDEX IF EXISTS ix_session_unit_messages_thread_msg;
DROP INDEX IF EXISTS ix_session_unit_messages_message;
DROP TABLE IF EXISTS session_unit_messages;

DROP INDEX IF EXISTS ix_session_units_temporal;
DROP INDEX IF EXISTS ix_session_units_message_to;
DROP INDEX IF EXISTS ix_session_units_message_from;
DROP INDEX IF EXISTS ix_session_units_thread_episode;
DROP INDEX IF EXISTS ix_session_units_project_thread;
DROP TABLE IF EXISTS session_units;

COMMIT;
