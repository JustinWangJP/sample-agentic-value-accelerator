#!/usr/bin/env bash
set -euo pipefail

portal_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
infra_dir="$portal_dir/infrastructure"

fail() { printf '%s\n' "$*" >&2; exit 1; }
[[ $# -eq 2 ]] || fail "使用方法: AWS_PROFILE=<SSO profile> scripts/deploy.sh <backend.hcl> <dev.tfvars>"
[[ -z "${CI:-}" && -z "${GITHUB_ACTIONS:-}" ]] || fail "CI からの反映は禁止されています。ローカル端末で実行してください。"
[[ -n "${AWS_PROFILE:-}" ]] || fail "IAM Identity Center の AWS_PROFILE を指定してください。"
[[ -z "${AWS_ACCESS_KEY_ID:-}" && -z "${AWS_SECRET_ACCESS_KEY:-}" && -z "${AWS_SESSION_TOKEN:-}" ]] || fail "環境変数の AWS 認証情報を解除し、SSO profile を使用してください。"
[[ -f "$1" && -f "$2" ]] || fail "backend 設定と dev の変数ファイルが必要です。"
backend_file="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
vars_file="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
for command in aws terraform python3 npm; do command -v "$command" >/dev/null || fail "必要なコマンドがありません: $command"; done
sso_session="$(aws configure get sso_session --profile "$AWS_PROFILE" || true)"
sso_start_url="$(aws configure get sso_start_url --profile "$AWS_PROFILE" || true)"
[[ -n "$sso_session" || -n "$sso_start_url" ]] || fail "IAM Identity Center の SSO profile が必要です。"
caller_arn="$(aws sts get-caller-identity --query Arn --output text --profile "$AWS_PROFILE")"
[[ "$caller_arn" == arn:aws:sts::*:assumed-role/AWSReservedSSO_RegistryPortalDeployer_*/* ]] || fail "RegistryPortalDeployer の短期認証情報を使用してください。"

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/registry-portal-deploy.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT
python3 "$portal_dir/scripts/build_lambda.py" --output "$work_dir/management.zip"
terraform -chdir="$infra_dir" init -input=false -reconfigure -backend-config="$backend_file" -backend-config=encrypt=true -backend-config=use_lockfile=true
terraform -chdir="$infra_dir" validate
terraform -chdir="$infra_dir" plan -input=false -var-file="$vars_file" -var="lambda_zip_path=$work_dir/management.zip" -out="$work_dir/approved.tfplan"
terraform -chdir="$infra_dir" show -json "$work_dir/approved.tfplan" > "$work_dir/plan.json"
python3 "$portal_dir/scripts/check_plan.py" "$work_dir/plan.json"
terraform -chdir="$infra_dir" show -no-color "$work_dir/approved.tfplan"
printf '\n上記 plan と Registry・IAM の変更を確認してください。反映する場合は apply と入力してください: '
read -r confirmation || fail "反映を中止しました。"
[[ "$confirmation" == "apply" ]] || fail "反映を中止しました。"
terraform -chdir="$infra_dir" apply -input=false "$work_dir/approved.tfplan"
terraform -chdir="$infra_dir" output -json > "$work_dir/outputs.json"
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
