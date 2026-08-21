# Farm scheduling notes

Queue depth is not the bottleneck; per-task startup is. Each task spends
around forty seconds loading before it does any useful work, and we dispatch
thousands of short tasks.

Batching adjacent frames into one task would amortise that. The objection is
that a failure then loses more work, but our failure rate is low enough that
the trade looks obviously worth it.
