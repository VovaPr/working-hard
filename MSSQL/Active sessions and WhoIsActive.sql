USE master;
GO

SELECT
    session_id,
    login_name,
    host_name,
    program_name,
    database_id,
    login_time
FROM sys.dm_exec_sessions
-- Optional filter:
-- WHERE login_name = 'shared-prep-neu-statelessaks-wid-01'
ORDER BY login_time DESC;

SELECT
    loginame,
    login_time
FROM sys.sysprocesses
ORDER BY login_time DESC;

USE dba_db;
GO

EXEC sp_WhoIsActive;
