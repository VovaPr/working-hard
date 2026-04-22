/*
    LOG CHAIN AFTER COMPLETION TIME (PRINT ONLY)
    - Builds RESTORE LOG commands after a given completion timestamp
    - Handles multi-file LOG media sets (striped backups)
    - Prints only (does not execute restores)
*/

SET NOCOUNT ON;

DECLARE
    @DatabaseName sysname = N'ProfileQueue',
    @CompletionTime datetime2(7) = '2026-04-22T16:15:34.0581067', -- SSMS completion time
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
        @FromClause nvarchar(MAX);

    DECLARE backup_cur CURSOR LOCAL FAST_FORWARD FOR
        SELECT BackupSetId, MediaSetId, BackupStart, BackupFinish
        FROM @LogBackups
        ORDER BY BackupStart, BackupFinish, BackupSetId;

    OPEN backup_cur;
    FETCH NEXT FROM backup_cur INTO @BackupSetId, @MediaSetId, @BackupStart, @BackupFinish;

    WHILE @@FETCH_STATUS = 0
    BEGIN
        DECLARE @Files TABLE (
            Seq int NOT NULL,
            FilePath nvarchar(4000) NOT NULL
        );

        INSERT INTO @Files (Seq, FilePath)
        SELECT
            bmf.family_sequence_number,
            bmf.physical_device_name
        FROM msdb.dbo.backupmediafamily bmf
        WHERE bmf.media_set_id = @MediaSetId
        ORDER BY bmf.family_sequence_number;

        IF EXISTS (SELECT 1 FROM @Files)
        BEGIN
            SET @FromClause = N'FROM' + CHAR(10);

            ;WITH f AS (
                SELECT
                    Seq,
                    FilePath,
                    ROW_NUMBER() OVER (ORDER BY Seq) AS rn,
                    COUNT(*) OVER () AS cnt
                FROM @Files
            )
            SELECT @FromClause = @FromClause
                + N'    DISK = N''' + REPLACE(FilePath, '''', '''''') + N''''
                + CASE WHEN rn < cnt THEN N',' ELSE N'' END + CHAR(10)
            FROM f;

            SET @msg = @msg
                + N'-- LOG backup set id: ' + CAST(@BackupSetId AS nvarchar(20))
                + N' (start=' + CONVERT(nvarchar(30), @BackupStart, 120)
                + N', finish=' + CONVERT(nvarchar(30), @BackupFinish, 120) + N')' + CHAR(10)
                + N'RESTORE LOG ' + QUOTENAME(@DatabaseName) + CHAR(10)
                + @FromClause
                + N'WITH NORECOVERY, STATS = 5;' + CHAR(10) + CHAR(10);
        END
        ELSE
        BEGIN
            SET @msg = @msg
                + N'-- WARNING: No media files found for LOG backup_set_id='
                + CAST(@BackupSetId AS nvarchar(20)) + N'.' + CHAR(10);
        END

        FETCH NEXT FROM backup_cur INTO @BackupSetId, @MediaSetId, @BackupStart, @BackupFinish;
    END

    CLOSE backup_cur;
    DEALLOCATE backup_cur;
END

SET @msg = @msg
  + N'-- After applying these logs, DB remains in RESTORING (NORECOVERY).' + CHAR(10);

DECLARE @i int = 1, @len int = LEN(@msg), @chunk nvarchar(4000);
WHILE @i <= @len
BEGIN
    SET @chunk = SUBSTRING(@msg, @i, 4000);
    PRINT @chunk;
    SET @i += 4000;
END;
