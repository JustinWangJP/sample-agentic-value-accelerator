# 本体スタックと追加スタックが共通に使うモジュール（discovery_client・lambda_log_group）を確かめる。
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "111122223333" }
  }
  mock_data "aws_region" {
    defaults = { region = "us-west-2" }
  }
}

run "discovery_client_without_customer_managed_key" {
  command = plan
  module {
    source = "./modules/discovery_client"
  }
  variables {
    client = {
      token_url         = "https://example.auth.us-east-1.amazoncognito.com/oauth2/token"
      client_id         = "discovery-client"
      client_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-AbCdEf"
      scope             = ""
    }
  }
  assert {
    condition     = output.kms_policy == null && length(output.kms_statements) == 0
    error_message = "AWS 管理キー（kms_key_arn が null）の場合は KMS の権限を付けないでください。"
  }
  assert {
    condition = output.environment == {
      DISCOVERY_TOKEN_URL         = "https://example.auth.us-east-1.amazoncognito.com/oauth2/token"
      DISCOVERY_CLIENT_ID         = "discovery-client"
      DISCOVERY_CLIENT_SECRET_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-AbCdEf"
      DISCOVERY_SCOPE             = ""
    }
    error_message = "発見用 M2M の環境変数（DISCOVERY_*）を client の設定から作ってください。"
  }
}

run "discovery_client_with_customer_managed_key" {
  command = plan
  module {
    source = "./modules/discovery_client"
  }
  variables {
    client = {
      token_url         = "https://example.auth.us-east-1.amazoncognito.com/oauth2/token"
      client_id         = "discovery-client"
      client_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-AbCdEf"
      scope             = ""
    }
    kms_key_arn = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000000"
  }
  assert {
    # SecretARN は完全一致（StringEquals）で、対象の secret だけに限る（#49）
    condition = (
      length(output.kms_statements) == 1
      && output.kms_statements[0].Sid == "DiscoverySecretKey"
      && output.kms_statements[0].Effect == "Allow"
      && output.kms_statements[0].Action == ["kms:Decrypt"]
      && output.kms_statements[0].Resource == "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000000"
      && keys(output.kms_statements[0].Condition) == ["StringEquals"]
      && output.kms_statements[0].Condition.StringEquals["kms:ViaService"] == "secretsmanager.us-east-1.amazonaws.com"
      && output.kms_statements[0].Condition.StringEquals["kms:EncryptionContext:SecretARN"] == "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-AbCdEf"
      && length(output.kms_statements[0].Condition.StringEquals) == 2
    )
    error_message = "kms:Decrypt は、そのキーに限り、Secrets Manager 経由と対象 secret の暗号化コンテキストの完全一致を条件にしてください。"
  }
}

run "discovery_client_rejects_wildcard_secret_arn" {
  command = plan
  module {
    source = "./modules/discovery_client"
  }
  variables {
    client = {
      token_url         = "https://example.auth.us-east-1.amazoncognito.com/oauth2/token"
      client_id         = "discovery-client"
      client_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-*"
      scope             = ""
    }
  }
  expect_failures = [var.client]
}

run "discovery_client_rejects_partial_secret_arn" {
  command = plan
  module {
    source = "./modules/discovery_client"
  }
  variables {
    client = {
      token_url         = "https://example.auth.us-east-1.amazoncognito.com/oauth2/token"
      client_id         = "discovery-client"
      client_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery"
      scope             = ""
    }
  }
  expect_failures = [var.client]
}

run "lambda_log_group_arn_is_known_on_first_plan" {
  command = plan
  module {
    source = "./modules/lambda_log_group"
  }
  variables {
    function_name = "registry-portal-dev-management"
  }
  assert {
    condition     = aws_cloudwatch_log_group.this.name == "/aws/lambda/registry-portal-dev-management" && aws_cloudwatch_log_group.this.retention_in_days == 365
    error_message = "Lambda のロググループは /aws/lambda/<関数名> とし、365日保存してください。"
  }
  assert {
    condition     = output.arn == "arn:aws:logs:us-west-2:111122223333:log-group:/aws/lambda/registry-portal-dev-management"
    error_message = "ロググループの ARN は、名前と plan 時に確定するアカウント ID・リージョンから組み立ててください。"
  }
}

# 本体・decision・notification_events の log_retention_in_days は、すべてこのモジュールの変数で検証する
run "lambda_log_group_rejects_unsupported_retention" {
  command = plan
  module {
    source = "./modules/lambda_log_group"
  }
  variables {
    function_name     = "registry-portal-dev-management"
    retention_in_days = 10
  }
  expect_failures = [var.retention_in_days]
}
