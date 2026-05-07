/*
    LOG CHAIN AFTER COMPLETION TIME (PRINT ONLY)
    - Builds RESTORE LOG commands after a given completion timestamp
    - Generates one DISK path per LOG backup set
    - Prints only (does not execute restores)
*/

SET NOCOUNT ON;

DECLARE
    @DatabaseName sysname = N'cube_rm_live',
    @CompletionTime datetime2(7) = '2026-05-06T16:15:34.0581067', -- SSMS completion time
    @msg nvarchar(MAX) = N'';

DECLARE @LogBackups TABLE (
    BackupSetId int NOT NULL,
    MediaSetId int NOT NULL,
    BackupStart datetime NOT NULL,
    BackupFinish datetime NOT NULL
);

INSERT INTO @LogBackups (BackupSetId, MediaSetId, BackupStart, BackupFinish)
SELECT
    bs.backup_set_id,
    bs.media_set_id,
    bs.backup_start_date,
    bs.backup_finish_date
FROM msdb.dbo.backupset bs
WHERE bs.database_name = @DatabaseName
  AND bs.type = 'L'
  AND bs.backup_finish_date > @CompletionTime
ORDER BY bs.backup_start_date, bs.backup_finish_date, bs.backup_set_id;

SET @msg = @msg
  + N'/* RESTORE LOG CHAIN AFTER COMPLETION TIME */' + CHAR(10)
  + N'-- Database: ' + QUOTENAME(@DatabaseName) + CHAR(10)
  + N'-- Completion time: ' + CONVERT(nvarchar(33), @CompletionTime, 126) + CHAR(10)
  + N'-- LOG backups selected: ' + CAST((SELECT COUNT(*) FROM @LogBackups) AS nvarchar(20)) + CHAR(10)
  + CHAR(10);

IF NOT EXISTS (SELECT 1 FROM @LogBackups)
BEGIN
    SET @msg = @msg + N'-- WARNING: No LOG backups found after completion time.' + CHAR(10);
END
ELSE
BEGIN
    DECLARE
        @BackupSetId int,
        @MediaSetId int,
        @BackupStart datetime,
        @BackupFinish datetime,
        @LogPath nvarchar(4000);

    DECLARE backup_cur CURSOR LOCAL FAST_FORWARD FOR
        SELECT BackupSetId, MediaSetId, BackupStart, BackupFinish
        FROM @LogBackups
        ORDER BY BackupStart, BackupFinish, BackupSetId;

    OPEN backup_cur;
    FETCH NEXT FROM backup_cur INTO @BackupSetId, @MediaSetId, @BackupStart, @BackupFinish;

    WHILE @@FETCH_STATUS = 0
    BEGIN
        SELECT TOP (1)
            @LogPath = bmf.physical_device_name
        FROM msdb.dbo.backupmediafamily bmf
        WHERE bmf.media_set_id = @MediaSetId
        ORDER BY bmf.family_sequence_number DESC;

        IF @LogPath IS NOT NULL
        BEGIN
            SET @msg = @msg
                + N'-- LOG backup set id: ' + CAST(@BackupSetId AS nvarchar(20))
                + N' (start=' + CONVERT(nvarchar(30), @BackupStart, 120)
                + N', finish=' + CONVERT(nvarchar(30), @BackupFinish, 120) + N')' + CHAR(10)
                + N'RESTORE LOG ' + QUOTENAME(@DatabaseName) + CHAR(10)
                + N'    FROM DISK = N''' + REPLACE(@LogPath, '''', '''''') + N'''' + CHAR(10)
                + N'    WITH NORECOVERY, REPLACE, STATS = 10;' + CHAR(10) + CHAR(10);
        END
        ELSE
        BEGIN
            SET @msg = @msg
                + N'-- WARNING: No media files found for LOG backup_set_id='
                + CAST(@BackupSetId AS nvarchar(20)) + N'.' + CHAR(10);
        END

            SET @LogPath = NULL;

        FETCH NEXT FROM backup_cur INTO @BackupSetId, @MediaSetId, @BackupStart, @BackupFinish;
    END

    CLOSE backup_cur;
    DEALLOCATE backup_cur;
END

SET @msg = @msg
  + N'-- After applying these logs, DB remains in RESTORING (NORECOVERY).' + CHAR(10);

DECLARE
    @remaining nvarchar(MAX) = REPLACE(@msg, CHAR(13), N''),
    @line nvarchar(MAX),
    @newlinePos int;

WHILE LEN(@remaining) > 0
BEGIN
    SET @newlinePos = CHARINDEX(CHAR(10), @remaining);

    IF @newlinePos = 0
    BEGIN
        SET @line = @remaining;
        SET @remaining = N'';
    END
    ELSE
    BEGIN
        SET @line = SUBSTRING(@remaining, 1, @newlinePos - 1);
        SET @remaining = SUBSTRING(@remaining, @newlinePos + 1, LEN(@remaining));
    END

    WHILE LEN(@line) > 4000
    BEGIN
        PRINT LEFT(@line, 4000);
        SET @line = SUBSTRING(@line, 4001, LEN(@line));
    END

    PRINT @line;
END;
