<!-- Use this file to provide workspace-specific custom instructions to Copilot. For more details, visit https://code.visualstudio.com/docs/copilot/copilot-customization#_use-a-githubcopilotinstructionsmd-file -->
- [x] Verify that the copilot-instructions.md file in the .github directory is created.

- [x] Clarify Project Requirements
	Technology: T-SQL with MS SQL Server
	Purpose: Monitor SQL Server Agent Jobs from one monitoring server. Write job statuses to dba_db on all servers. Email alerting every hour for failed jobs.

- [x] Scaffold the Project
	Created project structure:
	- setup/ - Schema and table initialization scripts
	- monitoring/ - Job monitoring and alerting scripts
	- docs/ - Additional documentation

- [x] Customize the Project
	Implemented T-SQL scripts for:
	- Job status collection from monitored servers
	- Failed job tracking and analysis
	- Email alerting system with hourly notifications

- [x] Install Required Extensions
	No extensions required for T-SQL project

- [x] Compile the Project
	T-SQL scripts are ready to execute (no compilation needed)

- [x] Create and Run Task
	Not required for T-SQL project

- [x] Launch the Project
	T-SQL scripts ready in monitoring/ directory

- [x] Ensure Documentation is Complete
	README.md created with comprehensive documentation
	copilot-instructions.md cleaned up with all HTML comments removed

- Work through each checklist item systematically.
- Keep communication concise and focused.
- Follow development best practices.