-- Select cached query plans in SQL Server

SELECT deqp.query_plan
       ,dest.TEXT
       ,creation_time
       ,SUBSTRING(dest.TEXT, (deqs.statement_start_offset / 2) + 1, (deqs.statement_end_offset - deqs.statement_start_offset) / 2 + 1) AS actualstatement
       ,dest.*
FROM sys.dm_exec_query_stats AS deqs
CROSS APPLY sys.dm_exec_query_plan(deqs.plan_handle) AS deqp
CROSS APPLY sys.dm_exec_sql_text(deqs.sql_handle) AS dest
WHERE deqp.objectid = OBJECT_ID('dbo.fnGetVirtualFoldersByUserId');