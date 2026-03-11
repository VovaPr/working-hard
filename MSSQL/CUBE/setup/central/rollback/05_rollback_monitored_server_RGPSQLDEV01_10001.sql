-- Central Server Rollback - Optional Step 05
-- Removes monitored server entry added by:
--   setup/central/05_add_monitored_server_RGPSQLDEV01_10001.sql
--
-- NOTE:
-- Run this script before 04_rollback_schema.sql (which drops Monitoring.MonitoredServers).

USE dba_db;
GO

DECLARE @ServerName SYSNAME = N'RGPSQLDEV01';
DECLARE @Port INT = 10001;

IF OBJECT_ID('Monitoring.MonitoredServers', 'U') IS NULL
BEGIN
    PRINT 'Table Monitoring.MonitoredServers does not exist, nothing to rollback.';
    RETURN;
END

IF EXISTS (
    SELECT 1
    FROM Monitoring.MonitoredServers
    WHERE ServerName = @ServerName
      AND Port = @Port
)
BEGIN
    DELETE FROM Monitoring.MonitoredServers
    WHERE ServerName = @ServerName
      AND Port = @Port;

    PRINT 'Removed monitored server entry: ' + @ServerName + ':' + CAST(@Port AS NVARCHAR(10));
END
ELSE
BEGIN
    PRINT 'Entry not found, nothing to delete: ' + @ServerName + ':' + CAST(@Port AS NVARCHAR(10));
END
GO

SELECT ServerName, Port, IsActive, CreatedAt, UpdatedAt
FROM Monitoring.MonitoredServers
WHERE ServerName = N'RGPSQLDEV01';
