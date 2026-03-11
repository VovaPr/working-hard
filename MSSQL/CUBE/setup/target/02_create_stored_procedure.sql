-- Target Server Setup - Step 2
-- Create monitoring stored procedure on each target server
-- Collects local job statuses and populates local alert table

USE dba_db;
GO

IF OBJECT_ID('Monitoring.SP_MonitoringJobs', 'P') IS NOT NULL
    DROP PROCEDURE Monitoring.SP_MonitoringJobs;
GO

CREATE PROCEDURE Monitoring.SP_MonitoringJobs
AS
BEGIN
    SET NOCOUNT ON;
    
    DECLARE @ServerName NVARCHAR(256) = @@SERVERNAME;
    
    BEGIN TRY
        -- Step 1: Collect Job Status from msdb
        MERGE INTO Monitoring.Jobs j
        USING (
            SELECT 
                @ServerName AS ServerName,
                sj.name AS JobName,
                sj.job_id AS SQLJobID,
                sjh.run_status AS LastRunStatus,
                sjh.LastRunDate AS LastRunDate,
                sjh.run_duration AS LastRunDuration,
                nr.NextRunDate AS NextRunDate,
                sj.enabled AS IsEnabled
            FROM msdb.dbo.sysjobs sj
            LEFT JOIN (
                SELECT 
                    job_id,
                    run_status,
                    run_duration,
                    msdb.dbo.agent_datetime(run_date, run_time) AS LastRunDate,
                    ROW_NUMBER() OVER (PARTITION BY job_id ORDER BY instance_id DESC) as rn
                FROM msdb.dbo.sysjobhistory
                WHERE step_id = 0
            ) sjh ON sj.job_id = sjh.job_id AND sjh.rn = 1
            OUTER APPLY (
                SELECT TOP (1)
                    CASE
                        WHEN sjs.next_run_date > 0
                            THEN msdb.dbo.agent_datetime(sjs.next_run_date, sjs.next_run_time)
                        ELSE NULL
                    END AS NextRunDate
                FROM msdb.dbo.sysjobschedules sjs
                WHERE sjs.job_id = sj.job_id
                ORDER BY sjs.next_run_date, sjs.next_run_time
            ) nr
        ) src
            ON j.ServerName = src.ServerName AND j.JobName = src.JobName
        WHEN MATCHED THEN
            UPDATE SET 
                LastRunStatus = src.LastRunStatus,
                LastRunDate = src.LastRunDate,
                LastRunDuration = src.LastRunDuration,
                NextRunDate = src.NextRunDate,
                IsEnabled = src.IsEnabled,
                RecordedDate = GETDATE()
        WHEN NOT MATCHED THEN
            INSERT (ServerName, JobName, SQLJobID, LastRunStatus, LastRunDate, LastRunDuration, NextRunDate, IsEnabled, RecordedDate)
            VALUES (src.ServerName, src.JobName, src.SQLJobID, src.LastRunStatus, src.LastRunDate, src.LastRunDuration, src.NextRunDate, src.IsEnabled, GETDATE());
        
        PRINT 'Job status collection completed successfully on ' + @ServerName;
        
        -- Step 2: Check for Failed Jobs and Create/Update Alerts
        WITH FailedJobs AS (
            SELECT 
                ServerName,
                JobName,
                COUNT(*) as FailureCount,
                MAX(RecordedDate) as LastFailureTime
            FROM Monitoring.Jobs
            WHERE ServerName = @ServerName
                AND LastRunStatus = 0
                AND LastRunDate >= DATEADD(HOUR, -1, GETDATE())
            GROUP BY ServerName, JobName
        )
        MERGE INTO Monitoring.FailedJobsAlerts fa
        USING FailedJobs fj
            ON fa.ServerName = fj.ServerName 
            AND fa.JobName = fj.JobName
            AND fa.IsResolved = 0
        WHEN MATCHED THEN
            UPDATE SET 
                FailureCount = fa.FailureCount + fj.FailureCount,
                LastFailureTime = fj.LastFailureTime
        WHEN NOT MATCHED THEN
            INSERT (ServerName, JobName, FailureCount, FirstFailureTime, LastFailureTime)
            VALUES (fj.ServerName, fj.JobName, fj.FailureCount, fj.LastFailureTime, fj.LastFailureTime);
        
        -- Mark alerts as resolved if jobs are now running successfully
        UPDATE Monitoring.FailedJobsAlerts
        SET IsResolved = 1, ResolutionTime = GETDATE()
        WHERE IsResolved = 0
            AND ServerName = @ServerName
            AND NOT EXISTS (
                SELECT 1 FROM Monitoring.Jobs js
                WHERE js.ServerName = Monitoring.FailedJobsAlerts.ServerName
                    AND js.JobName = Monitoring.FailedJobsAlerts.JobName
                    AND js.LastRunStatus = 0
                    AND js.LastRunDate >= DATEADD(HOUR, -1, GETDATE())
            );
        
        PRINT 'Failed job analysis completed successfully.';
        
    END TRY
    BEGIN CATCH
        DECLARE @ErrorMessage NVARCHAR(MAX) = ERROR_MESSAGE();
        DECLARE @ErrorNumber INT = ERROR_NUMBER();
        DECLARE @ErrorSeverity INT = ERROR_SEVERITY();
        DECLARE @ErrorState INT = ERROR_STATE();
        
        PRINT 'ERROR in Monitoring.SP_MonitoringJobs:';
        PRINT 'Error Number: ' + CAST(@ErrorNumber AS NVARCHAR(10));
        PRINT 'Error Message: ' + @ErrorMessage;
        
        RAISERROR (@ErrorMessage, @ErrorSeverity, @ErrorState);
    END CATCH
END
GO

PRINT 'Stored procedure Monitoring.SP_MonitoringJobs created on target server.';
