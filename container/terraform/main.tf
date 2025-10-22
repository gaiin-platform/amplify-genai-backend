terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    # Configure backend in terraform.tfvars or via CLI
    # bucket = "your-terraform-state-bucket"
    # key    = "amplify-chat-fargate/terraform.tfstate"
    # region = "us-east-1"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge(
      {
        Environment = var.environment
        Service     = "amplify-chat-fargate"
        ManagedBy   = "terraform"
      },
      var.tags
    )
  }
}

# Data sources for existing resources
data "aws_caller_identity" "current" {}

data "aws_iam_policy" "lambda_js_policy" {
  name = "amplify-${var.deployment_name}-amplify-js-${var.environment}-iam-policy"
}

data "aws_iam_policy" "bedrock_policy" {
  name = "amplify-${var.deployment_name}-amplify-js-${var.environment}-bedrock-policy"
}

locals {
  service_name = "amplify-chat-fargate"
  common_tags = {
    Environment = var.environment
    Service     = local.service_name
  }
}
