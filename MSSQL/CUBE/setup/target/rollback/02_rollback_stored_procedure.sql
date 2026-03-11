-- Target Server Rollback - Step 2
-- Drops Monitoring.SP_MonitoringJobs from dba_db on the target server

USE dba_db;
GO

IF OBJECT_ID('Monitoring.SP_MonitoringJobs', 'P') IS NOT NULL
BEGIN
    DROP PROCEDURE Monitoring.SP_MonitoringJobs;
    PRINT 'Procedure Monitoring.SP_MonitoringJobs dropped.';
END
ELSE
    PRINT 'Procedure Monitoring.SP_MonitoringJobs does not exist, nothing to drop.';
GO

PRINT 'Target rollback step 2 complete.';
