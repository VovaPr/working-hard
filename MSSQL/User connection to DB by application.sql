USE master;
GO

SELECT
               DB_NAME(dbid) as DBName,
               COUNT(dbid) as NumberOfConnections,
               ss.loginame as LoginName,
               ss.hostname HostName,
               ss.program_name as ProgramName
FROM
               sys.sysprocesses ss
WHERE
               dbid > 4
GROUP BY dbid, ss.loginame , ss.hostname, ss.program_name
order by count(dbid) desc;

-----------------------------------------------------------------------------------------

USE master;
GO

Select count(*)
from sysprocesses
where spid > 50