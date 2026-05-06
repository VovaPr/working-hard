--SELECT the list of statistics thats don't update more then 7 days

SELECT DISTINCT sOBJ.name AS [TableName], SUM(sPTN.Rows) AS [RowCount] 
FROM sys.objects AS sOBJ 
       INNER JOIN sys.partitions AS sPTN ON sOBJ.object_id = sPTN.object_id 
       INNER JOIN sys.stats AS sSTS ON sOBJ.object_id = sSTS.object_id 
       CROSS APPLY sys.dm_db_stats_properties (sSTS.object_id, sSTS.stats_id) as sp 
WHERE 
		sOBJ.type = 'U' -- user objects 
		AND sOBJ.is_ms_shipped = 0x0 
		AND sOBJ.name not in ('DFVAvailableIds') 
		AND index_id < 2 
		AND sPTN.Rows > 0 
		-- ignore empty tables (not necessary to defragment\rebuild) 
		AND STATS_DATE(sSTS.object_id, sSTS.stats_id) < (dateadd(day,datediff(day,3,GETDATE()),0)) 
		-- we work with statistics older than 3 days 
		AND sp.modification_counter * 1. / NULLIF(sp.rows,0) * 100 > 4	 
		-- percentage of changes in a particular statistic 
GROUP BY sOBJ.schema_id, sOBJ.name, sSTS.name 
ORDER BY [RowCount] DESC