USE [msdb];
GO

-- Declare the keyword you want to search for in job step commands
DECLARE @SearchKeyword NVARCHAR(200) = N'%YourKeywordHere%';

SELECT 
    j.job_id,
    s.srvname AS originating_server,
    j.name AS job_name,
    js.step_id,
    js.step_name,
    js.command,
    j.enabled
FROM dbo.sysjobs j
INNER JOIN dbo.sysjobsteps js
    ON js.job_id = j.job_id
INNER JOIN master.dbo.sysservers s
    ON s.srvid = j.originating_server_id
WHERE js.command LIKE @SearchKeyword
ORDER BY j.name, js.step_id;
GO