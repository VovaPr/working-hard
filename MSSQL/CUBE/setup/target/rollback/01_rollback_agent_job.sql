-- Target Server Rollback - Step 3
-- Removes the DBA - Monitoring Alerts Agent job, its schedule,
-- and the DEVMonitoring operator from the target server.
--
-- Reverses: 03_create_agent_job.sql
--   Job:      DBA - Monitoring Alerts
--   Schedule: DBA - Monitoring Alerts - Every Hour
--   Operator: DEVMonitoring

USE msdb;
GO

-- ============================================================
-- Remove Job: DBA - Monitoring Alerts
-- ============================================================
IF EXISTS (SELECT 1 FROM dbo.sysjobs WHERE name = 'DBA - Monitoring Alerts')
BEGIN
    EXEC sp_delete_job
        @job_name = 'DBA - Monitoring Alerts',
        @delete_unused_schedule = 1;
    PRINT 'Job "DBA - Monitoring Alerts" deleted.';
END
ELSE
    PRINT 'Job "DBA - Monitoring Alerts" does not exist, nothing to delete.';
GO

-- Safety: drop orphaned schedule if still present
IF EXISTS (SELECT 1 FROM msdb.dbo.sysschedules WHERE name = 'DBA - Monitoring Alerts - Every Hour')
BEGIN
    EXEC msdb.dbo.sp_delete_schedule
        @schedule_name = 'DBA - Monitoring Alerts - Every Hour',
        @force_delete = 1;
    PRINT 'Schedule "DBA - Monitoring Alerts - Every Hour" deleted.';
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

PRINT 'Target rollback step 3 complete (job and operator removed from ' + @@SERVERNAME + ').';
