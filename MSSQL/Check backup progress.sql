-- Check backup progress
SELECT 
    r.command,
    r.status,
    r.percent_complete,
    r.start_time,
    r.estimated_completion_time/1000 AS est_seconds_remaining,
    r.total_elapsed_time/1000 AS elapsed_seconds,
    d.name AS database_name
FROM sys.dm_exec_requests r
JOIN sys.databases d
    ON r.database_id = d.database_id
WHERE r.command LIKE 'BACKUP%';
