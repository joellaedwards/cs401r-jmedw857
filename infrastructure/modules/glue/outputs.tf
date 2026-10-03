output "database_name" {
  description = "Name of the Glue catalog database"
  value       = aws_glue_catalog_database.this.name
}

output "connection_name" {
  description = "Name of the Glue NETWORK connection (reused by the feature engineering job in Task 3)"
  value       = aws_glue_connection.this.name
}