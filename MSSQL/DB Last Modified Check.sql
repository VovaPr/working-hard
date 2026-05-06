DECLARE @DBName NVARCHAR(255)
CREATE TABLE #TempDBs (DatabaseName NVARCHAR(255), LastModifiedDate DATETIME)

DECLARE db_cursor CURSOR FOR
SELECT name
FROM sys.databases
WHERE state_desc = 'ONLINE'

OPEN db_cursor
FETCH NEXT FROM db_cursor INTO @DBName

WHILE @@FETCH_STATUS = 0
BEGIN
    DECLARE @sql NVARCHAR(MAX)
    SET @sql = 'USE [' + @DBName + ']; ' +
               'INSERT INTO #TempDBs (DatabaseName, LastModifiedDate) ' +
               'SELECT DB_NAME(), MAX(modify_date) FROM sys.tables'
    EXEC sp_executesql @sql
    FETCH NEXT FROM db_cursor INTO @DBName
END

CLOSE db_cursor
DEALLOCATE db_cursor

SELECT DatabaseName, LastModifiedDate
FROM #TempDBs
ORDER BY LastModifiedDate DESC

DROP TABLE #TempDBs