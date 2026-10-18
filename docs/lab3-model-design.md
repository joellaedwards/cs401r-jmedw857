# Lab 3 Model Design

**Track: B, Offer Generation (RAG over the policy corpus using Bedrock)**

# Part 1: Design justification (~700 words)

## 1. Churn model: why gradient boosting
We chose to use XGBoost because we don't have enough data to use a neural network. We only have 9,999 customers, and only 6,999 of them are used for training. A neural network trained on that few rows of tabular data would mostly memorize the training set, and it would be harder to explain. Churn also depends on thresholds and combinations of different features that a logistic regression model can't capture on its own. For example, a customer who bought recently but only once in the last 180 days is still risky. With 11 numeric features, logistic regression would need us to guess and hand-build those combinations, and it would still miss the ones we didn't think of. XGBoost learns them from the data. It also handles the class imbalance directly: about 22% of customers churn, so we set scale_pos_weight to 3.545, computed from the training split.

The retention team also needs to understand why a customer is flagged. XGBoost gives us feature importance (feature-importance.png), which shows which behaviors drive the predictions, and that is much harder to get from a neural network.

We would switch to logistic regression if the retention team needed a simple scorecard where every feature has one clear weight, even if it cost some accuracy. We would switch to a neural network if we had millions of customers and raw data, like every transaction in order or website clicks, instead of 11 summary features.


## 2. What the features bought

| Model | Test AUC |
|---|---|
| Recency-only baseline (`days_since_last_purchase`) | 0.7226 |
| Full model (11 features) | 0.7829 |
| Lift | +0.0603, 95% bootstrap CI [0.0415, 0.0796] |

AUC (Area Under the Curve) is how well the model ranks churners above non-churners. The days_since_last_purchase by itself gets an alright score of 0.72. When you add the other 10 features, the AUC goes up by 0.06. It may not seem like much, but the 95% confidence interval on the lift (0.042 to 0.080) never touches 0, so we know that difference is based on actual performance, not just luck. Because it's not only based on luck, it's an important difference even if it's small. However, it is important to acknowledge how well days_since_last_purchase predicts on its own. Customers who stopped buying months ago are easy to spot, and you don't need machine learning to find them. The extra features matter for the harder group: customers who were still buying right up to the cutoff, which the lab says is about a third of churners.

The feature importance plot (feature-importance.png) shows which features did the work. purchase_frequency_180d and days_since_last_purchase were the strongest (about 130 average gain each), followed by purchase_frequency_30d (~75), purchase_frequency_90d (~71), and avg_basket_size_6m (~52). It also shows that not all 11 features strengthen the prediction. customer_tenure_days (~11), online_to_store_ratio (~18), and category_diversity_score (~19) contributed the least. That is notable because category diversity and channel mix were the features Lab 2 built specifically to catch the customers who were still buying. Purchase frequency ended up doing that job instead: a customer who still buys, but less often than before, gets flagged.

## 3. LLM system: why RAG over fine-tuning

The main alternative to RAG is fine-tuning Haiku on example offers. We chose RAG because all the facts we need live in four policy documents, and those documents change over time (POL-LOY-011 §7 allows changes with 60 days' notice). With RAG, we update the document and the next offer uses the new rule. With fine-tuning, the old rules would be baked into the model, and we would have to retrain after every policy change. Fine-tuning also needs hundreds or thousands of good example offers to learn from, and we don't have any. We only have four policy documents and four test cases.

RAG also helps fight against hallucinations. The model gets the exact policy text in front of it every time, so it doesn't have to rely on memory, and we can trace each offer back to the sections it was based on (the retrieved chunk IDs in track-b-examples.md).

The primary production risk is that RAG reduces hallucinations but doesn't stop them. In F1, the model invented "100 bonus points" for a Bronze member even though the policy was in front of it and the grounding rule banned that phrase. In F2, it presented benefits a Gold member already has as something "this offer unlocks". POL-LOY-011 §5.1 says an offer like that is invalid, so NorthStar would either need Director approval to honor it or deal with an upset customer.



## 4. What I would do differently with 10× the time or data

With 10× the time, we would add a check after generation that pulls every benefit and number out of the offer and compares it to the customer's tier in the POL-LOY-011 §4 table, then rejects or regenerates the offer if anything doesn't match. That would have caught both of our failure cases, which the prompt rules and the faithfulness score missed. We would also read the customer's tier from the loyalty system (trailing 12-month spend) instead of the feature store, so the tier always matches policy.

# Part 2: Track B pipeline and evaluation

## 5. Pipeline design

Code: `offer_generation/rag_pipeline.py` (retrieval, generation, evaluation CLI) and `offer_generation/prompts.py` (prompts).

| Part | Choice |
|---|---|
| Corpus | `northstar-policy-docs/` (POL-RET-004, POL-LOY-011, POL-SHP-007, KB-FAQ-022) |
| Chunking | One chunk per `##`/`###` section, no overlap: 44 chunks, 36–254 words (median 80). Each chunk is labeled `[doc ID \| title \| heading]` |
| Embeddings | Amazon Titan Text Embeddings v2 (`amazon.titan-embed-text-v2:0`), 1024-dim, normalized |
| Vector store | FAISS `IndexFlatIP` (exact search; normalized vectors so inner product = cosine), local |
| Retrieval query | AB: Template 4 options A (tier benefits) and B (constraints), top 4 each, deduplicated (up to 8 sections) |
| Re-ranking | None |
| Generator model | Claude Haiku 4.5 (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) via Bedrock Converse |
| Temperature / max tokens | 0.2 / 500 |
| Output | Subject + 2–3 sentence body + one-sentence rationale |
| Input profile | Churn probability, loyalty tier, days since last purchase, lifetime value, average order value, category diversity, online-to-store ratio, top categories |

- **Embeddings:** Titan v2 over Cohere Embed because it was already enabled and verified in Pre-Lab 3, it's the same embedding model the RAGAS judge uses, and it handles short policy text well.
- **Vector store:** FAISS because 44 chunks fit easily in memory, it's free and local, and exact search is instant at this size. OpenSearch Serverless costs money even when idle.
- **Chunking:** one chunk per policy section with no overlap, because each section is one self-contained rule (for example, §4 is the whole tier-benefits table). Splitting by size would cut a rule or table in half.
- **Generator:** Claude Haiku 4.5 because it's fast and cheap enough for one call per customer and follows a fixed format well.
- **Re-ranking:** none. The two queries already return the tier-benefits table (§4) and the retention-offer terms (§5.1) for every test case, so a re-ranker would add a model call without changing what's retrieved.
- **Policy, not products:** there is no product catalog. Retrieval tells us what we're allowed to offer, and the product category comes from the customer's top categories.

### Tier mismatch (feature store vs. policy)

Lab 2's loyalty_tier comes from total lifetime value, but POL-LOY-011 §2 defines tier by trailing 12-month spend, so the two can disagree near a threshold. Our system handles this three ways: the prompt tells the model to phrase tier benefits conditionally ("as a Gold member you currently receive...") and never claim an upgrade, `interpret_signals()` warns when a customer is within 10% of the next tier (the Silver test case is $15 under Gold) that those benefits are not active, and only the customer's own tier guidelines are given to the model. This lowers the risk but doesn't remove it; the real fix is reading tier from the loyalty system (§4).

## 6. Prompt template fixes

**Template 3: the three planted errors**
- Gold upper bound was $9,999.99 → corrected to $4,999.99
- Platinum threshold was $10,000+ → corrected to $5,000+
- Gold "free expedited shipping on orders over $200" → removed; no tier gets free expedited shipping
- Also added explicit "NOT entitled to" lists for every tier, and replaced "discount / bonus points" incentives with entitlements the policy actually grants, because the corpus defines no discount program

**Template 1: tier never reached the model**
- The customer's tier is now in the system prompt
- Only that tier's guideline block is injected, not all four, so higher-tier benefits are never in front of the model
- Tier claims are phrased conditionally ("as a Silver member you currently receive…") because Lab 2 tier comes from lifetime value while policy uses trailing 12-month spend

**Template 2: raw numbers with no interpretation**
- `interpret_signals()` adds a plain-English line per signal (in-store vs. online shopper, narrow vs. broad categories, lapsed days, high AOV → no % discount, near-threshold warning)
- `--raw-signals` runs the uninterpreted starter version for comparison
- Offer body set to 2–3 sentences (the lab's required output) instead of the starter's "one paragraph"

**Templates 4 and 5**

- Template 4: all three query options are implemented (`--query A`, `B`, `C`). The final config runs A (tier benefits) and B (constraints) as two separate passes and combines them, because one query can't pull both the benefits table and the retention-offer limits. We did not measure A, B, and C separately.
- Template 5: the guardrails are included unchanged in the system prompt.

**Final prompts:** `offer_generation/prompts.py`

## 7. Evaluation setup

| Item | Value |
|---|---|
| Harness | `evaluation_harness.py --track B` (RAGAS: faithfulness, answer_relevancy, context_recall) |
| Test cases | 4 (Gold, Bronze, Platinum, Silver) |
| Judge model | Claude Haiku 4.5 (`us.anthropic.claude-haiku-4-5-20251001-v1:0`), temperature 0 |
| Judge embeddings | Amazon Titan Text Embeddings v2 (`amazon.titan-embed-text-v2:0`) |
| Contexts given to the judge | The retrieved policy sections plus the customer profile |
| Runs per config | 2 (baseline and improved, 4 cases each = 16 scored offers) |
| Evidence | `docs/lab3-evidence/rag_eval.csv`, `docs/lab3-evidence/track-b/` |

Harness fix: we raised the judge's `max_tokens` to 4096 in `build_bedrock_judge()`. With the default, the judge's faithfulness checks on longer offers got cut off and came back as NaN. This doesn't change how anything is scored; it just lets every case finish.

## 8. Results

### Per-case scores (final config)

| Customer | Tier | Run | Faithfulness | Answer relevancy | Context recall |
|---|---|---|---|---|---|
| CUST-10000317 | Gold | 1 | 0.286 | 0.358 | 1.000 |
| CUST-10000904 | Bronze | 1 | 0.188 | 0.521 | 1.000 |
| CUST-10000122 | Platinum | 1 | 0.357 | 0.221 | 0.333 |
| CUST-10000588 | Silver | 1 | 0.308 | 0.295 | 1.000 |
| CUST-10000317 | Gold | 2 | 0.308 | 0.388 | 1.000 |
| CUST-10000904 | Bronze | 2 | 0.071 | 0.489 | 1.000 |
| CUST-10000122 | Platinum | 2 | 0.250 | 0.249 | 0.333 |
| CUST-10000588 | Silver | 2 | 0.267 | 0.308 | 1.000 |

### Mean and range

| Metric | Mean | Min | Max | Original target (not graded) |
|---|---|---|---|---|
| Faithfulness | 0.254 | 0.071 | 0.357 | ≥ 0.80 |
| Answer relevancy | 0.354 | 0.221 | 0.521 | ≥ 0.75 |
| Context recall | 0.833 | 0.333 | 1.000 | ≥ 0.70 |

## 9. What limits each metric

### Faithfulness

Faithfulness (mean 0.254) counts every claim the contexts don't support. Most offers include warm-up lines like "we'd love to welcome you back", which count as unsupported. The lowest score (Bronze run 2, 0.071) comes from an invented promotion: "earn an extra 100 bonus points (worth $5) when you spend $75 or more". Other offers add plausible details that aren't in policy, like "first pick on new arrivals" and "this offer unlocks 24-hour early access" (Gold run 2). The Rationale line also counts, and it is full of reasoning ("removes friction") rather than policy facts. We pass the customer profile as a context, so true facts about the customer are not marked unsupported.

### Answer relevancy

Answer relevancy (mean 0.354) guesses the question from the offer and compares it to the real question. The real questions are instructions ("Generate a retention offer for this...") but the answers are marketing emails, so they never match closely. Platinum is lowest in both runs (0.221, 0.249): its request describes a "high-value member with broad category interest and a large average order", but the offer is about the $100 gear credit, which is correct but doesn't echo those words. Bronze is highest (0.521, 0.489) because its offer repeats the request's details (in store, apparel).

### Context recall

Context recall (mean 0.833) checks whether each statement in the ground truth is supported by the contexts. Gold, Bronze, and Silver score 1.000 in every run, since §4 Tier Benefits and §5.1 Retention Offers were retrieved for every case. Platinum is stuck at 0.333 because its ground truth includes grading commentary that isn't in any policy document ("a heavy percentage discount destroys margin on a $447 average order"), so no retrieval can find it.

## 10. One pipeline change: grounding rule

**Change:**

We added one rule to the system prompt (`GROUNDING_RULE` in `prompts.py`): only state benefits the way the policy states them, don't invent mechanics or programs ("bonus points", "we're activating this for you", "no strings attached", "stacks with"), and only use a birthday reward as a birthday reward. We added it because early offers invented exactly those things. The baseline is the same pipeline with the rule turned off (`--no-grounding-rule`); everything else is identical.

**Before / after** (2 runs each, 4 cases per run)

| Metric | Baseline mean (min–max) | Improved mean (min–max) |
|---|---|---|
| Faithfulness | 0.320 (0.067–0.500) | 0.254 (0.071–0.357) |
| Answer relevancy | 0.384 (0.246–0.541) | 0.354 (0.221–0.521) |
| Context recall | 0.833 (0.333–1.000) | 0.833 (0.333–1.000) |

**Did it help?**

No, not measurably. Faithfulness actually went down (0.320 to 0.254), but the ranges overlap a lot and identical runs can move faithfulness by about ±0.1, so the difference is within noise. Context recall didn't change, which makes sense because the rule doesn't affect retrieval. The rule did cut the invented phrases it names from 3 to 1, but the one left was "100 bonus points", which the rule names explicitly (F1). A prompt rule is only a request the model can ignore, which is why a check after generation (§4) is the better fix.

## 11. Failure cases

See [lab3-evidence/track-b-examples.md](lab3-evidence/track-b-examples.md).

F1 is an invented promotion ("100 bonus points") that the model wrote even with the grounding rule on. F2 presents benefits a Gold member already has as something "this offer unlocks" and adds details that aren't in policy, which faithfulness didn't flag. The fix for both is a check after generation that compares every benefit and number in the offer to the customer's tier in the POL-LOY-011 §4 table.

## 12. Offer checklist

Checked across the 16 scored offers in `docs/lab3-evidence/track-b/offers_*.md`.

| Criterion | Result | Evidence |
|---|---|---|
| All three planted errors in Template 3 found and corrected | Pass | §6 |
| No offer promises free expedited shipping | Pass | No offer mentions expedited shipping |
| No offer implies final sale merchandise is returnable | Pass | No offer mentions returning final sale items |
| Platinum offer does not lead with a discount percentage | Pass | All 4 Platinum offers lead with the $100 gear credit |
| No offer claims a benefit above the customer's tier | Pass | None in the 16 scored offers, though an earlier demo run offered a Gold member 2× points (the Platinum rate) |
| Every offer includes incentive + known category + CTA | Pass | Every offer has a tier benefit, a top category, and a call to action |
| Adversarial prompt injection rejected | Not tested | The 4 harness cases have no adversarial input, and the pipeline doesn't put free-text user input into the prompt |
| System prompt not revealed when asked | Not tested | Same as above |

## 13. Demo

`notebooks/lab3-demo.ipynb`: churn model scores customers rebuilt from `northstar-raw-sample.csv`, then the RAG pipeline writes offers for the most at-risk ones.
