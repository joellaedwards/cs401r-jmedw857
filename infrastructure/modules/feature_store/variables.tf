variable "project" {
  description = "Project name, used as the first element of every resource name"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
}

variable "bucket_name" {
  description = "Name of the data bucket that backs the offline store"
  type        = string
}

variable "role_arn" {
  description = "Execution role for the feature group (the DataEngineer role)"
  type        = string
}