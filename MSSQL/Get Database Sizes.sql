USE master;
GO

SELECT 
    d.name AS database_name,
    SUM(mf.size * 8 / 1024) AS size_mb,
    SUM(mf.size * 8 / 1024 / 1024) AS size_gb
FROM 
    sys.databases d
JOIN 
    sys.master_files mf
ON 
    d.database_id = mf.database_id
WHERE 
    d.database_id > 4
GROUP BY 
    d.name
ORDER BY 
    size_gb DESC;