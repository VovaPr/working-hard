USE master;
GO

DECLARE @TableName NVARCHAR(128) = 'Entity';
DECLARE @DatabaseName NVARCHAR(128);
DECLARE @TableCount INT;
DECLARE @EntityCount INT;
DECLARE @TableExists INT;
DECLARE @SQL NVARCHAR(MAX);
DECLARE @SQL2 NVARCHAR(MAX);
DECLARE @SQL3 NVARCHAR(MAX);
DECLARE @SPConnections NVARCHAR(MAX);
DECLARE @SPLD NVARCHAR(128);
DECLARE @SPProjectNAME NVARCHAR(128);
 
DECLARE DatabaseCursor CURSOR FOR
SELECT name
FROM sys.databases
WHERE database_id > 4 AND state_desc = 'ONLINE' AND is_read_only = 0; -- Excluding system databases and read-only databases
 
OPEN DatabaseCursor;
FETCH NEXT FROM DatabaseCursor INTO @DatabaseName;
 
PRINT 'START';
 
WHILE @@FETCH_STATUS = 0
 
BEGIN
SET @SQL = 'SELECT @TableCount = COUNT(*) FROM ' + QUOTENAME(@DatabaseName) + '.sys.tables  WHERE name = ''Entity'' AND SCHEMA_NAME(schema_id) = ''dbo''';
SET @SQL2 = 'SELECT @EntityCount = COUNT(*) FROM ' + QUOTENAME(@DatabaseName) + '.dbo.' + QUOTENAME(@TableName);
 
 
   BEGIN TRY
EXEC sp_executesql @SQL, N'@TableCount INT OUTPUT', @TableCount OUTPUT;
--set @TableExists = @@ROWCOUNT
 
IF @TableCount > 0
BEGIN
EXEC sp_executesql @SQL2, N'@TableName NVARCHAR(128), @EntityCount INT OUTPUT', @TableName, @EntityCount OUTPUT;
 
PRINT 'Database: ' + @DatabaseName + ', Record Count: ' + CAST(@EntityCount AS NVARCHAR(10));
 
END
ELSE
 
BEGIN
PRINT 'Table ' + @TableName + ' does not exist in database ' + @DatabaseName;
       END
 
   END TRY
   BEGIN CATCH
       PRINT 'Error occurred while querying table ' + @TableName + ' in database ' + @DatabaseName + ': ' + ERROR_MESSAGE();
   END CATCH;
 
   FETCH NEXT FROM DatabaseCursor INTO @DatabaseName;
set @EntityCount = 0;
END
 
CLOSE DatabaseCursor;
DEALLOCATE DatabaseCursor;
 
 
declare @cur cursor;
declare @data1 varchar(1000);
declare @data2 varchar(1000);
declare @data3 varchar(1000);
declare @i int = 0, @lastNum int, @rowNum int;
set @cur = cursor local static read_only for 
   select
        row_number() over (order by(select null)) as RowNum
       ,user_account, login_date, project_name
   from sk2012.sks_sys.sds_connection_log
where User_Account <> 'sks_admin' and login_date>=dateadd(day, -160, getdate()) 
   order by login_date desc
open @cur
begin try
   fetch last from @cur into @lastNum, @data1, @data2, @data3
   fetch absolute 1 from @cur into @rowNum, @data1,@data2, @data3  --start from the beginning and get first value 
   while @i < @lastNum
   begin
       set @i += 1
 
       --Do your job here
       print @data1 + '---' + @data2 + '---' + @data3;
 
       fetch next from @cur into @rowNum, @data1,@data2, @data3
   end
end try
begin catch
   close @cur      --|
   deallocate @cur --|-remove this 3 lines if you do not throw
   ;throw          --|
end catch
close @cur
deallocate @cur