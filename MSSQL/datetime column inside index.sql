SELECT 
    i.name AS IndexName,
    OBJECT_NAME(ic.OBJECT_ID) AS TableName,
    COL_NAME(ic.OBJECT_ID,ic.column_id) AS ColumnName
FROM 
    sys.indexes AS i
JOIN 
    sys.index_columns AS ic ON  i.OBJECT_ID = ic.OBJECT_ID
    AND i.index_id = ic.index_id
JOIN 
    sys.columns AS c ON ic.OBJECT_ID = c.OBJECT_ID 
    AND ic.column_id = c.column_id
JOIN 
    sys.types AS t ON c.user_type_id = t.user_type_id
WHERE 
    t.name = 'datetime'
ORDER BY 
    TableName, 
    IndexName, 
    ColumnName;