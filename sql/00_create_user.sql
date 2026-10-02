-- Run as an admin in the ClickHouse Cloud SQL console.
-- The agent gets a dedicated, read-only user: least privilege is part of the accountability story.
CREATE USER IF NOT EXISTS mcp_agent IDENTIFIED BY 'change-me';
GRANT SELECT ON uk.* TO mcp_agent;
