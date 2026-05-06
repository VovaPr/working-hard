# PowerShell command to list TCP connections related to SQL Server with LocalAddress as 0.0.0.0
# Provides information such as LocalAddress, LocalPort, RemoteAddress, RemotePort, State, ProcessID, and CommandLine

# Retrieve SQL Server processes
$SqlProcesses = Get-WmiObject -Class Win32_Process | Where-Object { $_.Name -eq 'sqlservr.exe' }

# Get TCP connections and filter based on SQL Server process ID and LocalAddress
Get-NetTcpConnection |
    Where-Object { $_.OwningProcess -in $SqlProcesses.ProcessId -and $_.LocalAddress -eq '0.0.0.0' } |
    ForEach-Object {
        # Store the process ID and command line for each connection
        $ProcessId = $_.OwningProcess
        $CommandLine = ($SqlProcesses | Where-Object { $_.ProcessId -eq $ProcessId }).CommandLine

        # Add ProcessID and CommandLine properties to each connection
        $_ | Add-Member -NotePropertyName 'ProcessID' -NotePropertyValue $ProcessId -PassThru |
            Add-Member -NotePropertyName 'CommandLine' -NotePropertyValue $CommandLine -PassThru
    } |
    Select-Object LocalAddress, LocalPort, RemoteAddress, RemotePort, State, ProcessID, CommandLine