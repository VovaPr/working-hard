-- Shows recent operation status entries for the distribution database, including progress, state, and error details.

SELECT
    session_activity_id,
    major_resource_id,
    operation,
    state_desc,
    percent_complete,
    error_code,
    error_desc,
    start_time,
    last_modify_time
FROM sys.dm_operation_status
WHERE major_resource_id = 'distribution'
ORDER BY start_time DESC;