USE msdb;
GO
-- Disable jobs starting with 'DatabaseBackup'
UPDATE j
SET j.enabled = 0
FROM msdb.dbo.sysjobs j
WHERE j.name LIKE 'DatabaseBackup%';

USE msdb;
GO
-- Enable jobs starting with 'DatabaseBackup'
UPDATE j
SET j.enabled = 1
FROM msdb.dbo.sysjobs j
WHERE j.name LIKE 'DatabaseBackup%';
