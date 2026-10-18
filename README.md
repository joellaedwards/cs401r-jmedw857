# cs401r-jmedw857

CS 401R labs for the NorthStar Retail AI platform: infrastructure (Lab 1), data platform (Lab 2), and model development (Lab 3).

## Repo layout

| Path | What |
|---|---|
| `infrastructure/` | Terraform for the AWS platform (Lab 1) |
| `glue-scripts/` | Glue ETL: cleaning (`transform.py`) and feature engineering (`feature_engineer.py`) (Lab 2) |
| `churn_training_skeleton.py` | Task 1 churn model training: XGBoost, MLflow tracking, model registry (Lab 3) |
| `run_experiments.py` | Task 1 experiment runs |
| `model/final/` | The registered churn model and its metadata |
| `offer_generation/` | Track B retention-offer RAG pipeline (Lab 3) |
| `northstar-policy-docs/` | Policy corpus the RAG pipeline retrieves from |
| `evaluation_harness.py` | RAGAS evaluation harness for Track B |
| `notebooks/lab3-demo.ipynb` | End-to-end Lab 3 demo |
| `docs/` | Reports and evidence for every lab |

## Lab 3: Model Development

### Task 1: churn model

XGBoost on the 11 customer features from the Lab 2 feature store. The final model (depth 4, eta 0.05, 200 rounds) reaches validation AUC 0.783 against a recency-only baseline of 0.723.

```bash
python churn_training_skeleton.py \
    --feature-group-name northstar-dev-customer-features \
    --artifacts-bucket northstar-dev-data-<account> \
    --max-depth 4 --eta 0.05 --num-round 200 \
    --mlflow-arn <mlflow-app-arn> --register
```

- Report: `docs/lab3-evaluation-report.md`
- Evidence: `docs/lab3-evidence/` (`train-final.log`, `evaluation_metrics.json`, `slice-evaluation.csv`, plots, MLflow and registry exports)

### Track B: retention offer generation (RAG)

Generates a retention offer for an at-risk customer that only promises benefits the customer's loyalty tier actually has.

- **Retrieval:** policy sections, not products (there is no product catalog), embedded with Titan v2 and searched with a local FAISS index
- **Generation:** Claude Haiku 4.5 on Bedrock, using the corrected prompt templates in `offer_generation/prompts.py`

Needs AWS credentials with Bedrock access to `amazon.titan-embed-text-v2:0` and `us.anthropic.claude-haiku-4-5-20251001-v1:0`. Nothing else in AWS has to be running.

```bash
pip install boto3 numpy pandas faiss-cpu ragas datasets "langchain-community<0.4" langchain-aws

# one offer
python -m offer_generation.rag_pipeline --demo

# RAGAS evaluation on the 4 harness test cases
python -m offer_generation.rag_pipeline --eval                      # final config
python -m offer_generation.rag_pipeline --eval --no-grounding-rule  # baseline
python evaluation_harness.py --track B                              # same as the final config, via the harness
```

`rag_pipeline` options: `--query A|B|C|AB` (retrieval query, default AB) and `--raw-signals` (turn off signal interpretation).

- Design and results: `docs/lab3-model-design.md`
- Scores: `docs/lab3-evidence/rag_eval.csv`, with raw per-run output and generated offers in `docs/lab3-evidence/track-b/`
- Example offers: `docs/lab3-evidence/track-b-examples.md`

### Demo notebook

`notebooks/lab3-demo.ipynb` runs end to end:

1. Rebuilds customer features from `northstar-raw-sample.csv` with the Lab 2 logic.
2. Scores those customers with the churn model.
3. Generates offers for the most at-risk ones.
4. Summarizes the RAGAS results.

Needs `xgboost`, `scikit-learn`, `boto3`, `pandas`, `matplotlib`, `faiss-cpu` and Jupyter.

```bash
cd notebooks
jupyter nbconvert --to notebook --execute --inplace lab3-demo.ipynb
```

### Teardown

```bash
bash scripts/teardown-lab3.sh   # writes docs/lab3-teardown-output.txt
```
