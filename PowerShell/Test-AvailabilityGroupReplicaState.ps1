$Computer = $env:COMPUTERNAME
$MainAg = "US-ROL-A02-SG"

Get-ChildItem "SQLSERVER:\Sql\$Computer\DEFAULT\AvailabilityGroups" | Test-SqlAvailabilityGroup
Get-ChildItem "SQLSERVER:\Sql\$Computer\DEFAULT\AvailabilityGroups\$MainAg\AvailabilityReplicas" | Test-SqlAvailabilityReplica
Get-ChildItem "SQLSERVER:\Sql\$Computer\DEFAULT\AvailabilityGroups\$MainAg\DatabaseReplicaStates" | Test-SqlDatabaseReplicaState