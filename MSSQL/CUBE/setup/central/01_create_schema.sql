-- Central Server Setup - Step 1
-- Create monitoring schema and tables on INFRA-MGMT01
-- The dba_db database must already exist

USE dba_db;
GO

-- Create schema for monitoring
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'Monitoring')
BEGIN
    EXEC sp_executesql N'CREATE SCHEMA Monitoring';
END
GO

-- Current Job Status Table
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'Jobs' AND schema_id = SCHEMA_ID('Monitoring'))
BEGIN
    CREATE TABLE Monitoring.Jobs (
        JobID INT PRIMARY KEY IDENTITY(1,1),
        ServerName NVARCHAR(256) NOT NULL,
        JobName NVARCHAR(256) NOT NULL,
        SQLJobID UNIQUEIDENTIFIER,
        LastRunStatus INT,
        LastRunDate DATETIME2,
        LastRunDuration INT,
        NextRunDate DATETIME2,
        IsEnabled BIT,
        RecordedDate DATETIME2 DEFAULT GETDATE(),
        UNIQUE (ServerName, JobName)
    );
END
GO

-- Failed Jobs Alert Log
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'FailedJobsAlerts' AND schema_id = SCHEMA_ID('Monitoring'))
BEGIN
    CREATE TABLE Monitoring.FailedJobsAlerts (
        AlertID INT PRIMARY KEY IDENTITY(1,1),
        ServerName NVARCHAR(256) NOT NULL,
        JobName NVARCHAR(256) NOT NULL,
        FailureCount INT DEFAULT 1,
        FirstFailureTime DATETIME2 DEFAULT GETDATE(),
        LastFailureTime DATETIME2 DEFAULT GETDATE(),
        AlertSentTime DATETIME2,
        IsResolved BIT DEFAULT 0,
        ResolutionTime DATETIME2
    );
END
GO

-- Monitored target servers list (used by central job extensions)
-- Legacy support: if dbo.MonitoredServers exists, move it into Monitoring schema.
IF OBJECT_ID('Monitoring.MonitoredServers', 'U') IS NULL
BEGIN
    IF OBJECT_ID('dbo.MonitoredServers', 'U') IS NOT NULL
    BEGIN
        EXEC('ALTER SCHEMA Monitoring TRANSFER dbo.MonitoredServers');
    END
    ELSE
    BEGIN
        CREATE TABLE Monitoring.MonitoredServers (
            ServerName SYSNAME NOT NULL PRIMARY KEY,
            Port INT NOT NULL CONSTRAINT DF_MonitoredServers_Port DEFAULT (1433),
            IsActive BIT NOT NULL DEFAULT 1,
            CreatedAt DATETIME2 NOT NULL DEFAULT GETDATE(),
            UpdatedAt DATETIME2 NULL
        );
    END
END

-- Backward compatibility: if table existed without Port, add it.
IF OBJECT_ID('Monitoring.MonitoredServers', 'U') IS NOT NULL
    AND COL_LENGTH('Monitoring.MonitoredServers', 'Port') IS NULL
BEGIN
    ALTER TABLE Monitoring.MonitoredServers
        ADD Port INT NOT NULL CONSTRAINT DF_MonitoredServers_Port DEFAULT (1433) WITH VALUES;
END
GO

-- Create indexes
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Jobs_ServerName')
    CREATE INDEX IX_Jobs_ServerName ON Monitoring.Jobs(ServerName);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Jobs_LastRunDate')
    CREATE INDEX IX_Jobs_LastRunDate ON Monitoring.Jobs(LastRunDate);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_FailedJobsAlerts_AlertSentTime')
    CREATE INDEX IX_FailedJobsAlerts_AlertSentTime ON Monitoring.FailedJobsAlerts(AlertSentTime);

PRINT 'Central monitoring schema created successfully.';
