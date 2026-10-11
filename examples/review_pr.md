# Review a pull request

## For Humans

The supplied context identifies the pull request to review. The caller prepares its diff and repository files in the workspace; the webhook does not fetch them automatically.

Read `pull-request.diff` in the workspace and inspect relevant repository files. Check that the provided diff identifies the same PR and head SHA as the supplied context. If the diff or that identifying information is missing, report failure with code `missing_pr_context`; do not guess which changes to review.

Review the changes for actionable correctness problems. Return a JSON object of the form `{"comments": [{"message": "Describe the finding and affected location"}]}`. Return `{"comments": []}` when you find no actionable problems. Do not edit files or post comments to GitHub.
