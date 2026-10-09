import boto3, json
bedrock = boto3.client("bedrock", region_name="us-east-1")

form = {
    "companyName": "Brigham Young University",
    "companyWebsite": "https://github.com/joellaedwards",     # a GitHub or portfolio URL is fine
    "intendedUsers": "0",                        # 0=Internal, 1=External, 2=Both
    "industryOption": "Education",
    "otherIndustryOption": "Higher Education",
    "useCases": "University coursework: retrieval-augmented offer generation and a "
                "customer-service agent over synthetic retail data. No production traffic, "
                "no real personal data.",
}
bedrock.put_use_case_for_model_access(formData=json.dumps(form).encode())
bedrock.get_use_case_for_model_access()   # raises ResourceNotFoundException if it didn't register