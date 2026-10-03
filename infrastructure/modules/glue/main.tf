resource "aws_glue_catalog_database" "this" {
  name = "${var.project}_${var.environment}"
}

resource "aws_glue_crawler" "raw" {
  name          = "${var.project}-${var.environment}-raw-crawler"
  database_name = aws_glue_catalog_database.this.name
  role          = var.data_engineer_role_arn

  s3_target {
    path = "s3://${var.bucket_name}/raw/customers/"
  }
}

resource "aws_s3_object" "transform_script" {
  bucket      = var.bucket_name
  key         = "artifacts/glue/transform.py"
  source      = var.transform_script_path
  source_hash = filemd5(var.transform_script_path)
}

resource "aws_glue_connection" "this" {
  name            = "${var.project}-${var.environment}-vpc-connection"
  connection_type = "NETWORK"

  physical_connection_requirements {
    availability_zone      = var.availability_zone
    security_group_id_list = [var.security_group_id]
    subnet_id              = var.private_subnet_id
  }
}

resource "aws_glue_job" "transform" {
  name              = "${var.project}-${var.environment}-transform"
  role_arn          = var.data_engineer_role_arn
  glue_version      = "4.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  connections       = [aws_glue_connection.this.name]

  default_arguments = {
    "--database_name" = aws_glue_catalog_database.this.name
    "--table_name"    = "customers"
    "--output_path"   = "s3://${var.bucket_name}/processed/customers/"
  }

  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${var.bucket_name}/${aws_s3_object.transform_script.key}"
  }
}

resource "aws_s3_object" "feature_engineer_script" {
  bucket      = var.bucket_name
  key         = "artifacts/glue/feature_engineer.py"
  source      = var.feature_engineer_script_path
  source_hash = filemd5(var.feature_engineer_script_path)
}

resource "aws_glue_job" "feature_engineer" {
  name              = "${var.project}-${var.environment}-feature-engineer"
  role_arn          = var.data_engineer_role_arn
  glue_version      = "4.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  connections       = [aws_glue_connection.this.name]

  default_arguments = {
    "--input_path"         = "s3://${var.bucket_name}/processed/customers/"
    "--output_path"        = "s3://${var.bucket_name}/features/customers/"
    "--feature_group_name" = var.feature_group_name
    "--region"             = var.aws_region
  }

  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${var.bucket_name}/${aws_s3_object.feature_engineer_script.key}"
  }
}