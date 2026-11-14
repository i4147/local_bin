Debug and fix errors in the Python files in this repository, making changes in place.
Work through the repository in small batches.
After each batch, summarize the files examined, fixes made,
and any remaining issues before continuing.
First, inspect the repository structure and read its contribution guidelines, test instructions,
and relevant configuration.
Identify how the project expects code to be run and tested.
### Guidelines
- Focus on reproducible errors in Python code, such as failing tests, runtime exceptions,
  incorrect behavior, and lint or type-check errors when those checks are part of the project.
- Ignore import errors completely:
  do not investigate or fix them, and do not treat them as a reason to stop.
  If a test or check fails only because of an import error,
  note that it was skipped for that reason and continue with other checks.
- Make the smallest targeted changes that resolve confirmed errors.
  Avoid unrelated refactoring, formatting changes, or feature additions.
- Preserve intended behavior and existing APIs.
  If the intended behavior is unclear, don’t guess—flag the issue for me.
- Add or update tests when practical to cover each fix.
  Don’t weaken or delete tests just to make them pass.
- Don’t modify generated files, dependencies, or configuration unless a fix clearly requires it.
  Explain any such change before making it.
- Do not commit or push changes.
At the end of each batch,
review the diff and run the relevant tests or checks for the files changed, where possible.
Keep track of failures that remain, including failures unrelated to your changes.
After all batches are complete, run the relevant project checks if available.
Provide a final summary of the errors fixed, files changed, checks run, remaining failures,
and anything that needs my input.
Clearly distinguish skipped import-error failures from other failures.
