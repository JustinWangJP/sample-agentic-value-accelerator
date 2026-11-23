#!/usr/bin/env bash
set -euo pipefail

portal_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
infra_dir="$portal_dir/infrastructure"

fail() { printf '%s\n' "$*" >&2; exit 1; }
# 反映できるスタックの表。--stack を省略すると本体スタック（main）を反映する。
# 追加スタック（ADR-0013）は別 state のため、同じ検査と plan ガードを通して Terraform だけを反映する。
addon_stacks=(decision management notification_events)
# スタックごとの属性を設定する。引数はスタック名で、表にない名前なら 1 を返す。
#   stack_subdir: infrastructure/ からの相対ディレクトリ（本体は空）
#   state_key: backend.hcl の key。別のスタックの state を指定して誤って反映するのを防ぐ
#   publishes_spa: 反映後に SPA をビルドして配信するか
#   allows_sso: IAM Identity Center の permission set で反映できるか（permission set は本体向けの権限だけを持つ）
stack_config() {
  publishes_spa=false
  allows_sso=false
  case "$1" in
    main)
      stack_subdir=""
      state_key="registry-portal/dev/terraform.tfstate"
      publishes_spa=true
      allows_sso=true
      ;;
    decision)
      stack_subdir="decision"
      state_key="registry-portal/dev/decision.tfstate"
      ;;
    management)
      stack_subdir="management"
      state_key="registry-portal/dev/management.tfstate"
      ;;
    notification_events)
      stack_subdir="notification_events"
      state_key="registry-portal/dev/notification-events.tfstate"
      ;;
    *) return 1 ;;
  esac
}
addon_choices="$(IFS='|'; echo "${addon_stacks[*]}")"
usage="使用方法: AWS_PROFILE=<SSO profile またはロールの profile> [TERRAFORM_AWS_PROFILE=<credential_process の profile>] scripts/deploy.sh [--destroy] [--stack $addon_choices] <backend.hcl> <tfvars>（各オプションは 1 回だけ指定できます。順番は自由です）"
# --destroy: 指定した 1 つのスタックを撤去する。配信と同じ検査と plan ガードを通し、SPA は配信しない
# 同じオプションを 2 回指定した場合は、後の指定を黙って使わずに止める（撤去の対象の取り違えを防ぐ）
destroy=false
stack="main"
stack_given=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --destroy)
      [[ "$destroy" == false ]] || fail "--destroy は 1 回だけ指定してください。"
      destroy=true
      shift
      ;;
    --stack)
      [[ "$stack_given" == false ]] || fail "--stack は 1 回だけ指定してください。"
      [[ $# -ge 2 ]] || fail "$usage"
      stack_given=true
      stack="$2"
      shift 2
      [[ "$stack" != main ]] && stack_config "$stack" || fail "追加スタックには ${addon_stacks[*]} のいずれかを指定してください。"
      ;;
    *) break ;;
  esac
done
stack_config "$stack"
# 撤去のモードで変わる設定は、ここで一度だけ決める。
#   is_main_destroy: 本体の撤去か。追加スタックの state と本体の保護を、apply の前に検査する
#   plan_args: terraform plan の引数。撤去では destroy の plan を保存する
#   completion_message: apply が成功し、SPA を配信しないときに表示する
is_main_destroy=false
plan_args=(-input=false)
completion_message="${stack} スタックの反映が完了しました。README の dev 手動受入を実施してください。"
if [[ "$destroy" == true ]]; then
  publishes_spa=false
  plan_args+=(-destroy)
  completion_message="${stack} スタックの撤去が完了しました。"
  if [[ "$stack" == main ]]; then is_main_destroy=true; fi
fi
[[ $# -eq 2 ]] || fail "$usage"
[[ -z "${CI:-}" && -z "${GITHUB_ACTIONS:-}" ]] || fail "CI からの反映は禁止されています。ローカル端末で実行してください。"
stack_dir="$infra_dir${stack_subdir:+/$stack_subdir}"
[[ -n "${AWS_PROFILE:-}" ]] || fail "AWS_PROFILE に IAM Identity Center の SSO profile か、RegistryPortalDeployer を MFA 付きで引き受ける profile を指定してください。"
[[ -z "${AWS_ACCESS_KEY_ID:-}" && -z "${AWS_SECRET_ACCESS_KEY:-}" && -z "${AWS_SESSION_TOKEN:-}" ]] || fail "環境変数の AWS 認証情報を解除し、profile を使用してください。"
[[ -f "$1" && -f "$2" ]] || fail "backend 設定と dev の変数ファイルが必要です。"
backend_file="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
vars_file="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
# 本体の backend.hcl を追加スタックに渡すと、plan に本体の資源の削除が出る。plan ガードは Registry 以外の
# 削除を止めないため、Terraform を実行する前に state key とスタックの対応を確かめる
backend_key="$(sed -n 's/^[[:space:]]*key[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$backend_file")"
[[ "$backend_key" == "$state_key" ]] || fail "backend 設定の key（${backend_key:-未設定}）が ${stack} スタックの state key（${state_key}）と一致しません。infrastructure/${stack_subdir:+$stack_subdir/}backend.hcl.example を確認してください。"
required_commands=(aws terraform python3)
[[ "$publishes_spa" == true ]] && required_commands+=(npm)
for command in "${required_commands[@]}"; do command -v "$command" >/dev/null || fail "必要なコマンドがありません: $command"; done
profile_setting() { aws configure get "$1" --profile "$2" 2>/dev/null || true; }
caller_of() { aws sts get-caller-identity --query Arn --output text --profile "$1"; }

sso_session="$(profile_setting sso_session "$AWS_PROFILE")"
sso_start_url="$(profile_setting sso_start_url "$AWS_PROFILE")"
role_arn="$(profile_setting role_arn "$AWS_PROFILE")"
mfa_serial="$(profile_setting mfa_serial "$AWS_PROFILE")"
if [[ -n "$sso_session" || -n "$sso_start_url" ]]; then
  # IAM Identity Center: permission set の SSO ロール。Terraform も同じ profile を使う。
  # bootstrap の permission set は本体スタック向けの権限だけを持つため、追加スタックは扱わない。
  [[ "$allows_sso" == true ]] || fail "IAM Identity Center の permission set は追加スタックに対応していません。RegistryPortalDeployer を MFA 付きで引き受ける profile で実行してください。"
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
# 指定したディレクトリで Terraform を実行する。tf は反映するスタックのディレクトリで実行する
tf_at() {
  local dir="$1"
  shift
  AWS_PROFILE="$terraform_profile" terraform -chdir="$dir" "$@"
}
tf() { tf_at "$stack_dir" "$@"; }
# backend.hcl で backend を初期化する。追加の引数（key の差し替え）は backend.hcl の後に置き、
# backend.hcl の値より優先させる（後から指定した値が優先される）
tf_init() {
  local dir="$1"
  shift
  tf_at "$dir" init -input=false -reconfigure -backend-config="$backend_file" "$@" -backend-config=encrypt=true -backend-config=use_lockfile=true
}

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/registry-portal-deploy.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT
if [[ "$is_main_destroy" == true ]]; then
  # 追加スタックは本体の REST API・Lambda・実行ロール・補助テーブルを借りて route や権限を足している。
  # 本体を先に消すと追加スタックの state に孤立したリソースが残るため、撤去の順番は「追加スタック → 本体」とし、
  # 追加スタックの state にリソースが残っていれば、本体の plan を作る前に止める
  addon_subdirs=()
  addon_keys=()
  addon_backends=()
  for addon in "${addon_stacks[@]}"; do
    # stack_config は呼び出し元の変数を書き換えるため、サブシェルで追加スタックの値だけを取り出す
    read -r addon_subdir addon_key < <(stack_config "$addon" && printf '%s %s\n' "$stack_subdir" "$state_key")
    addon_subdirs+=("$addon_subdir")
    addon_keys+=("$addon_key")
    # 手元の追加スタックの backend.hcl は、本体の backend.hcl と同じディレクトリの <スタック>/ に置く（ガイド 5.4）
    addon_backends+=("$addon=$(dirname "$backend_file")/$addon_subdir/backend.hcl")
  done
  # 追加スタックの state は本体と同じバケットにあり、key だけが異なる前提で読む（各 backend.hcl.example と
  # ガイド 4.5）。手元の追加スタックの backend.hcl が別のバケットやリージョンを指していれば、読む前に止める
  python3 "$portal_dir/scripts/check_teardown.py" addon-backends "$backend_file" "${addon_backends[@]}"
  addon_states=()
  for i in "${!addon_stacks[@]}"; do
    addon="${addon_stacks[i]}"
    # 本体の backend.hcl の key だけを追加スタックのものに差し替えて読む
    tf_init "$infra_dir/${addon_subdirs[i]}" -backend-config="key=${addon_keys[i]}"
    # state のファイルがない（未配信）と、state pull は何も出力しない。読めない場合は set -e で止まる
    tf_at "$infra_dir/${addon_subdirs[i]}" state pull > "$work_dir/$addon.tfstate"
    addon_states+=("$addon=$work_dir/$addon.tfstate")
  done
  python3 "$portal_dir/scripts/check_teardown.py" addon-states "${addon_states[@]}"
fi
plan_args+=(-var-file="$vars_file" -out="$work_dir/approved.tfplan")
# 共通の Lambda ZIP は、変数 lambda_zip_path を持つスタック（本体・decision・notification_events）にだけ渡す
if grep -qs '^variable "lambda_zip_path"' "$stack_dir"/*.tf; then
  python3 "$portal_dir/scripts/build_lambda.py" --output "$work_dir/lambda.zip"
  plan_args+=(-var="lambda_zip_path=$work_dir/lambda.zip")
fi
tf_init "$stack_dir"
tf validate
tf plan "${plan_args[@]}"
(
  cd "$stack_dir"
  AWS_PROFILE="$terraform_profile" python3 "$portal_dir/scripts/check_plan.py" --saved-plan "$work_dir/approved.tfplan"
)
# 本体の撤去では、保護を外す apply（ガイド 5.4 の手順 3）が state に入っていることを apply の前に確かめる。
# 飛ばすと、保護のないリソースだけを消してから途中で失敗する
if [[ "$is_main_destroy" == true ]]; then
  tf show -json "$work_dir/approved.tfplan" > "$work_dir/plan.json"
  python3 "$portal_dir/scripts/check_teardown.py" main-protection "$work_dir/plan.json"
fi
tf show -no-color "$work_dir/approved.tfplan"
if [[ "$destroy" == true ]]; then
  # 撤去は取り消せないため、y や apply ではなく撤去するスタック名の入力を求める
  printf '\n上記 plan は %s スタックのリソースを削除します。撤去する場合はスタック名（%s）を入力してください: ' "$stack" "$stack"
  IFS= read -r confirmation || fail "撤去を中止しました。"
  [[ "$confirmation" == "$stack" ]] || fail "入力がスタック名（${stack}）と一致しないため、撤去を中止しました。"
else
  printf '\n上記 plan と Registry・IAM の変更を確認してください。反映する場合は apply と入力してください: '
  read -r confirmation || fail "反映を中止しました。"
  [[ "$confirmation" == "apply" ]] || fail "反映を中止しました。"
fi
if [[ "$destroy" == true && ( "$stack" == management || "$stack" == decision ) ]]; then
  # 撤去で output が消える前に、検査済み plan から再デプロイ先を取得する。
  api_target="$(tf show -json "$work_dir/approved.tfplan" | python3 -c '
import json
import sys

variables = json.load(sys.stdin)["variables"]
print(variables["rest_api_id"]["value"], variables["region"]["value"])
')"
  read -r api_id api_region <<<"$api_target"
fi
tf apply -input=false "$work_dir/approved.tfplan"
if [[ "$stack" != notification_events && "$is_main_destroy" != true ]]; then
  if [[ "$destroy" != true ]]; then
    api_id="$(tf output -raw rest_api_id)"
    api_region="$(tf output -raw api_region)"
  fi
  deployment_id="$(aws apigateway create-deployment --rest-api-id "$api_id" \
    --region "$api_region" --profile "$AWS_PROFILE" --query id --output text)"
  [[ -n "$deployment_id" && "$deployment_id" != None ]] || fail "REST API のデプロイ ID を取得できませんでした。"
  aws apigateway update-stage --rest-api-id "$api_id" --stage-name v1 \
    --patch-operations "op=replace,path=/deploymentId,value=$deployment_id" \
    --region "$api_region" --profile "$AWS_PROFILE" >/dev/null
fi
if [[ "$publishes_spa" != true ]]; then
  printf '\n%s\n' "$completion_message"
  exit 0
fi
spa_bucket="$(tf output -raw spa_bucket_name)"
distribution_id="$(tf output -raw cloudfront_distribution_id)"
tf output -json frontend_config > "$work_dir/frontend.json"
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
aws s3 sync "$portal_dir/frontend/dist/" "s3://$spa_bucket/" --delete --exclude index.html --cache-control 'public,max-age=31536000,immutable' --profile "$AWS_PROFILE"
aws s3 cp "$portal_dir/frontend/dist/index.html" "s3://$spa_bucket/index.html" --cache-control 'no-cache,no-store,must-revalidate' --content-type 'text/html' --profile "$AWS_PROFILE"
aws cloudfront create-invalidation --distribution-id "$distribution_id" --paths '/*' --profile "$AWS_PROFILE"
printf '\n配信が完了しました。README の dev 手動受入を実施してください。\n'
