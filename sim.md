Analyze all Python files in this repository recursively for similarity
and overlapping functionality.
Do not modify source files.
First, inspect the repository structure and identify generated, vendored, virtual-environment,
build, and cache directories to exclude.
Respect existing ignore files and project conventions where applicable.
Include the exclusions and analysis method in the report.
Analyze both:
- **Code similarity:** duplicated or near-duplicated blocks, functions, and scripts,
  including cases where names or formatting differ.
- **Functional overlap:** scripts that appear to perform the same or substantially similar job,
  even if their implementations differ.
Do not treat shared imports, boilerplate,
or common patterns alone as evidence that scripts do the same job.
Base functional-overlap findings on the code’s behavior and purpose,
and mark uncertain conclusions clearly.
Create a `reports/` directory if needed, and save these outputs:
- `reports/python_similarity.md`
  — a readable report with an executive summary and detailed findings.
- `reports/python_similarity.svg` — a standalone visual overview of the findings.
- `reports/python_similarity.mmd`
  — a Mermaid diagram source file that renders the relationships between similar
  or functionally overlapping scripts.
Report requirements:
- For each finding, list the relevant file paths and line ranges,
  explain what is similar or overlapping, and provide a confidence level.
- Group related scripts where useful, distinguish code duplication from functional overlap,
  and identify especially strong matches.
- If the analysis uses a numeric similarity score or threshold,
  explain how it was calculated and what it means.
  Do not present estimates as measured scores.
- Make the SVG and Mermaid diagram consistent with the written report, readable,
  and labeled with repository-relative paths.
  The SVG must be valid standalone SVG; the Mermaid output must contain valid Mermaid syntax.
- If there are no meaningful findings, say so explicitly rather than forcing matches.
Use an appropriate available analysis approach or tool.
Don’t install or upgrade dependencies without approval.
If a tool is unavailable, use a reasonable alternative and document the method and limitations.
Do not commit or push changes.
When finished, verify that all three reports exist, the SVG is valid XML,
and the Mermaid source is syntactically plausible.
Summarize the analysis method, key findings, excluded paths, and report locations.
