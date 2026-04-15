USE [msdb];
GO

-- Keyword to search for in job step commands
DECLARE @SearchKeyword NVARCHAR(200) = N'%YourKeywordHere%';

SELECT 
    j.name AS job_name,
    js.step_id,
    js.step_name,
    js.command,
    sch.name AS schedule_name,
    sch.enabled AS schedule_enabled,
    sch.freq_type,
    sch.freq_interval,
    sch.freq_subday_type,
    sch.freq_subday_interval,
    sch.active_start_date,
    sch.active_start_time,
    sch.active_end_date,
    sch.active_end_time,
    j.enabled AS job_enabled,
    s.srvname AS originating_server
FROM dbo.sysjobs j
INNER JOIN dbo.sysjobsteps js
    ON js.job_id = j.job_id
INNER JOIN master.dbo.sysservers s
    ON s.srvid = j.originating_server_id
LEFT JOIN dbo.sysjobschedules jsch
    ON j.job_id = jsch.job_id
LEFT JOIN dbo.sysschedules sch
    ON jsch.schedule_id = sch.schedule_id
WHERE js.command LIKE @SearchKeyword
ORDER BY j.name, js.step_id;
GO