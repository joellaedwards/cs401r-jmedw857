| Component | Monthly Estimate | Key Assumptions | One Optimization |
|---|---|---|---|
| SageMaker Studio | $2,203.20 | 2 hrs/day 15 days/month at $30/hr | Fewer data scientists. One per each of the three teams brings the cost per hour down from $30 to $3  |
| S3 storage | $4.94 | 200 GB at $0.023/GB |Decrease the number of GET requests -- get all the data at once and then divy it out as needed |
| Internet Gateway | $5.26 | $0.01/GB data transfer | Fewer data transfers |
| DynamoDB (state lock) | $25.00 | On-demand, near-zero reads |I had us storing 100 GB. Storing less in DynamoDB saves money |
| S3 state bucket | $0.01 | Minimal storage | Keep to the 24 months lifecycle policy to recycle old data |
| **Total** | **$2,238.41** | | |