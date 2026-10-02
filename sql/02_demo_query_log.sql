-- Clip 11a. Run in the ClickHouse Cloud SQL console right after load_test.py finishes.
-- clusterAllReplicas is needed on Cloud because each replica keeps its own query_log.

-- 1) The headline: how many agent queries, and how fast
SELECT
  count()                                   AS agent_queries,
  round(quantile(0.50)(query_duration_ms))  AS p50_ms,
  round(quantile(0.95)(query_duration_ms))  AS p95_ms,
  max(query_duration_ms)                    AS max_ms,
  formatReadableQuantity(sum(read_rows))    AS rows_scanned
FROM clusterAllReplicas(default, system.query_log)
WHERE type = 'QueryFinish'
  AND user = 'mcp_agent'
  AND query_kind = 'Select'
  AND event_time > now() - INTERVAL 10 MINUTE;

-- 2) Queries per second during the run (switch the result to a chart in the SQL console)
SELECT
  event_time,
  count() AS queries
FROM clusterAllReplicas(default, system.query_log)
WHERE type = 'QueryFinish'
  AND user = 'mcp_agent'
  AND event_time > now() - INTERVAL 10 MINUTE
GROUP BY event_time
ORDER BY event_time;
