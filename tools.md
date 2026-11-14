Run the following static-analysis and type-checking tools on the Python files in this repository:
- mypy
- pylint
- ruff
- pyright
- ty
- pyrefly
Save a separate report for each tool, for example:
- reports/mypy.txt
- reports/pylint.txt
- reports/ruff.txt
- reports/pyright.txt
- reports/ty.txt
- reports/pyrefly.txt
First, inspect the repository structure, Python version, and project configuration.
Check each tool’s available version and use the repository’s existing configuration and conventions.
Run each tool against the repository’s Python files, using its supported project-wide command.
Don’t install or upgrade tools or dependencies unless I explicitly approve it.
### Guidelines
- Run the tools independently so a failure or missing tool doesn’t prevent the others from running.
- Capture each tool’s standard output, standard error, and exit status in that tool’s report.
  If a tool is unavailable or cannot run, write that fact and the reason in its report.
- Keep reports separate by tool; don’t combine results into one file.
- Create the reports directory if needed.
  Don’t overwrite unrelated files.
  If a report already exists, preserve it or ask before replacing it.
- Do not modify Python source files or project configuration.
  Only create the requested reports.
- Do not suppress or fix diagnostics.
  Preserve the tools’ output so the reports reflect what they actually found.
After running the tools, summarize which ones ran, where each report was saved,
and any tools that were unavailable or encountered errors.
