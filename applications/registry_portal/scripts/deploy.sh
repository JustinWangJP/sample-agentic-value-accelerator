#!/usr/bin/env bash
set -euo pipefail

portal_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
infra_dir="$portal_dir/infrastructure"

fail() { printf '%s\n' "$*" >&2; exit 1; }
usage="使用方法: AWS_PROFILE=<SSO profile またはロールの profile> [TERRAFORM_AWS_PROFILE=<credential_process の profile>] scripts/deploy.sh <backend.hcl> <dev.tfvars>"
[[ $# -eq 2 ]] || fail "$usage"
[[ -z "${CI:-}" && -z "${GITHUB_ACTIONS:-}" ]] || fail "CI からの反映は禁止されています。ローカル端末で実行してください。"
[[ -n "${AWS_PROFILE:-}" ]] || fail "AWS_PROFILE に IAM Identity Center の SSO profile か、RegistryPortalDeployer を MFA 付きで引き受ける profile を指定してください。"
[[ -z "${AWS_ACCESS_KEY_ID:-}" && -z "${AWS_SECRET_ACCESS_KEY:-}" && -z "${AWS_SESSION_TOKEN:-}" ]] || fail "環境変数の AWS 認証情報を解除し、profile を使用してください。"
[[ -f "$1" && -f "$2" ]] || fail "backend 設定と dev の変数ファイルが必要です。"
backend_file="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
vars_file="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
for command in aws terraform python3 npm; do command -v "$command" >/dev/null || fail "必要なコマンドがありません: $command"; done
profile_setting() { aws configure get "$1" --profile "$2" 2>/dev/null || true; }
caller_of() { aws sts get-caller-identity --query Arn --output text --profile "$1"; }

sso_session="$(profile_setting sso_session "$AWS_PROFILE")"
sso_start_url="$(profile_setting sso_start_url "$AWS_PROFILE")"
role_arn="$(profile_setting role_arn "$AWS_PROFILE")"
mfa_serial="$(profile_setting mfa_serial "$AWS_PROFILE")"
if [[ -n "$sso_session" || -n "$sso_start_url" ]]; then
  # IAM Identity Center: permission set の SSO ロール。Terraform も同じ profile を使う。
  terraform_profile="$AWS_PROFILE"
  caller_arn="$(caller_of "$AWS_PROFILE")"
  [[ "$caller_arn" == arn:aws:sts::*:assumed-role/AWSReservedSSO_RegistryPortalDeployer_*/* ]] || fail "RegistryPortalDeployer の短期認証情報を使用してください。"
elif [[ -n "$role_arn" && -n "$mfa_serial" ]]; then
  # 個人アカウント: IAM ロール RegistryPortalDeployer を MFA 付きで引き受ける。
  # Terraform の AWS provider は MFA コードを入力できないため、AWS CLI がキャッシュした
  # 短期認証情報を credential_process で渡す profile（既定は <AWS_PROFILE>-tf）を使う。
  terraform_profile="${TERRAFORM_AWS_PROFILE:-${AWS_PROFILE}-tf}"
  read -r -a process_words <<<"$(profile_setting credential_process "$terraform_profile")"
  process_profile=""
  process_format=""
  for ((i = 0; i + 1 < ${#process_words[@]}; i++)); do
    [[ "${process_words[i]}" == "--profile" ]] && process_profile="${process_words[i + 1]}"
    [[ "${process_words[i]}" == "--format" ]] && process_format="${process_words[i + 1]}"
  done
  [[ "${process_words[*]:0:3}" == "aws configure export-credentials" && "$process_profile" == "$AWS_PROFILE" && "$process_format" == "process" ]] \
    || fail "Terraform 用の profile $terraform_profile に credential_process = aws configure export-credentials --profile $AWS_PROFILE --format process を設定してください。"
  # AWS CLI で MFA コードを入力し、短期認証情報をキャッシュする。
  caller_arn="$(caller_of "$AWS_PROFILE")"
  [[ "$caller_arn" == arn:aws:sts::*:assumed-role/RegistryPortalDeployer/* ]] || fail "RegistryPortalDeployer の短期認証情報を使用してください。"
  terraform_caller_arn="$(caller_of "$terraform_profile")" || fail "Terraform 用の profile $terraform_profile で認証できません。aws sts get-caller-identity --profile $AWS_PROFILE を実行し直してください。"
  [[ "$terraform_caller_arn" == "$caller_arn" ]] || fail "Terraform 用の profile $terraform_profile が RegistryPortalDeployer の同じセッションになりません。"
else
  fail "IAM Identity Center の SSO profile か、role_arn と mfa_serial を持つ profile を指定してください。"
fi
tf() { AWS_PROFILE="$terraform_profile" terraform -chdir="$infra_dir" "$@"; }

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/registry-portal-deploy.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT
python3 "$portal_dir/scripts/build_lambda.py" --output "$work_dir/lambda.zip"
tf init -input=false -reconfigure -backend-config="$backend_file" -backend-config=encrypt=true -backend-config=use_lockfile=true
tf validate
tf plan -input=false -var-file="$vars_file" -var="lambda_zip_path=$work_dir/lambda.zip" -out="$work_dir/approved.tfplan"
tf show -json "$work_dir/approved.tfplan" > "$work_dir/plan.json"
python3 "$portal_dir/scripts/check_plan.py" "$work_dir/plan.json"
tf show -no-color "$work_dir/approved.tfplan"
printf '\n上記 plan と Registry・IAM の変更を確認してください。反映する場合は apply と入力してください: '
read -r confirmation || fail "反映を中止しました。"
[[ "$confirmation" == "apply" ]] || fail "反映を中止しました。"
tf apply -input=false "$work_dir/approved.tfplan"
tf output -json > "$work_dir/outputs.json"
python3 - "$work_dir/outputs.json" "$work_dir" <<'PY'
import json
from pathlib import Path
import sys

outputs = json.loads(Path(sys.argv[1]).read_text())
work = Path(sys.argv[2])
config = outputs["frontend_config"]["value"]
(work / "frontend.json").write_text(json.dumps(config))
for key in ("spa_bucket_name", "cloudfront_distribution_id"):
    (work / key).write_text(outputs[key]["value"])
PY
(cd "$portal_dir/frontend" && npm ci)
python3 - "$work_dir/frontend.json" "$portal_dir/frontend" <<'PY'
import json
import os
from pathlib import Path
import subprocess
import sys

config = json.loads(Path(sys.argv[1]).read_text())
subprocess.run(["npm", "run", "build"], cwd=sys.argv[2], env={**os.environ, **config}, check=True)
PY
spa_bucket="$(cat "$work_dir/spa_bucket_name")"
distribution_id="$(cat "$work_dir/cloudfront_distribution_id")"
aws s3 sync "$portal_dir/frontend/dist/" "s3://$spa_bucket/" --delete --exclude index.html --cache-control 'public,max-age=31536000,immutable' --profile "$AWS_PROFILE"
aws s3 cp "$portal_dir/frontend/dist/index.html" "s3://$spa_bucket/index.html" --cache-control 'no-cache,no-store,must-revalidate' --content-type 'text/html' --profile "$AWS_PROFILE"
aws cloudfront create-invalidation --distribution-id "$distribution_id" --paths '/*' --profile "$AWS_PROFILE"
printf '\n配信が完了しました。README の dev 手動受入を実施してください。\n'
