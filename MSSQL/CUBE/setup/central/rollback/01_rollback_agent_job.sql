-- Central Server Rollback - Step 4
-- Removes both central Agent jobs, their schedules, and the DEVMonitoring operator
-- from INFRA-MGMT01.
--
-- Reverses: 04_create_agent_job.sql
--   Job 1: DBA - Collect Job Status         (schedule: DBA - Collect Job Status - Hourly at :01)
--   Job 2: DBA - Common Monitoring Alerts   (schedule: DBA - Common Monitoring Alerts - Hourly at :05)
--   Operator: DEVMonitoring

USE msdb;
GO

-- ============================================================
-- Remove Job 1: DBA - Collect Job Status
-- ============================================================
IF EXISTS (SELECT 1 FROM dbo.sysjobs WHERE name = 'DBA - Collect Job Status')
BEGIN
    EXEC sp_delete_job
        @job_name = 'DBA - Collect Job Status',
        @delete_unused_schedule = 1;
    PRINT 'Job "DBA - Collect Job Status" deleted.';
END
ELSE
    PRINT 'Job "DBA - Collect Job Status" does not exist, nothing to delete.';
GO

-- Safety: drop orphaned schedule if still present
IF EXISTS (SELECT 1 FROM msdb.dbo.sysschedules WHERE name = 'DBA - Collect Job Status - Hourly at :01')
BEGIN
    EXEC msdb.dbo.sp_delete_schedule
        @schedule_name = 'DBA - Collect Job Status - Hourly at :01',
        @force_delete = 1;
    PRINT 'Schedule "DBA - Collect Job Status - Hourly at :01" deleted.';
END
GO

-- ============================================================
-- Remove Job 2: DBA - Common Monitoring Alerts
-- ============================================================
IF EXISTS (SELECT 1 FROM dbo.sysjobs WHERE name = 'DBA - Common Monitoring Alerts')
BEGIN
    EXEC sp_delete_job
        @job_name = 'DBA - Common Monitoring Alerts',
        @delete_unused_schedule = 1;
    PRINT 'Job "DBA - Common Monitoring Alerts" deleted.';
END
ELSE
    PRINT 'Job "DBA - Common Monitoring Alerts" does not exist, nothing to delete.';
GO

-- Safety: drop orphaned schedule if still present
IF EXISTS (SELECT 1 FROM msdb.dbo.sysschedules WHERE name = 'DBA - Common Monitoring Alerts - Hourly at :05')
BEGIN
    EXEC msdb.dbo.sp_delete_schedule
        @schedule_name = 'DBA - Common Monitoring Alerts - Hourly at :05',
        @force_delete = 1;
    PRINT 'Schedule "DBA - Common Monitoring Alerts - Hourly at :05" deleted.';
END
GO

-- ============================================================
-- Remove Operator: DEVMonitoring
-- NOTE: Only drop if no other jobs still reference this operator.
--       Comment out if the operator is shared with other jobs.
-- ============================================================
IF EXISTS (SELECT 1 FROM msdb.dbo.sysoperators WHERE name = N'DEVMonitoring')
BEGIN
    EXEC msdb.dbo.sp_delete_operator @name = N'DEVMonitoring';
    PRINT 'Operator DEVMonitoring deleted.';
END
ELSE
    PRINT 'Operator DEVMonitoring does not exist, nothing to delete.';
GO

PRINT 'Central rollback step 4 complete (jobs and operator removed from ' + @@SERVERNAME + ').';
