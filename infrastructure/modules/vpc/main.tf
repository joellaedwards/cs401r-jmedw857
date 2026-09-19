# ── modules/vpc ──────────────────────────────────────────────────────────────
# Required resources (Task B1). Only these belong in this module:
#
#   aws_vpc
#   aws_subnet                      public only in Lab 1
#   aws_internet_gateway
#   aws_route_table
#   aws_route_table_association
#   aws_security_group
#
# Name everything from var.project and var.environment. A hardcoded
# project-environment literal anywhere under modules/ fails the rubric grep.
#
# Example of the naming pattern expected:
#
#   resource "aws_vpc" "this" {
#     cidr_block = var.vpc_cidr
#     tags       = { Name = "${var.project}-${var.environment}-vpc" }
#   }

# TODO: implement the six resources above.

resource "aws_vpc" "this" {
    cidr_block  = var.vpc_cidr
    tags        = { Name = "${var.project}-${var.environment}-vpc" } 
    enable_dns_support   = true
    enable_dns_hostnames = true
}

resource "aws_subnet" "public" {
    vpc_id = aws_vpc.this.id
    cidr_block = var.public_subnet_cidr
    availability_zone = var.availability_zone
    map_public_ip_on_launch = true
    tags = { Name = "${var.project}-${var.environment}-public-1" }
}

resource "aws_internet_gateway" "this" {
    vpc_id = aws_vpc.this.id
    tags = { Name = "${var.project}-${var.environment}-igw" }
}

resource "aws_route_table" "this" {
    vpc_id = aws_vpc.this.id
    route {
        cidr_block = var.main_route_cidr
        gateway_id = aws_internet_gateway.this.id
    }
}

resource "aws_route_table_association" "this" {
    subnet_id = aws_subnet.public.id
    route_table_id = aws_route_table.this.id
}

resource "aws_security_group" "this" {
    name = "${var.project}-${var.environment}-sagemaker-sg"
    description = "AWS Security Group for SageMaker"
    vpc_id = aws_vpc.this.id

    ingress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}