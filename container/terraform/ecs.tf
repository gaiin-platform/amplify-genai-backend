# ECS Cluster

resource "aws_ecs_cluster" "main" {
  name = "${var.deployment_name}-${local.service_name}-${var.environment}"

  setting {
    name  = "containerInsights"
    value = var.enable_container_insights ? "enabled" : "disabled"
  }

  tags = merge(
    local.common_tags,
    {
      Name = "${local.service_name}-cluster"
    }
  )
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name = aws_ecs_cluster.main.name

  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight           = 1
    base             = 1
  }
}

# CloudWatch Log Group

resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/${var.deployment_name}-${local.service_name}-${var.environment}"
  retention_in_days = var.log_retention_days

  tags = merge(
    local.common_tags,
    {
      Name = "${local.service_name}-logs"
    }
  )
}

# ECS Task Execution Role (for pulling images, writing logs)

resource "aws_iam_role" "task_execution" {
  name = "${var.deployment_name}-${local.service_name}-${var.environment}-task-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "task_execution_policy" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "task_execution_secrets" {
  name = "secrets-access"
  role = aws_iam_role.task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:*"
        ]
      }
    ]
  })
}

# ECS Task Role (for application permissions - same as Lambda)

resource "aws_iam_role" "task" {
  name = "${var.deployment_name}-${local.service_name}-${var.environment}-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

# Attach the same policies as Lambda
resource "aws_iam_role_policy_attachment" "task_lambda_js_policy" {
  role       = aws_iam_role.task.name
  policy_arn = data.aws_iam_policy.lambda_js_policy.arn
}

resource "aws_iam_role_policy_attachment" "task_bedrock_policy" {
  role       = aws_iam_role.task.name
  policy_arn = data.aws_iam_policy.bedrock_policy.arn
}

# ECS Task Definition

resource "aws_ecs_task_definition" "main" {
  family                   = "${var.deployment_name}-${local.service_name}-${var.environment}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "chat-service"
      image     = var.container_image != "" ? var.container_image : "${aws_ecr_repository.chat_service.repository_url}:latest"
      essential = true

      portMappings = [
        {
          containerPort = var.container_port
          protocol      = "tcp"
        }
      ]

      environment = [
        { name = "PORT", value = tostring(var.container_port) },
        { name = "NODE_ENV", value = "production" },
        { name = "STREAM", value = "true" },
        { name = "ALLOWED_ORIGINS", value = var.allowed_origins },
        { name = "COGNITO_USER_POOL_ID", value = var.cognito_user_pool_id },
        { name = "COGNITO_CLIENT_ID", value = var.cognito_client_id },
        { name = "IDP_PREFIX", value = var.idp_prefix },
        { name = "DEP_REGION", value = var.aws_region },
        # All other env vars from serverless.yml should be added here
        # These will be set via tfvars or environment-specific configs
        { name = "S3_FILE_TEXT_BUCKET_NAME", value = "amplify-${var.deployment_name}-lambda-${var.environment}-file-text" },
        { name = "S3_IMAGE_INPUT_BUCKET_NAME", value = "amplify-${var.deployment_name}-lambda-${var.environment}-image-input" },
        { name = "S3_RAG_INPUT_BUCKET_NAME", value = "amplify-${var.deployment_name}-lambda-${var.environment}-rag-input" },
        { name = "HASH_FILES_DYNAMO_TABLE", value = "amplify-${var.deployment_name}-lambda-${var.environment}-hash-files" },
        { name = "CHAT_USAGE_DYNAMO_TABLE", value = "amplify-${var.deployment_name}-lambda-${var.environment}-chat-usage" },
        { name = "REQUEST_STATE_DYNAMO_TABLE", value = "amplify-${var.deployment_name}-amplify-js-${var.environment}-request-state" },
        { name = "TRACE_BUCKET_NAME", value = "amplify-${var.deployment_name}-lambda-${var.environment}-chat-traces" },
        { name = "ASSISTANT_QUEUE_NAME", value = "amplify-${var.deployment_name}-amplify-${var.deployment_name}-amplify-js-${var.environment}-assistant-queue" },
        { name = "ASSISTANT_TASK_RESULTS_BUCKET_NAME", value = "amplify-${var.deployment_name}-amplify-${var.deployment_name}-amplify-js-${var.environment}-ast-results" },
        { name = "ASSISTANT_LOGS_BUCKET_NAME", value = "amplify-${var.deployment_name}-${var.environment}-assistant-chat-logs" },
        { name = "ASSISTANTS_ALIASES_DYNAMODB_TABLE", value = "amplify-${var.deployment_name}-assistants-${var.environment}-assistant-aliases" },
        { name = "ASSISTANTS_DYNAMODB_TABLE", value = "amplify-${var.deployment_name}-assistants-${var.environment}-assistants" },
        { name = "API_KEYS_DYNAMODB_TABLE", value = "amplify-${var.deployment_name}-object-access-${var.environment}-api-keys" },
        { name = "COST_CALCULATIONS_DYNAMO_TABLE", value = "amplify-${var.deployment_name}-lambda-${var.environment}-cost-calculations" },
        { name = "HISTORY_COST_CALCULATIONS_DYNAMO_TABLE", value = "amplify-${var.deployment_name}-lambda-${var.environment}-history-cost-calculations" },
        { name = "MODEL_RATE_TABLE", value = "amplify-${var.deployment_name}-chat-billing-${var.environment}-model-rates" },
        { name = "GROUPS_DYNAMO_TABLE", value = "amplify-${var.deployment_name}-object-access-${var.environment}-amplify-groups" },
        { name = "GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE", value = "amplify-${var.deployment_name}-assistants-${var.environment}-group-assistant-conversations" },
        { name = "S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME", value = "amplify-${var.deployment_name}-assistants-${var.environment}-group-conversations-content" },
        { name = "ADMIN_DYNAMODB_TABLE", value = "amplify-${var.deployment_name}-admin-${var.environment}-admin-configs" },
        { name = "AGENT_STATE_DYNAMODB_TABLE", value = "amplify-${var.deployment_name}-agent-loop-${var.environment}-agent-state" },
        { name = "DATASOURCE_REGISTRY_DYNAMO_TABLE", value = "amplify-${var.deployment_name}-amplify-js-${var.environment}-datasource-registry" },
      ]

      # Secrets can be added here if needed
      # secrets = [
      #   {
      #     name      = "LLM_ENDPOINTS_SECRETS"
      #     valueFrom = "arn:aws:secretsmanager:..."
      #   }
      # ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "wget --no-verbose --tries=1 --spider http://localhost:${var.container_port}/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
    }
  ])

  tags = local.common_tags
}

# ECS Service

resource "aws_ecs_service" "main" {
  name            = "${var.deployment_name}-${local.service_name}-${var.environment}"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.main.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    assign_public_ip = false
    security_groups  = [aws_security_group.ecs_tasks.id]
    subnets          = var.private_subnet_ids
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.main.arn
    container_name   = "chat-service"
    container_port   = var.container_port
  }

  deployment_configuration {
    maximum_percent         = 200
    minimum_healthy_percent = 100
    deployment_circuit_breaker {
      enable   = true
      rollback = true
    }
  }

  health_check_grace_period_seconds = var.health_check_grace_period

  enable_ecs_managed_tags = true
  propagate_tags          = "SERVICE"

  depends_on = [
    aws_lb_listener.http,
    aws_iam_role_policy_attachment.task_lambda_js_policy,
    aws_iam_role_policy_attachment.task_bedrock_policy
  ]

  tags = local.common_tags
}

# Auto Scaling

resource "aws_appautoscaling_target" "ecs" {
  max_capacity       = var.max_capacity
  min_capacity       = var.min_capacity
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.main.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "ecs_cpu" {
  name               = "${var.deployment_name}-${local.service_name}-${var.environment}-cpu-scaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.ecs.resource_id
  scalable_dimension = aws_appautoscaling_target.ecs.scalable_dimension
  service_namespace  = aws_appautoscaling_target.ecs.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = 70.0
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

resource "aws_appautoscaling_policy" "ecs_memory" {
  name               = "${var.deployment_name}-${local.service_name}-${var.environment}-memory-scaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.ecs.resource_id
  scalable_dimension = aws_appautoscaling_target.ecs.scalable_dimension
  service_namespace  = aws_appautoscaling_target.ecs.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageMemoryUtilization"
    }
    target_value       = 80.0
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}
