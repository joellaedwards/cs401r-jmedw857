terraform {
  backend "s3" {
    bucket         = "northstar-tfstate-860366539138"
    key            = "dev/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "northstar-tfstate-lock"
  }
}