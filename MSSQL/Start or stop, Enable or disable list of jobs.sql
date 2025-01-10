--------------------
--Stop and Disable--
--------------------

--stop jobs
USE msdb;
GO
DECLARE @Job as VARCHAR (100);
SET @Job = 'Ops - Update Statistics for top 10 Tables';
IF EXISTS (select job_id from msdb.dbo.sysjobs WHERE name = @Job)
BEGIN
       EXEC dbo.sp_stop_job @Job;
END
--start jobs
USE msdb;
GO
DECLARE @Job as VARCHAR (100);
SET @Job = 'Ops - Update Statistics for top 10 Tables';
IF EXISTS (select job_id from msdb.dbo.sysjobs WHERE name = @Job)
BEGIN
       EXEC dbo.sp_start_job @Job;
END

------------------
--Run and Enable--
------------------

--disable jobs
USE msdb;
GO
EXEC dbo.sp_update_job
    @job_name = N'Ops - Backup Transaction Logs',  @enabled = 0;
GO
EXEC dbo.sp_update_job
    @job_name = N'Ops - Backup Differential Database',  @enabled = 0;
GO
EXEC dbo.sp_update_job
    @job_name = N'Ops - Backup Full Database',  @enabled = 0;
GO
--enable jobs
USE msdb; 
GO 
EXEC dbo.sp_update_job 
    @job_name = N'Ops - Backup Transaction Logs',  @enabled = 1; 
GO
EXEC dbo.sp_update_job 
    @job_name = N'Ops - Backup Differential Database',  @enabled = 1; 
GO
EXEC dbo.sp_update_job 
    @job_name = N'Ops - Backup Full Database',  @enabled = 1; 
GO

------------
--Generate--
------------

--generate disable
USE msdb; 
GO 
SELECT 'exec msdb..sp_update_job @job_name = '''+NAME+''', @enabled = 0' FROM msdb..sysjobs
where enabled <> 0
--generate enable
USE msdb; 
GO 
SELECT 'exec msdb..sp_update_job @job_name = '''+NAME+''', @enabled = 1' FROM msdb..sysjobs
where enabled <> 1