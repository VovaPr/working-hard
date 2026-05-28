-- Report synchronization state for all AG databases
SELECT 
    ag.name AS AGName,
    DB_NAME(drs.database_id) AS DatabaseName,
    ar.replica_server_name,
    ars.role_desc,
    drs.synchronization_state_desc,
    drs.synchronization_health_desc,
    drs.is_suspended,
    drs.suspend_reason_desc,
    drs.last_commit_time,
    drs.last_hardened_lsn,
    drs.end_of_log_lsn
FROM sys.dm_hadr_database_replica_states AS drs
JOIN sys.availability_replicas AS ar
    ON drs.replica_id = ar.replica_id
JOIN sys.dm_hadr_availability_replica_states AS ars
    ON ar.replica_id = ars.replica_id
JOIN sys.availability_groups AS ag
    ON ar.group_id = ag.group_id
ORDER BY ag.name, DatabaseName;

-- Example: Resume synchronization for a specific database
-- Run this only if is_suspended = 1 and the database is healthy
-- ALTER DATABASE [cube_rdi_live] SET HADR RESUME;
