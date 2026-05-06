---------- Prod ----------
declare @backup nvarchar(1000)
declare @grant nvarchar(1000)
set @backup =
'mkdir b:\backup ; mkdir b:\backup\FULL ; mkdir b:\backup\DIFF; mkdir b:\backup\LOG ; mkdir b:\backup\FULL\'+@@SERVERNAME+' ; mkdir b:\backup\LOG\'+@@SERVERNAME+' ; mkdir b:\backup\DIFF\'+@@SERVERNAME+' ; mkdir b:\backup\FULL\SYSTEMDB ; mkdir b:\backup\FULL\SYSTEMDB\'+@@SERVERNAME+' '
 exec xp_cmdshell @backup
 
 set @grant = 'icacls "b:\backup" /grant DOMAIN\SQLAgent:(OI)(CI)F /T'
 exec xp_cmdshell @grant

---
---------- PreProd ----------
declare @backup nvarchar(1000)
declare @grant nvarchar(1000)
set @backup =
'mkdir b:\backup ; mkdir b:\backup\FULL ; mkdir b:\backup\DIFF; mkdir b:\backup\LOG ; mkdir b:\backup\FULL\'+@@SERVERNAME+' ; mkdir b:\backup\LOG\'+@@SERVERNAME+' ; mkdir b:\backup\DIFF\'+@@SERVERNAME+' ; mkdir b:\backup\FULL\SYSTEMDB ; mkdir b:\backup\FULL\SYSTEMDB\'+@@SERVERNAME+' '
 exec xp_cmdshell @backup
 
 set @grant = 'icacls "b:\backup" /grant DOMAIN\SQLAgent:(OI)(CI)F /T'
 exec xp_cmdshell @grant