task: reformat module docstrings of .py files as following example does.
do the job in batches.
report stats when done

example of reformat:
before:

"""Create a GitHub repository from a local project and push it.
Subcommands: ``api`` creates the repository through the REST API, ``api-push`` also initializes, commits and pushes, ``gh-create`` mixes PyGithub and the ``gh`` CLI, ``gh-cli`` is a pure ``gh``/git workflow and ``gh-managed`` an interactive manager.
The token comes from ``~/.env`` and ``--backend`` picks the git implementation.
Usage: gh_repo {api,api-push,gh-create,gh-cli,gh-managed} [options] Positional arguments: repo_name Name of the new repository.
description Subcommands: api Create a repo via API (mkghrepo.py).
api-push Init/commit + API create + push (new_repo.py).
gh-create PyGithub/gh-CLI hybrid create+push (new_repo2.py).
gh-cli Pure gh CLI + git subprocess workflow (newrepo.py).
gh-managed Interactive manager with prompts & README (pynewrepo.py).
Options: -b, --backend Backend for git and GitHub operations.
Default: subprocess.
--env-file --github-username --private Make the repo private.
-n, --name Repository name.
Default: current directory name.
--branch --commit-message --global-gitignore -m, --message Custom commit message.
Default: auto-generated with timestamp.
--git-email ...
plus 1 more; run ``gh_repo --help`` for the full list.
Examples: $ gh_repo api-push myproject --private Command-line script: run ``python gh_repo.py`` or the ``gh_repo`` command.
"""



after:

"""
Create a GitHub repository from a local project and push it.

Subcommands: 
 - api : creates the repository through the REST API
 - api-push: initialises, commits and pushes
 - gh-create: mixes PyGithub and the gh CLI
 - gh-cli: is a pure gh/git workflow
 - gh-managed: an interactive manager

The token comes from ~/.env and --backend picks the git implementation.

Usage:
gh_repo {api,api-push,gh-create,gh-cli,gh-managed} [options] Positional arguments: repo_name (Name of the new repository)

description Subcommands:
- api: Create a repo via API
- api-push: Init/commit + API create + push
- gh-create: PyGithub/gh-CLI hybrid create+push
- gh-cli: Pure gh CLI + git subprocess workflow
- gh-managed: Interactive manager with prompts & README

Options: 
-b, --backend : Backend for git and GitHub operations (Default: subprocess)
--env-file:
--github-username:
--private:Make the repo private
-n, --name:Repository name (Default: current directory name)
--branch:
--commit-message:
--global-gitignore:
-m, --message: Custom commit message (Default: auto-generated with timestamp)
--git-email:

run gh_repo --help for the full list

Examples: 
  $ gh_repo api-push myproject --private

Command-line script:
  run python gh_repo.py or gh_repo command

"""
