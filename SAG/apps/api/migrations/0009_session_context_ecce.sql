-- Chặng 2 (SAG-ECCE): Derived provenance units, constituent message links,
-- bi-temporal decision ledger, and multi-view graph edges for Zero-Token Session Context.
-- Additive migration: does not alter `threads`, `messages`, or migrations 0001..0008.
-- Rollback script: `SAG/apps/api/scripts/0009_session_context_ecce_down.sql`.

BEGIN;

CREATE TABLE IF NOT EXISTS session_units (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id VARCHAR(256) NOT NULL,
    project_id VARCHAR(256) NOT NULL,
    thread_id VARCHAR(32) NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    owner_key VARCHAR(64) NULL,
    unit_kind VARCHAR(16) NOT NULL DEFAULT 'TURN',
    ordinal INTEGER NOT NULL,
    episode_id VARCHAR(64) NOT NULL,
    episode_ordinal INTEGER NOT NULL DEFAULT 0,
    window_id VARCHAR(64) NULL,
    message_from_id VARCHAR(32) NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    message_to_id VARCHAR(32) NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    source_message_ids_json JSON NOT NULL DEFAULT '[]',
    role VARCHAR(16) NULL,
    epistemic_status VARCHAR(24) NOT NULL DEFAULT 'FACT',
    topic_slot_key VARCHAR(128) NULL,
    is_anaphoric_or_short BOOLEAN NOT NULL DEFAULT FALSE,
    security_partition_ids_json JSON NOT NULL DEFAULT '[]',
    cited_source_ids_json JSON NOT NULL DEFAULT '[]',
    entity_mentions_json JSON NOT NULL DEFAULT '[]',
    sentences_json JSON NOT NULL DEFAULT '[]',
    term_freqs_json JSON NOT NULL DEFAULT '{}',
    doc_word_len INTEGER NOT NULL DEFAULT 1,
    token_count INTEGER NOT NULL DEFAULT 0,
    content_hash VARCHAR(64) NOT NULL,
    embedding_model_signature VARCHAR(128) NOT NULL DEFAULT 'deterministic-ngram-v1',
    qdrant_point_id VARCHAR(36) NULL,
    valid_from TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    valid_to TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_session_units_thread_kind_ordinal
        UNIQUE (thread_id, unit_kind, ordinal),
    CONSTRAINT uq_session_units_scope_identity
        UNIQUE (id, tenant_id, project_id, thread_id),
    CONSTRAINT ck_session_units_tenant_id
        CHECK (length(trim(tenant_id)) > 0),
    CONSTRAINT ck_session_units_project_id
        CHECK (length(trim(project_id)) > 0),
    CONSTRAINT ck_session_units_unit_kind
        CHECK (unit_kind IN ('TURN', 'WINDOW', 'EPISODE')),
    CONSTRAINT ck_session_units_turn_single_msg
        CHECK (unit_kind != 'TURN' OR message_from_id = message_to_id),
    CONSTRAINT ck_session_units_span_source_ids
        CHECK (message_from_id = message_to_id OR json_array_length(source_message_ids_json) >= 2),
    CONSTRAINT ck_session_units_ordinal_nonnegative
        CHECK (ordinal >= 0),
    CONSTRAINT ck_session_units_episode_id
        CHECK (length(trim(episode_id)) > 0),
    CONSTRAINT ck_session_units_episode_ordinal_nonnegative
        CHECK (episode_ordinal >= 0),
    CONSTRAINT ck_session_units_epistemic_status
        CHECK (epistemic_status IN (
            'FACT', 'PROPOSED', 'CONSIDERING', 'CONFIRMED',
            'SUPERSEDED', 'NEGATED', 'OPEN_ISSUE'
        )),
    CONSTRAINT ck_session_units_doc_word_len_nonnegative
        CHECK (doc_word_len >= 0),
    CONSTRAINT ck_session_units_token_count_nonnegative
        CHECK (token_count >= 0),
    CONSTRAINT ck_session_units_content_hash_len
        CHECK (length(content_hash) = 64),
    CONSTRAINT ck_session_units_embedding_sig
        CHECK (length(trim(embedding_model_signature)) > 0),
    CONSTRAINT ck_session_units_valid_window
        CHECK (valid_to >= valid_from)
);

CREATE INDEX IF NOT EXISTS ix_session_units_project_thread
    ON session_units (tenant_id, project_id, thread_id, unit_kind, ordinal);
CREATE INDEX IF NOT EXISTS ix_session_units_thread_episode
    ON session_units (thread_id, episode_id, ordinal);
CREATE INDEX IF NOT EXISTS ix_session_units_message_from
    ON session_units (message_from_id);
CREATE INDEX IF NOT EXISTS ix_session_units_message_to
    ON session_units (message_to_id);
CREATE INDEX IF NOT EXISTS ix_session_units_temporal
    ON session_units (thread_id, epistemic_status, valid_from, valid_to);

CREATE TABLE IF NOT EXISTS session_unit_messages (
    session_unit_id VARCHAR(36) NOT NULL,
    tenant_id VARCHAR(256) NOT NULL,
    project_id VARCHAR(256) NOT NULL,
    thread_id VARCHAR(32) NOT NULL,
    message_id VARCHAR(32) NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    PRIMARY KEY (session_unit_id, message_id),
    CONSTRAINT fk_session_unit_messages_unit_scope
        FOREIGN KEY (session_unit_id, tenant_id, project_id, thread_id)
        REFERENCES session_units (id, tenant_id, project_id, thread_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_session_unit_messages_message
    ON session_unit_messages (message_id);
CREATE INDEX IF NOT EXISTS ix_session_unit_messages_thread_msg
    ON session_unit_messages (thread_id, message_id);

CREATE TABLE IF NOT EXISTS session_decision_ledger (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id VARCHAR(256) NOT NULL,
    project_id VARCHAR(256) NOT NULL,
    thread_id VARCHAR(32) NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    session_unit_id VARCHAR(36) NOT NULL,
    ledger_kind VARCHAR(24) NOT NULL DEFAULT 'DECISION',
    topic_slot_key VARCHAR(128) NOT NULL,
    turn_ordinal INTEGER NOT NULL,
    epistemic_status VARCHAR(24) NOT NULL,
    summary_text TEXT NOT NULL,
    entity_keys_json JSON NOT NULL DEFAULT '[]',
    superseded_entity_keys_json JSON NOT NULL DEFAULT '[]',
    companion_unit_ids_json JSON NOT NULL DEFAULT '[]',
    supersedes_id VARCHAR(36) NULL REFERENCES session_decision_ledger(id) ON DELETE SET NULL,
    superseded_by_id VARCHAR(36) NULL REFERENCES session_decision_ledger(id) ON DELETE SET NULL,
    supersedes_ids_json JSON NOT NULL DEFAULT '[]',
    security_partition_ids_json JSON NOT NULL DEFAULT '[]',
    cited_source_ids_json JSON NOT NULL DEFAULT '[]',
    valid_from TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    valid_to TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_session_decision_unit_slot
        UNIQUE (session_unit_id, topic_slot_key),
    CONSTRAINT fk_session_decision_unit_scope
        FOREIGN KEY (session_unit_id, tenant_id, project_id, thread_id)
        REFERENCES session_units (id, tenant_id, project_id, thread_id)
        ON DELETE CASCADE,
    CONSTRAINT ck_session_decision_tenant_id
        CHECK (length(trim(tenant_id)) > 0),
    CONSTRAINT ck_session_decision_project_id
        CHECK (length(trim(project_id)) > 0),
    CONSTRAINT ck_session_decision_ledger_kind
        CHECK (ledger_kind IN ('DECISION', 'OPEN_TASK', 'CONSTRAINT', 'OPEN_ISSUE')),
    CONSTRAINT ck_session_decision_topic_slot_key
        CHECK (length(trim(topic_slot_key)) > 0),
    CONSTRAINT ck_session_decision_turn_ordinal_nonnegative
        CHECK (turn_ordinal >= 0),
    CONSTRAINT ck_session_decision_epistemic_status
        CHECK (epistemic_status IN (
            'FACT', 'PROPOSED', 'CONSIDERING', 'CONFIRMED',
            'SUPERSEDED', 'NEGATED', 'OPEN_ISSUE'
        )),
    CONSTRAINT ck_session_decision_valid_window
        CHECK (valid_to >= valid_from)
);

CREATE INDEX IF NOT EXISTS ix_session_decision_thread_slot_status
    ON session_decision_ledger (thread_id, topic_slot_key, epistemic_status, turn_ordinal);
CREATE INDEX IF NOT EXISTS ix_session_decision_project_status
    ON session_decision_ledger (tenant_id, project_id, thread_id, epistemic_status);
CREATE INDEX IF NOT EXISTS ix_session_decision_supersedes
    ON session_decision_ledger (supersedes_id);
CREATE INDEX IF NOT EXISTS ix_session_decision_superseded_by
    ON session_decision_ledger (superseded_by_id);

CREATE TABLE IF NOT EXISTS session_graph_edges (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id VARCHAR(256) NOT NULL,
    project_id VARCHAR(256) NOT NULL,
    thread_id VARCHAR(32) NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    source_unit_id VARCHAR(36) NOT NULL,
    target_unit_id VARCHAR(36) NULL,
    source_node_type VARCHAR(24) NOT NULL,
    source_node_key VARCHAR(256) NOT NULL,
    target_node_type VARCHAR(24) NOT NULL,
    target_node_key VARCHAR(256) NOT NULL,
    edge_type VARCHAR(32) NOT NULL,
    weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    metadata_json JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_session_graph_edges_provenance
        UNIQUE (
            source_unit_id,
            source_node_type,
            source_node_key,
            target_node_type,
            target_node_key,
            edge_type
        ),
    CONSTRAINT fk_session_graph_edges_source_unit_scope
        FOREIGN KEY (source_unit_id, tenant_id, project_id, thread_id)
        REFERENCES session_units (id, tenant_id, project_id, thread_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_session_graph_edges_target_unit_scope
        FOREIGN KEY (target_unit_id, tenant_id, project_id, thread_id)
        REFERENCES session_units (id, tenant_id, project_id, thread_id)
        ON DELETE CASCADE,
    CONSTRAINT ck_session_graph_edges_tenant_id
        CHECK (length(trim(tenant_id)) > 0),
    CONSTRAINT ck_session_graph_edges_project_id
        CHECK (length(trim(project_id)) > 0),
    CONSTRAINT ck_session_graph_edges_source_node_type
        CHECK (source_node_type IN ('UNIT', 'SENTENCE', 'ENTITY', 'EPISODE', 'SOURCE')),
    CONSTRAINT ck_session_graph_edges_target_node_type
        CHECK (target_node_type IN ('UNIT', 'SENTENCE', 'ENTITY', 'EPISODE', 'SOURCE')),
    CONSTRAINT ck_session_graph_edges_source_node_key
        CHECK (length(trim(source_node_key)) > 0),
    CONSTRAINT ck_session_graph_edges_target_node_key
        CHECK (length(trim(target_node_key)) > 0),
    CONSTRAINT ck_session_graph_edges_edge_type
        CHECK (edge_type IN (
            'ENTITY_MENTION', 'SENTENCE_IN_UNIT', 'ADJACENT_IN_EPISODE',
            'SUPERSEDES', 'ALIAS_CANDIDATE', 'CITES_DOCUMENT_SOURCE'
        )),
    CONSTRAINT ck_session_graph_edges_weight_nonnegative
        CHECK (weight >= 0.0)
);

CREATE INDEX IF NOT EXISTS ix_session_graph_edges_thread_type
    ON session_graph_edges (thread_id, edge_type);
CREATE INDEX IF NOT EXISTS ix_session_graph_edges_source_node
    ON session_graph_edges (thread_id, source_node_key);
CREATE INDEX IF NOT EXISTS ix_session_graph_edges_target_node
    ON session_graph_edges (thread_id, target_node_key);
CREATE INDEX IF NOT EXISTS ix_session_graph_edges_source_unit
    ON session_graph_edges (source_unit_id);
CREATE INDEX IF NOT EXISTS ix_session_graph_edges_target_unit
    ON session_graph_edges (target_unit_id);

-- PostgreSQL triggers enforcing multi-message window/episode cascade & cross-thread/scope integrity
CREATE OR REPLACE FUNCTION fn_session_units_before_update_guard()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.thread_id IS DISTINCT FROM OLD.thread_id
       OR NEW.unit_kind IS DISTINCT FROM OLD.unit_kind
       OR NEW.message_from_id IS DISTINCT FROM OLD.message_from_id
       OR NEW.message_to_id IS DISTINCT FROM OLD.message_to_id
       OR NEW.source_message_ids_json::text IS DISTINCT FROM OLD.source_message_ids_json::text THEN
        RAISE EXCEPTION 'SessionUnit provenance identity and constituent messages are immutable'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_session_units_before_update_guard ON session_units;
CREATE TRIGGER trg_session_units_before_update_guard
BEFORE UPDATE ON session_units
FOR EACH ROW
EXECUTE FUNCTION fn_session_units_before_update_guard();

CREATE OR REPLACE FUNCTION fn_session_units_after_insert_link()
RETURNS TRIGGER AS $$
DECLARE
    bad_thread_count INTEGER;
    has_from BOOLEAN;
    has_to BOOLEAN;
BEGIN
    IF json_typeof(NEW.source_message_ids_json) <> 'array' THEN
        RAISE EXCEPTION 'source_message_ids_json must be a JSON array'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1 FROM json_array_elements(NEW.source_message_ids_json) AS raw_elem
        WHERE json_typeof(raw_elem) <> 'string'
    ) THEN
        RAISE EXCEPTION 'source_message_ids_json elements must all be JSON strings'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.message_from_id <> NEW.message_to_id THEN
        SELECT EXISTS (
            SELECT 1 FROM json_array_elements_text(NEW.source_message_ids_json) AS elem
            WHERE elem = NEW.message_from_id
        ) INTO has_from;
        SELECT EXISTS (
            SELECT 1 FROM json_array_elements_text(NEW.source_message_ids_json) AS elem
            WHERE elem = NEW.message_to_id
        ) INTO has_to;
        IF NOT has_from OR NOT has_to THEN
            RAISE EXCEPTION 'Multi-message SessionUnit source_message_ids_json must include message_from_id and message_to_id'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    INSERT INTO session_unit_messages (session_unit_id, tenant_id, project_id, thread_id, message_id)
    SELECT DISTINCT NEW.id, NEW.tenant_id, NEW.project_id, NEW.thread_id, candidate_msg_id
    FROM (
        SELECT NEW.message_from_id AS candidate_msg_id
        UNION ALL
        SELECT NEW.message_to_id AS candidate_msg_id
        UNION ALL
        SELECT elem AS candidate_msg_id
          FROM json_array_elements_text(NEW.source_message_ids_json) AS elem
        UNION ALL
        SELECT u.message_from_id AS candidate_msg_id
          FROM session_units u,
               (SELECT ordinal AS ord_from FROM session_units
                 WHERE thread_id = NEW.thread_id AND unit_kind = 'TURN' AND message_from_id = NEW.message_from_id
                 LIMIT 1) uf,
               (SELECT ordinal AS ord_to FROM session_units
                 WHERE thread_id = NEW.thread_id AND unit_kind = 'TURN' AND message_from_id = NEW.message_to_id
                 LIMIT 1) ut
         WHERE NEW.message_from_id <> NEW.message_to_id
           AND u.thread_id = NEW.thread_id
           AND u.unit_kind = 'TURN'
           AND uf.ord_from <= ut.ord_to
           AND u.ordinal BETWEEN uf.ord_from AND ut.ord_to
    ) AS candidates
    ON CONFLICT (session_unit_id, message_id) DO NOTHING;

    SELECT COUNT(*) INTO bad_thread_count
      FROM session_unit_messages sum_link
      JOIN messages m ON m.id = sum_link.message_id
     WHERE sum_link.session_unit_id = NEW.id
       AND m.thread_id <> NEW.thread_id;

    IF bad_thread_count > 0 THEN
        RAISE EXCEPTION 'SessionUnit constituent message belongs to a different thread_id'
            USING ERRCODE = '23503';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_session_units_after_insert_link ON session_units;
CREATE TRIGGER trg_session_units_after_insert_link
AFTER INSERT ON session_units
FOR EACH ROW
EXECUTE FUNCTION fn_session_units_after_insert_link();

CREATE OR REPLACE FUNCTION fn_session_unit_messages_before_update_guard()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'session_unit_messages rows are immutable'
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_session_unit_messages_before_update_guard ON session_unit_messages;
CREATE TRIGGER trg_session_unit_messages_before_update_guard
BEFORE UPDATE ON session_unit_messages
FOR EACH ROW
EXECUTE FUNCTION fn_session_unit_messages_before_update_guard();

CREATE OR REPLACE FUNCTION fn_session_unit_messages_cascade_unit()
RETURNS TRIGGER AS $$
BEGIN
    DELETE FROM session_units WHERE id = OLD.session_unit_id;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_session_unit_messages_cascade_unit ON session_unit_messages;
CREATE TRIGGER trg_session_unit_messages_cascade_unit
AFTER DELETE ON session_unit_messages
FOR EACH ROW
EXECUTE FUNCTION fn_session_unit_messages_cascade_unit();

CREATE OR REPLACE FUNCTION fn_session_decision_lineage_scope_guard()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
           OR NEW.project_id IS DISTINCT FROM OLD.project_id
           OR NEW.thread_id IS DISTINCT FROM OLD.thread_id
           OR NEW.session_unit_id IS DISTINCT FROM OLD.session_unit_id THEN
            RAISE EXCEPTION 'session_decision_ledger scope identity is immutable'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    IF NEW.supersedes_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.supersedes_id IS DISTINCT FROM OLD.supersedes_id) THEN
        IF NEW.supersedes_id = NEW.id THEN
            RAISE EXCEPTION 'session_decision_ledger cannot supersede itself'
                USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM session_decision_ledger p
             WHERE p.id = NEW.supersedes_id
               AND p.tenant_id = NEW.tenant_id
               AND p.project_id = NEW.project_id
               AND p.thread_id = NEW.thread_id
        ) THEN
            RAISE EXCEPTION 'supersedes_id must belong to the same tenant_id, project_id, and thread_id'
                USING ERRCODE = '23503';
        END IF;
    END IF;

    IF NEW.superseded_by_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.superseded_by_id IS DISTINCT FROM OLD.superseded_by_id) THEN
        IF NEW.superseded_by_id = NEW.id THEN
            RAISE EXCEPTION 'session_decision_ledger cannot be superseded by itself'
                USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM session_decision_ledger n
             WHERE n.id = NEW.superseded_by_id
               AND n.tenant_id = NEW.tenant_id
               AND n.project_id = NEW.project_id
               AND n.thread_id = NEW.thread_id
        ) THEN
            RAISE EXCEPTION 'superseded_by_id must belong to the same tenant_id, project_id, and thread_id'
                USING ERRCODE = '23503';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_session_decision_lineage_scope_guard ON session_decision_ledger;
CREATE TRIGGER trg_session_decision_lineage_scope_guard
BEFORE INSERT OR UPDATE ON session_decision_ledger
FOR EACH ROW
EXECUTE FUNCTION fn_session_decision_lineage_scope_guard();

COMMIT;
