select st.name, pf.name, p.partition_number, r.value, p.rows
from sys.tables st
inner join sys.partitions p
    on p.object_id = st.object_id
    and p.index_id <= 1 -- clustered or heap
inner join sys.indexes si
    on si.object_id = st.object_id
    and si.index_id = p.index_id
inner join sys.partition_schemes ps
    on si.data_space_id = ps.data_space_id
inner join sys.partition_functions pf
    on ps.function_id = pf.function_id
INNER JOIN sys.partition_range_values r  
    ON pf.function_id = r.function_id
    and r.boundary_id = p.partition_number
order by st.name, p.partition_number, p.rows

---

select st.name, pf.name, p.partition_number, r.value, p.rows
from sys.tables st
inner join sys.partitions p
    on p.object_id = st.object_id
    and p.index_id <= 1 -- clustered or heap
inner join sys.indexes si
    on si.object_id = st.object_id
    and si.index_id = p.index_id
inner join sys.partition_schemes ps
    on si.data_space_id = ps.data_space_id
inner join sys.partition_functions pf
    on ps.function_id = pf.function_id
INNER JOIN sys.partition_range_values r
    ON pf.function_id = r.function_id
    and r.boundary_id = p.partition_number
       where st.name = 'VirtualFolderNewsFeed_Archive_1_Partition'
             and p.partition_number <= 5
             or st.name = 'VirtualFolderNewsFeed_Permanent_Archive_1'
             and p.partition_number between 65 and 70
             or st.name = 'VirtualFolderNewsFeedEvent_Archive_1_Partition'
             and p.partition_number <= 5
             or st.name = 'VirtualFolderNewsFeedEvent_Permanent_Archive_1'
             and p.partition_number between 65 and 70
order by st.name, p.partition_number, p.rows