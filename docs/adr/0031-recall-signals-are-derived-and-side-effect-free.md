# Recall signals are derived and side-effect free

Due promises, overdue promises, and open loops appear as Recall Signals recomputed from accepted history and an explicit observation context rather than as persisted memories or automatic relationship events. Each signal identifies its source events and is deterministic for the same inputs, but it never mutates relationship state, starts background work, or sends a message; any later fulfillment, repair, conflict, or acknowledgement must enter through the normal evidence-backed event path.
