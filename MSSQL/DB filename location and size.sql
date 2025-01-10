SELECT
    db.name AS name,
    mf.physical_name AS filename,
    fg.name AS filegroup,
    mf.size * 8 AS size,
    CASE
        WHEN mf.type_desc = 'LOG' THEN 'log only'
        ELSE 'data only'
    END AS usage
FROM 
    sys.master_files mf
LEFT JOIN 
    sys.databases db ON db.database_id = mf.database_id
LEFT JOIN 
    sys.filegroups fg ON fg.data_space_id = mf.data_space_id
WHERE 
    db.name NOT IN ('master', 'msdb', 'model', 'tempdb', 'Northwind', 'TestDB', 'OpsDB')
ORDER BY 
    db.name;