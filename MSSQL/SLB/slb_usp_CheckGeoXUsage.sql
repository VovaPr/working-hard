USE master;
GO

SELECT TOP (50) m.type
	,m.id
	,m.name
	,m.modified_username
	,m.modified_date
	,m.created
	,m.created_by
FROM (
	SELECT 'Basin' AS type
		,b.basin_id AS id
		,b.name
		,b.modified_username
		,b.modified_date
		,c.modified_date AS created
		,c.modified_username AS created_by
	FROM geox.geox.basin b
		,geox.geox.basin_change c
	WHERE b.basin_id = c.basin_id
		AND c.description = 'Created'
	
	UNION
	
	SELECT 'Play' AS type
		,b.play_id AS id
		,b.name
		,b.modified_username
		,b.modified_date
		,c.modified_date AS created
		,c.modified_username AS created_by
	FROM geox.geox.play b
		,geox.geox.play_change c
	WHERE b.play_id = c.play_id
		AND c.description = 'Created'
	
	UNION
	
	SELECT 'Prospect' AS type
		,b.prospect_id AS id
		,b.name
		,b.modified_username
		,b.modified_date
		,c.modified_date AS created
		,c.modified_username AS created_by
	FROM geox.geox.prospect b
		,geox.geox.prospect_change c
	WHERE b.prospect_id = c.prospect_id
		AND c.description = 'Created'
	
	UNION
	
	SELECT 'Segment' AS type
		,b.segment_id AS id
		,b.name
		,b.modified_username
		,b.modified_date
		,c.modified_date AS created
		,c.modified_username AS created_by
	FROM geox.geox.segment b
		,geox.geox.segment_change c
	WHERE b.segment_id = c.segment_id
		AND c.description = 'Created'
	
	UNION
	
	SELECT 'Basin ana' AS type
		,b.bas_ana_id AS id
		,b.name
		,b.modified_username
		,b.modified_date
		,c.modified_date AS created
		,c.modified_username
	FROM geox.geox.bas_ana b
		,geox.geox.bas_change c
	WHERE b.bas_ana_id = c.bas_ana_id
		AND c.description = 'Created'
	
	UNION
	
	SELECT 'Play ana' AS type
		,b.pla_ana_id AS id
		,b.name
		,b.modified_username
		,b.modified_date
		,c.modified_date AS created
		,c.modified_username AS created_by
	FROM geox.geox.pla_ana b
		,geox.geox.pla_change c
	WHERE b.pla_ana_id = c.pla_ana_id
		AND c.description = 'Created'
	
	UNION
	
	SELECT 'Prospect ana' AS type
		,b.pro_ana_id AS id
		,b.name
		,b.modified_username
		,b.modified_date
		,c.modified_date AS created
		,c.modified_username AS created_by
	FROM geox.geox.pro_ana b
		,geox.geox.pro_change c
	WHERE b.pro_ana_id = c.pro_ana_id
		AND c.description = 'Created'
	
	UNION
	
	SELECT 'Segment ana' AS type
		,b.seg_ana_id AS id
		,b.name
		,b.modified_username
		,b.modified_date
		,c.modified_date AS created
		,c.modified_username AS created_by
	FROM geox.geox.seg_ana b
		,geox.geox.seg_change c
	WHERE b.seg_ana_id = c.seg_ana_id
		AND c.description = 'Created'
	
	UNION
	
	SELECT 'Discovery ana' AS type
		,b.dis_ana_id AS id
		,b.name
		,b.modified_username
		,b.modified_date
		,c.modified_date AS created
		,c.modified_username AS created_by
	FROM geox.geox.dis_ana b
		,geox.geox.dis_change c
	WHERE b.dis_ana_id = c.dis_ana_id
		AND c.description = 'Created'
	) AS m
ORDER BY modified_date DESC