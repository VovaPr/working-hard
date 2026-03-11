-- Target Server Rollback - Step 1
-- Drops indexes, tables, and Monitoring schema from dba_db on the target server
-- WARNING: All monitoring data will be permanently deleted.

USE dba_db;
GO

-- Drop indexes first
IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_FailedJobsAlerts_AlertSentTime')
    DROP INDEX IX_FailedJobsAlerts_AlertSentTime ON Monitoring.FailedJobsAlerts;

IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Jobs_LastRunDate')
    DROP INDEX IX_Jobs_LastRunDate ON Monitoring.Jobs;

IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Jobs_ServerName')
    DROP INDEX IX_Jobs_ServerName ON Monitoring.Jobs;
GO

-- Drop tables
IF OBJECT_ID('Monitoring.FailedJobsAlerts', 'U') IS NOT NULL
BEGIN
    DROP TABLE Monitoring.FailedJobsAlerts;
    PRINT 'Table Monitoring.FailedJobsAlerts dropped.';
END

IF OBJECT_ID('Monitoring.Jobs', 'U') IS NOT NULL
BEGIN
    DROP TABLE Monitoring.Jobs;
    PRINT 'Table Monitoring.Jobs dropped.';
END
GO

-- Drop schema (only if no objects remain)
IF EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'Monitoring')
BEGIN
    EXEC sp_executesql N'DROP SCHEMA Monitoring';
    PRINT 'Schema Monitoring dropped.';
END
GO

PRINT 'Target rollback step 1 complete (schema removed from ' + @@SERVERNAME + ').';
