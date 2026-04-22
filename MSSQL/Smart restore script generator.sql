/*
    SMART RESTORE SCRIPT GENERATOR (PRINT ONLY)
    - Auto-picks FULL for the specified database:
        * Always picks latest FULL
    - Optionally uses latest DIFF after that FULL (@PreferDiff = 1)
    - Builds complete LOG chain after the chosen base (DIFF or FULL)
    - Handles multi-file FULL/DIFF media sets (no trailing comma)
    - Prints only (does not execute restores)

    Author: Volodymyr-friendly version
*/

SET NOCOUNT ON;

DECLARE 
    @DatabaseName sysname       = N'RegAudit',                 -- <== change as needed
    @PreferDiff bit             = 1;                          -- 1: include DIFF if available; 0: skip DIFF

DECLARE 
    @FullMediaSetID int,
    @FullStart datetime,
    @FullFinish datetime,
    @DiffMediaSetID int,
    @DiffStart datetime,
    @DiffFinish datetime,
    @BaseStart datetime,       -- base for logs (DIFF if used, else FULL)
    @msg nvarchar(MAX) = N'';

/* ------------------------------------------------------------------
   1) Choose the FULL backup for the database
-------------------------------------------------------------------- */
;WITH Fulls AS (
    SELECT 
        bs.media_set_id,
        bs.backup_start_date,
        bs.backup_finish_date
    FROM msdb.dbo.backupset bs
    WHERE bs.database_name = @DatabaseName
      AND bs.type = 'D'
)
SELECT TOP (1)
    @FullMediaSetID = media_set_id,
    @FullStart      = backup_start_date,
    @FullFinish     = backup_finish_date
FROM Fulls
ORDER BY backup_finish_date DESC;

IF @FullMediaSetID IS NULL
BEGIN
    PRINT 'ERROR: No FULL backup found for database ' + QUOTENAME(@DatabaseName)
        + ' (latest FULL lookup returned none).';

    PRINT 'HINT: Available FULL backups for this database:';
    SELECT TOP (25)
        bs.backup_start_date AS full_start,
        bs.backup_finish_date AS full_finish
    FROM msdb.dbo.backupset bs
    WHERE bs.database_name = @DatabaseName
      AND bs.type = 'D'
    ORDER BY bs.backup_finish_date DESC;
    RETURN;
END;

/* ------------------------------------------------------------------
   2) If requested, locate the latest DIFF after that FULL
-------------------------------------------------------------------- */
IF @PreferDiff = 1
BEGIN
    ;WITH Diffs AS (
        SELECT 
            bs.media_set_id,
            bs.backup_start_date,
            bs.backup_finish_date,
            ROW_NUMBER() OVER (ORDER BY bs.backup_finish_date DESC) AS rn
        FROM msdb.dbo.backupset bs
        WHERE bs.database_name = @DatabaseName
          AND bs.type = 'I'
          AND bs.backup_start_date > @FullStart
    )
    SELECT TOP (1)
        @DiffMediaSetID = media_set_id,
        @DiffStart      = backup_start_date,
        @DiffFinish     = backup_finish_date
    FROM Diffs
    ORDER BY rn;

    -- If no DIFF exists, we’ll just use the FULL as base
END

SET @BaseStart = COALESCE(@DiffStart, @FullStart);

/* ------------------------------------------------------------------
   3) Collect FULL media family files in order (multi-file-aware)
-------------------------------------------------------------------- */
DECLARE @FullFiles TABLE (
    Seq int NOT NULL,
    FilePath nvarchar(4000) NOT NULL
);

INSERT INTO @FullFiles (Seq, FilePath)
SELECT 
    bmf.family_sequence_number,
    bmf.physical_device_name
FROM msdb.dbo.backupmediafamily bmf
WHERE bmf.media_set_id = @FullMediaSetID
ORDER BY bmf.family_sequence_number;

IF NOT EXISTS (SELECT 1 FROM @FullFiles)
BEGIN
    PRINT 'ERROR: No media family files found for the chosen FULL backup (media_set_id=' + CAST(@FullMediaSetID AS nvarchar(20)) + ').';
    RETURN;
END;

DECLARE @FullFromClause nvarchar(MAX) = N'FROM' + CHAR(10);
;WITH f AS (
    SELECT 
        Seq,
        FilePath,
        ROW_NUMBER() OVER (ORDER BY Seq) AS rn,
        COUNT(*)     OVER () AS cnt
    FROM @FullFiles
)
SELECT @FullFromClause = @FullFromClause +
       N'    DISK = N''' + REPLACE(FilePath, '''', '''''') + N'''' +
       CASE WHEN rn < cnt THEN N',' ELSE N'' END + CHAR(10)
FROM f;

/* ------------------------------------------------------------------
   4) (Optional) Collect DIFF media family file (usually single file)
-------------------------------------------------------------------- */
DECLARE @DiffFromClause nvarchar(MAX) = NULL;

IF @DiffMediaSetID IS NOT NULL
BEGIN
    DECLARE @DiffFiles TABLE (Seq int NOT NULL, FilePath nvarchar(4000) NOT NULL);
    INSERT INTO @DiffFiles (Seq, FilePath)
    SELECT bmf.family_sequence_number, bmf.physical_device_name
    FROM msdb.dbo.backupmediafamily bmf
    WHERE bmf.media_set_id = @DiffMediaSetID
    ORDER BY bmf.family_sequence_number;

    IF EXISTS (SELECT 1 FROM @DiffFiles)
    BEGIN
        SET @DiffFromClause = N'FROM' + CHAR(10);
        ;WITH d AS (
            SELECT 
                Seq,
                FilePath,
                ROW_NUMBER() OVER (ORDER BY Seq) AS rn,
                COUNT(*)     OVER () AS cnt
            FROM @DiffFiles
        )
        SELECT @DiffFromClause = @DiffFromClause +
               N'    DISK = N''' + REPLACE(FilePath, '''', '''''') + N'''' +
               CASE WHEN rn < cnt THEN N',' ELSE N'' END + CHAR(10)
        FROM d;
    END
END

/* ------------------------------------------------------------------
   5) Gather all LOG backups AFTER @BaseStart (DIFF if used, else FULL)
-------------------------------------------------------------------- */
DECLARE @Logs TABLE (
    LogStart datetime NOT NULL,
    LogPath  nvarchar(4000) NOT NULL
);

INSERT INTO @Logs (LogStart, LogPath)
SELECT 
    bs.backup_start_date,
    bmf.physical_device_name
FROM msdb.dbo.backupset bs
JOIN msdb.dbo.backupmediafamily bmf
    ON bs.media_set_id = bmf.media_set_id
WHERE bs.database_name = @DatabaseName
  AND bs.type = 'L'
  AND bs.backup_start_date > @BaseStart
ORDER BY bs.backup_start_date;

/* ------------------------------------------------------------------
   6) Compose the PRINT-only restore script
-------------------------------------------------------------------- */

SET @msg = @msg 
  + N'/* RESTORE SCRIPT FOR SECONDARY REPLICA */' + CHAR(10)
  + N'-- Database: ' + QUOTENAME(@DatabaseName) + CHAR(10)
  + N'-- FULL start: ' + CONVERT(nvarchar(30), @FullStart, 120) + CHAR(10)
  + CASE WHEN @DiffMediaSetID IS NOT NULL 
         THEN N'-- DIFF start: ' + CONVERT(nvarchar(30), @DiffStart, 120) + CHAR(10)
         ELSE N'' END
  + N'-- Base for LOG chain: ' + CONVERT(nvarchar(30), @BaseStart, 120) + CHAR(10)
  + CHAR(10);

-- FULL restore
SET @msg = @msg 
  + N'-- 1) RESTORE FULL' + CHAR(10)
  + N'RESTORE DATABASE ' + QUOTENAME(@DatabaseName) + CHAR(10)
  + @FullFromClause
  + N'WITH NORECOVERY, REPLACE, STATS = 10;' + CHAR(10) + CHAR(10);

-- DIFF restore (if present and preferred)
IF @DiffMediaSetID IS NOT NULL AND @PreferDiff = 1 AND @DiffFromClause IS NOT NULL
BEGIN
    SET @msg = @msg
      + N'-- 2) RESTORE DIFFERENTIAL' + CHAR(10)
      + N'RESTORE DATABASE ' + QUOTENAME(@DatabaseName) + CHAR(10)
      + @DiffFromClause
      + N'WITH NORECOVERY, STATS = 10;' + CHAR(10) + CHAR(10);
END

-- LOG chain
SET @msg = @msg + CASE WHEN @DiffMediaSetID IS NOT NULL AND @PreferDiff = 1
                       THEN N'-- 3) RESTORE LOG CHAIN'
                       ELSE N'-- 2) RESTORE LOG CHAIN'
                  END + N' (chronological order)' + CHAR(10);

DECLARE @LogStart datetime, @LogPath nvarchar(4000);

DECLARE log_cur CURSOR LOCAL FAST_FORWARD FOR
    SELECT LogStart, LogPath FROM @Logs ORDER BY LogStart;

OPEN log_cur;
FETCH NEXT FROM log_cur INTO @LogStart, @LogPath;

WHILE @@FETCH_STATUS = 0
BEGIN
    SET @msg = @msg
        + N'RESTORE LOG ' + QUOTENAME(@DatabaseName) + CHAR(10)
        + N'    FROM DISK = N''' + REPLACE(@LogPath, '''', '''''') + N'''' + CHAR(10)
        + N'    WITH NORECOVERY, STATS = 5;' + CHAR(10);
    FETCH NEXT FROM log_cur INTO @LogStart, @LogPath;
END

CLOSE log_cur;
DEALLOCATE log_cur;

-- Footer
SET @msg = @msg + CHAR(10)
    + N'-- After all restores, DB remains in RESTORING (required for AG join).' + CHAR(10)
    + N'-- Then run on the secondary:' + CHAR(10)
    + N'-- ALTER DATABASE ' + QUOTENAME(@DatabaseName) + N' SET HADR AVAILABILITY GROUP = CUBEPREP2AG07;' + CHAR(10);

-- If no logs found, warn (still okay if you intend to catch-up by additional logs later)
IF NOT EXISTS (SELECT 1 FROM @Logs)
BEGIN
    SET @msg = @msg + CHAR(10) + N'-- WARNING: No LOG backups found after base. If the primary is still generating logs, take a new LOG backup and restore it.';
END

/* ------------------------------------------------------------------
   7) PRINT in chunks (avoid SSMS truncation)
-------------------------------------------------------------------- */
DECLARE @i int = 1, @len int = LEN(@msg), @chunk nvarchar(4000);
WHILE @i <= @len
BEGIN
    SET @chunk = SUBSTRING(@msg, @i, 4000);
    PRINT @chunk;
    SET @i += 4000;
END;