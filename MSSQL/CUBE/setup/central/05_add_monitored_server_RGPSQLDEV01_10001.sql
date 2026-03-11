-- Utility script: add or update one monitored server on central server
-- Server: RGPSQLDEV01
-- Port:   10001

USE dba_db;
GO

DECLARE @ServerName SYSNAME = N'RGPSQLDEV01';
DECLARE @Port INT = 10001;

IF EXISTS (
    SELECT 1
    FROM Monitoring.MonitoredServers
    WHERE ServerName = @ServerName
)
BEGIN
    UPDATE Monitoring.MonitoredServers
    SET Port = @Port,
        IsActive = 1,
        UpdatedAt = GETDATE()
    WHERE ServerName = @ServerName;

    PRINT 'Updated Monitoring.MonitoredServers: ' + @ServerName + ':' + CAST(@Port AS NVARCHAR(10));
END
ELSE
BEGIN
    INSERT INTO Monitoring.MonitoredServers (ServerName, Port, IsActive)
    VALUES (@ServerName, @Port, 1);

    PRINT 'Inserted Monitoring.MonitoredServers: ' + @ServerName + ':' + CAST(@Port AS NVARCHAR(10));
END
GO

SELECT ServerName, Port, IsActive, CreatedAt, UpdatedAt
FROM Monitoring.MonitoredServers
WHERE ServerName = N'RGPSQLDEV01';
