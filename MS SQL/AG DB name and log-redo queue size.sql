-- AG DB name and log\redo queue size

select ag.name as ServeName, adc.database_name, drs.is_local,
drs.synchronization_state_desc, drs.synchronization_health_desc,
(drs.log_send_queue_size / 1024) as log_queue_size_mb,
(drs.redo_queue_size / 1024) as redo_queue_size_mb
FROM sys.dm_hadr_database_replica_states AS drs
INNER JOIN sys.availability_groups AS ag ON drs.group_id = ag.group_id
INNER JOIN sys.availability_databases_cluster AS adc ON drs.group_id = adc.group_id
--WHERE drs.is_local = 1

---

$Computer = $env:COMPUTERNAME
$MainAg = "US-ROL-A02-SG"

Get-ChildItem "SQLSERVER:\Sql\$Computer\DEFAULT\AvailabilityGroups" | Test-SqlAvailabilityGroup
Get-ChildItem "SQLSERVER:\Sql\$Computer\DEFAULT\AvailabilityGroups\$MainAg\AvailabilityReplicas" | Test-SqlAvailabilityReplica
Get-ChildItem "SQLSERVER:\Sql\$Computer\DEFAULT\AvailabilityGroups\$MainAg\DatabaseReplicaStates" | Test-SqlDatabaseReplicaState