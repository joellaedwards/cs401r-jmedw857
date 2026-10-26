## Data Contract: processed/customers

### Producer
Team / process: Glue ETL job `northstar-dev-transform`

### Consumers
- Feature engineering job `northstar-dev-feature-engineer`
- (Future) Direct model training in Lab 3

### Grain
One row per transaction. A customer appears on many rows.

### Schema
| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
|transaction_id|string|no|Unique for each transaction. Formatted as TXN-{12 alphanumeric}. Not null-checked but no current nulls|
|customer_id|string|no|Format CUST-{8 digits}. Unique to each customer. One customer may have multiple transactions. Null values dropped.|
|purchase_date|date|no|Parsed from ISO 8601 or MM/dd/yyyy into date type|
|order_value|double|no|USD, gross. Missing values filled in with column median|
|num_items|int|no|Line items in the order. Missing values replaced with rounded median|
|payment_method|string|no|One of: credit_card, debit_card, gift_card, cash, unknown|
|channel|string|no|store, online, or unknown|
|store_id|string|no|STORE-{3 digits} or ONLINE, or unknown|
|product_category|string|no|Primary category for the order, or unknown|


### Quality Guarantees
- `customer_id` is never null (0 nulls in output, all rows with null customer_id are dropped)
- No duplicate `transaction_id` rows (a `customer_id` repeating across rows is expected, not a defect)
- `purchase_date` is a valid ISO 8601 date
- `purchase_date` falls between 2025-04-01 and 2026-06-30 inclusive
- `order_value` is >= 0
- `num_items` is an integer > 0
- Grain is preserved: more rows than distinct customer_ids

### SLA
- Data is available in `processed/customers/` within 2 hours of landing in `raw/customers/`

### Versioning
- Schema changes require a new S3 prefix (e.g., `processed/customers/v2/`)
- Breaking changes require consumer notification 5 business days in advance