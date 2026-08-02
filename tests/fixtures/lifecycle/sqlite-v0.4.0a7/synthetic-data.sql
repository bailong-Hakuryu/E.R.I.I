PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

INSERT INTO stable_identities VALUES
  ('identity-agent-orion', 'agent', 'agent_orion', '2026-01-15T08:00:00+00:00'),
  ('identity-user-lin', 'user', 'user_lin', '2026-01-15T08:00:00+00:00'),
  ('identity-agent-nova', 'agent', 'agent_nova', '2026-01-16T09:00:00+00:00'),
  ('identity-user-mika', 'user', 'user_mika', '2026-01-16T09:00:00+00:00');

INSERT INTO relationships VALUES
  (
    'relationship-orion-lin', 'persona-orion', 'blueprint-orion',
    'identity-agent-orion', 'identity-user-lin', 'agent_orion', 'user_lin',
    '{"name":"Orion","traits":["patient","curious"],"source":"synthetic"}',
    '2026-01-15T08:00:00+00:00'
  ),
  (
    'relationship-nova-mika', 'persona-nova', 'blueprint-nova',
    'identity-agent-nova', 'identity-user-mika', 'agent_nova', 'user_mika',
    '{"name":"Nova","traits":["direct","kind"],"source":"synthetic"}',
    '2026-01-16T09:00:00+00:00'
  );

INSERT INTO relationship_initial_context VALUES
  (
    'relationship-orion-lin',
    '{"premise":{"summary":"共同观察冬夜天空"},"baseline":{"trust":0.2},"manifest_id":null}',
    '2026-01-15T08:00:00+00:00'
  ),
  (
    'relationship-nova-mika',
    '{"premise":{"summary":"一起维护虚构花园"},"baseline":{"trust":0.1},"manifest_id":null}',
    '2026-01-16T09:00:00+00:00'
  );

INSERT INTO core_memories VALUES
  ('agent_orion', 'user_lin', '记得在下雪的清晨检查观测记录。', '2026-01-17T08:30:00+08:00'),
  ('agent_nova', 'user_mika', 'Remember the blue seedling and its weekly notes.', '2026-01-17T19:00:00-05:00');

INSERT INTO memory_nodes VALUES
  (
    'node-orion-snow', 'agent_orion', 'user_lin',
    '{"node_id":"node-orion-snow","content":"第一次记录到六角雪晶","created_at":"2026-01-17T08:31:00+08:00"}'
  ),
  (
    'node-nova-garden', 'agent_nova', 'user_mika',
    '{"node_id":"node-nova-garden","content":"The seedling opened one leaf 🌱","created_at":"2026-01-17T19:02:00-05:00"}'
  );

INSERT INTO source_turns
  (turn_id, relationship_id, status, data, opened_at)
VALUES
  (
    'turn-orion-001', 'relationship-orion-lin', 'completed',
    '{"turn_id":"turn-orion-001","relationship_id":"relationship-orion-lin","user_message":"今天的雪晶很清楚。","assistant_message":"我把观察时间也记下来了。","opened_at":"2026-01-17T08:30:00+08:00","completed_at":"2026-01-17T08:32:00+08:00"}',
    '2026-01-17T08:30:00+08:00'
  ),
  (
    'turn-nova-001', 'relationship-nova-mika', 'completed',
    '{"turn_id":"turn-nova-001","relationship_id":"relationship-nova-mika","user_message":"The garden survived the frost.","assistant_message":"Then we should mark today as a small victory.","opened_at":"2026-01-17T19:00:00-05:00","completed_at":"2026-01-17T19:03:00-05:00"}',
    '2026-01-17T19:00:00-05:00'
  );

INSERT INTO reply_attempts
  (attempt_id, relationship_id, turn_id, attempt_number, data, attempted_at)
VALUES
  (
    'attempt-orion-001', 'relationship-orion-lin', 'turn-orion-001', 1,
    '{"attempt_id":"attempt-orion-001","outcome":"delivered"}',
    '2026-01-17T08:32:00+08:00'
  ),
  (
    'attempt-nova-001', 'relationship-nova-mika', 'turn-nova-001', 1,
    '{"attempt_id":"attempt-nova-001","outcome":"delivered"}',
    '2026-01-17T19:03:00-05:00'
  );

INSERT INTO relationship_events
  (event_id, relationship_id, data, recorded_at)
VALUES
  (
    'event-orion-snow', 'relationship-orion-lin',
    '{"event_id":"event-orion-snow","relationship_id":"relationship-orion-lin","kind":"shared_experience","summary":"一起记录清晨雪晶","source_turn_id":"turn-orion-001"}',
    '2026-01-17T00:33:00+00:00'
  ),
  (
    'event-nova-frost', 'relationship-nova-mika',
    '{"event_id":"event-nova-frost","relationship_id":"relationship-nova-mika","kind":"shared_experience","summary":"The garden survived a frost.","source_turn_id":"turn-nova-001"}',
    '2026-01-18T00:04:00+00:00'
  );

INSERT INTO relationship_adjudications
  (
    decision_id, relationship_id, source_turn_id, source_revision,
    processing_identity, candidate_key, data, created_at
  )
VALUES
  (
    'decision-orion-snow', 'relationship-orion-lin', 'turn-orion-001',
    'revision-orion-001', 'synthetic-extractor-v1', 'shared-snow',
    '{"decision":"accepted","event_id":"event-orion-snow","confidence":0.91}',
    '2026-01-17T00:33:00+00:00'
  ),
  (
    'decision-nova-frost', 'relationship-nova-mika', 'turn-nova-001',
    'revision-nova-001', 'synthetic-extractor-v1', 'garden-frost',
    '{"decision":"accepted","event_id":"event-nova-frost","confidence":0.88}',
    '2026-01-18T00:04:00+00:00'
  );

INSERT INTO relationship_processing_runs
  (
    processing_id, relationship_id, source_turn_id, source_revision,
    processing_identity, record_version, status, data, created_at, updated_at
  )
VALUES
  (
    'processing-orion-001', 'relationship-orion-lin', 'turn-orion-001',
    'revision-orion-001', 'synthetic-extractor-v1', 1, 'completed',
    '{"processing_id":"processing-orion-001","status":"completed","candidate_count":1}',
    '2026-01-17T00:33:00+00:00', '2026-01-17T00:33:01+00:00'
  ),
  (
    'processing-nova-001', 'relationship-nova-mika', 'turn-nova-001',
    'revision-nova-001', 'synthetic-extractor-v1', 1, 'completed',
    '{"processing_id":"processing-nova-001","status":"completed","candidate_count":1}',
    '2026-01-18T00:04:00+00:00', '2026-01-18T00:04:01+00:00'
  );

INSERT INTO persona_reflection_decisions
  (decision_id, relationship_id, event_id, interpretation_identity, data, recorded_at)
VALUES
  (
    'reflection-decision-orion', 'relationship-orion-lin', 'event-orion-snow',
    'synthetic-reflector-v1',
    '{"decision":"reflection","meaning":"Shared careful observation reinforces patience."}',
    '2026-01-17T00:34:00+00:00'
  );

INSERT INTO persona_reflection_records
  (reflection_id, relationship_id, event_id, target_reflection_id, data, recorded_at)
VALUES
  (
    'reflection-orion', 'relationship-orion-lin', 'event-orion-snow', '',
    '{"reflection_id":"reflection-orion","meaning":"愿意继续耐心记录微小变化。","intensity":0.35}',
    '2026-01-17T00:34:00+00:00'
  );

INSERT INTO timeline_entries
  (agent_id, user_id, content, timestamp, timeline_entry_id, source_archival_id, data)
VALUES
  (
    'agent_orion', 'user_lin', '清晨一起观察雪晶 ❄',
    '2026-01-17T08:31:00+08:00', 'timeline-orion-snow', NULL,
    '{"timeline_entry_id":"timeline-orion-snow","recorded_at":"2026-01-17T08:31:00+08:00","content":"清晨一起观察雪晶 ❄"}'
  ),
  (
    'agent_orion', 'user_lin', 'A legacy note without a stable ID.',
    '2026-01-17 00:35:00', NULL, NULL, NULL
  ),
  (
    'agent_nova', 'user_mika', 'The blue seedling survived.',
    '2026-01-17T19:02:00-05:00', 'timeline-nova-seedling', NULL,
    '{"timeline_entry_id":"timeline-nova-seedling","legacy_timestamp":"2026-01-17T19:02:00-05:00","content":"The blue seedling survived 🌱"}'
  ),
  (
    'agent_nova', 'user_mika', 'Unparseable legacy clock value.',
    'not-a-time', NULL, NULL,
    '{"content":"Unparseable legacy clock value."}'
  );

COMMIT;
