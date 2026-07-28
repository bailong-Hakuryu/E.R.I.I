# Scope archival receipts to the Agent and User pair

Public receipt lookup requires `agent_id`, `user_id`, and `archival_id` to match the same Archival Scope, and a mismatch is reported as not found rather than revealing another pair's record. This prevents accidental cross-pair reads inside the embedded kernel but is deliberately not described as authorization: a hosted service must derive tenant and user scope from authenticated context instead of trusting caller-supplied IDs, and the unauthenticated reference REST service remains unsuitable for public deployment.
