variable "project" {
  description = "Project name, used as the first element of every resource name"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
}

variable "data_engineer_role_arn" {
  description = "ARN of the DataEngineer role used by the crawler and jobs"
  type        = string
}

variable "bucket_name" {
  description = "Name of the data bucket"
  type        = string
}

variable "private_subnet_id" {
  description = "Private subnet where Glue workers run"
  type        = string
}

variable "security_group_id" {
  description = "Security group for the Glue connection (needs the self-referencing ingress rule)"
  type        = string
}

variable "availability_zone" {
  description = "Availability zone of the private subnet"
  type        = string
}

variable "transform_script_path" {
  description = "Local path to transform.py"
  type        = string
}

variable "feature_engineer_script_path" {
  description = "Local path to feature_engineer.py"
  type        = string
}

variable "feature_group_name" {
  description = "Name of the Feature Store feature group the job writes to"
  type        = string
}

variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

