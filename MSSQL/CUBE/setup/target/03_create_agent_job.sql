-- Target Server Setup - Step 3
-- Create Agent Job on each target server
-- Job: DBA - Monitoring Alerts
-- Schedule: Every 1 hour

USE msdb;
GO

-- Create or update SQL Agent operator for monitoring notifications
IF NOT EXISTS (SELECT 1 FROM msdb.dbo.sysoperators WHERE name = N'DEVMonitoring')
BEGIN
    EXEC msdb.dbo.sp_add_operator
        @name = N'DEVMonitoring',
        @enabled = 1,
        @weekday_pager_start_time = 90000,
        @weekday_pager_end_time = 180000,
        @saturday_pager_start_time = 90000,
        @saturday_pager_end_time = 180000,
        @sunday_pager_start_time = 90000,
        @sunday_pager_end_time = 180000,
        @pager_days = 0,
        @email_address = N'559c4de8.cube.global@emea.teams.ms',
        @category_name = N'[Uncategorized]';
END
ELSE
BEGIN
    EXEC msdb.dbo.sp_update_operator
        @name = N'DEVMonitoring',
        @enabled = 1,
        @weekday_pager_start_time = 90000,
        @weekday_pager_end_time = 180000,
        @saturday_pager_start_time = 90000,
        @saturday_pager_end_time = 180000,
        @sunday_pager_start_time = 90000,
        @sunday_pager_end_time = 180000,
        @pager_days = 0,
        @email_address = N'559c4de8.cube.global@emea.teams.ms';
END
GO

-- Remove existing job
IF EXISTS (SELECT 1 FROM dbo.sysjobs WHERE name = 'DBA - Monitoring Alerts')
BEGIN
    EXEC sp_delete_job @job_name = 'DBA - Monitoring Alerts', @delete_unused_schedule = 1;
END
GO

-- Create the job
EXEC sp_add_job
    @job_name = 'DBA - Monitoring Alerts',
    @enabled = 1,
    @description = 'Target server monitoring job; collects local job statuses and tracks failures. Runs hourly.',
    @owner_login_name = 'sa',
    @notify_level_email = 2,
    @notify_email_operator_name = N'DEVMonitoring';
GO

-- Step 1: Collect status and analyze alerts (only step on target servers)
EXEC sp_add_jobstep
    @job_name = 'DBA - Monitoring Alerts',
    @step_name = 'Collect Job Status and Check Alerts',
    @step_id = 1,
    @subsystem = 'TSQL',
    @command = 'EXEC dba_db.Monitoring.SP_MonitoringJobs',
    @database_name = 'dba_db',
    @retry_attempts = 3,
    @retry_interval = 1,
    @on_success_action = 1,
    @on_fail_action = 2;
GO

IF EXISTS (
    SELECT 1
    FROM msdb.dbo.sysschedules
    WHERE name = 'DBA - Monitoring Alerts - Every Hour'
)
BEGIN
    EXEC msdb.dbo.sp_delete_schedule @schedule_name = 'DBA - Monitoring Alerts - Every Hour';
END
GO

-- Create schedule: every 1 hour at :01
EXEC sp_add_schedule
    @schedule_name = 'DBA - Monitoring Alerts - Every Hour',
    @freq_type = 4,
    @freq_interval = 1,
    @freq_subday_type = 8,
    @freq_subday_interval = 1,
    @active_start_time = 000100, -- start at :01
    @active_end_time = 235959;
GO

-- Attach schedule
EXEC sp_attach_schedule
    @job_name = 'DBA - Monitoring Alerts',
    @schedule_name = 'DBA - Monitoring Alerts - Every Hour';
GO

-- Assign to local server
EXEC sp_add_jobserver
    @job_name = 'DBA - Monitoring Alerts',
    @server_name = N'(local)';
GO

PRINT 'Target Agent Job "DBA - Monitoring Alerts" created successfully (every hour) on ' + @@SERVERNAME;
