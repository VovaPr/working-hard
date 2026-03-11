-- Central Server Rollback - Step 3
-- Drops Monitoring.SP_SendAlerts from dba_db on INFRA-MGMT01

USE dba_db;
GO

IF OBJECT_ID('Monitoring.SP_SendAlerts', 'P') IS NOT NULL
BEGIN
    DROP PROCEDURE Monitoring.SP_SendAlerts;
    PRINT 'Procedure Monitoring.SP_SendAlerts dropped.';
END
ELSE
    PRINT 'Procedure Monitoring.SP_SendAlerts does not exist, nothing to drop.';
GO

PRINT 'Central rollback step 3 complete.';
