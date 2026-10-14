NaN`daniele21/local-runner-infra`. Do **not** use `runs-on: self-hosted` in a source repository expecting access to central runners.
- `.github/workflows/local-ci.yml` dispatches the exact commit SHA via `repository_dispatch`. It is Terraform-managed (except protected PR-managed source repositories). Do not bypass protections, delete it, or leak `CENTRAL_DISPATCH_TOKEN`.
- The source-owned `.github/ci/local-test.sh` runs as a bounded validation lane on a central Mac runner. Preserve exit codes, command failures and exact commit identity.
- Verify `local-ci/mac` on the source commit and central logs. Dispatch job success alone is not a test pass.
- Existing hosted tests, release/signing workflows, PR required checks and OS-specific matrices remain required until an explicit proven migration.
- Integration guide: https://github.com/daniele21/local-runner-infra/blob/main/docs/integration-guide.md
