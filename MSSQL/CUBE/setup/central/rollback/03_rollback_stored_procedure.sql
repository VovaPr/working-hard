-- Central Server Rollback - Step 2
-- Drops Monitoring.SP_MonitoringJobs from dba_db on INFRA-MGMT01

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

PRINT 'Central rollback step 2 complete.';
