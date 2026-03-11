-- Central Server Setup - Step 3
-- Create alert sending procedure on INFRA-MGMT01

USE dba_db;
GO

IF OBJECT_ID('Monitoring.SP_SendAlerts', 'P') IS NOT NULL
    DROP PROCEDURE Monitoring.SP_SendAlerts;
GO

CREATE PROCEDURE Monitoring.SP_SendAlerts
    @EmailRecipient NVARCHAR(256) = '559c4de8.cube.global@emea.teams.ms',
    @MailProfile NVARCHAR(256) = 'SQLAlerts'
AS
BEGIN
    SET NOCOUNT ON;
    
    DECLARE @FailedJobCount INT;
    DECLARE @AlertBody NVARCHAR(MAX);
    DECLARE @AlertSubject NVARCHAR(256);
    
    BEGIN TRY
        -- Get count of active failed job alerts that haven't been notified in the last 50 minutes
        -- (job runs hourly; 50-min window prevents duplicate emails on manual reruns)
        SELECT @FailedJobCount = COUNT(*)
        FROM Monitoring.FailedJobsAlerts
        WHERE IsResolved = 0
            AND (AlertSentTime IS NULL OR AlertSentTime < DATEADD(MINUTE, -50, GETDATE()));
        
        IF @FailedJobCount > 0
        BEGIN
            SET @AlertSubject = 'SQL Server Job Alert - ' + CAST(@FailedJobCount AS NVARCHAR(10)) + ' Failed Jobs Detected';
            
            SET @AlertBody = 'Active Failed Jobs Alert' + CHAR(10) + CHAR(10);
            SET @AlertBody = @AlertBody + 'The following SQL Server Agent jobs are currently failing:' + CHAR(10) + CHAR(10);
            
            SELECT @AlertBody = @AlertBody + 
                'Server: ' + ServerName + CHAR(10) +
                'Job: ' + JobName + CHAR(10) +
                'First Failure: ' + FORMAT(FirstFailureTime, 'yyyy-MM-dd HH:mm:ss') + CHAR(10) +
                'Last Failure: ' + FORMAT(LastFailureTime, 'yyyy-MM-dd HH:mm:ss') + CHAR(10) +
                'Failure Count (last hour): ' + CAST(FailureCount AS NVARCHAR(10)) + CHAR(10) + CHAR(10)
            FROM Monitoring.FailedJobsAlerts
            WHERE IsResolved = 0
                AND (AlertSentTime IS NULL OR AlertSentTime < DATEADD(MINUTE, -50, GETDATE()))
            ORDER BY ServerName, LastFailureTime DESC;
            
            -- Send email using SQL Server Mail
            EXEC msdb.dbo.sp_send_dbmail
                @profile_name = @MailProfile,
                @recipients = @EmailRecipient,
                @subject = @AlertSubject,
                @body = @AlertBody,
                @body_format = 'TEXT';
            
            -- Update AlertSentTime for notified alerts
            UPDATE Monitoring.FailedJobsAlerts
            SET AlertSentTime = GETDATE()
            WHERE IsResolved = 0
                AND (AlertSentTime IS NULL OR AlertSentTime < DATEADD(MINUTE, -50, GETDATE()));
            
            PRINT 'Email alert sent successfully to: ' + @EmailRecipient;
        END
        ELSE
        BEGIN
            PRINT 'No failed jobs to alert on.';
        END
        
    END TRY
    BEGIN CATCH
        DECLARE @ErrorMessage NVARCHAR(MAX) = ERROR_MESSAGE();
        DECLARE @ErrorNumber INT = ERROR_NUMBER();
        DECLARE @ErrorSeverity INT = ERROR_SEVERITY();
        DECLARE @ErrorState INT = ERROR_STATE();
        
        PRINT 'ERROR in Monitoring.SP_SendAlerts:';
        PRINT 'Error Number: ' + CAST(@ErrorNumber AS NVARCHAR(10));
        PRINT 'Error Message: ' + @ErrorMessage;
        
        RAISERROR (@ErrorMessage, @ErrorSeverity, @ErrorState);
    END CATCH
END
GO

PRINT 'Stored procedure Monitoring.SP_SendAlerts created successfully.';
