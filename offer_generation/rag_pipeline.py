"""
rag pipeline for track B offer generation.

searches the 4 policy docs (not products, there's no product catalog), then
sends the retrieved sections + prompts from prompts.py to haiku on bedrock.

how to run (from repo root):
    # one offer for test case 1
    python -m offer_generation.rag_pipeline --demo

    # ragas eval on the 4 harness test cases
    python -m offer_generation.rag_pipeline --eval --query AB
    python -m offer_generation.rag_pipeline --eval --no-grounding-rule  # baseline, before the improvement
    python -m offer_generation.rag_pipeline --eval --query A      # compare queries (template 4)
    python -m offer_generation.rag_pipeline --eval --raw-signals  # no signal notes (template 2)

needs: pip install boto3 numpy faiss-cpu ragas datasets "langchain-community<0.4" langchain-aws
"""

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import boto3  # type: ignore[import-untyped]
import faiss  # type: ignore[import-untyped]
import numpy as np

from offer_generation import prompts

REPO_ROOT = Path(__file__).resolve().parent.parent

# policy docs copied into this repo from the lab template. use --docs-dir to change
DEFAULT_DOCS_DIR = REPO_ROOT / "northstar-policy-docs"

# same haiku model the harness uses to judge
GENERATION_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
TOP_K = 4


# ── loading docs and vector store ───────────────────────────────────────────────

def load_chunks(docs_dir: Path) -> list[dict]:
    # one chunk per ## or ### section (markdownnn). each chunk labeled [doc id | title | heading]
    chunks = []
    for path in sorted(docs_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        m = re.search(r"\*\*Document ID:\*\*\s*(\S+)", text)
        doc_id = m.group(1) if m else path.stem
        title = text.splitlines()[0].lstrip("# ").strip()

        sections = re.split(r"(?m)^(?=#{2,3} )", text)
        for sec in sections[1:]:              # [0] is just the doc header stuff
            body = sec.strip().rstrip("-").strip()
            heading = body.splitlines()[0].lstrip("# ").strip()
            if len(body.splitlines()) < 2:    # skip headings with nothing under them
                continue
            chunks.append({
                "doc_id": doc_id,
                "heading": heading,
                "text": f"[{doc_id} | {title} | {heading}]\n{body}",
            })
    if not chunks:
        raise FileNotFoundError(f"No policy docs found in {docs_dir}")
    return chunks


class PolicyVectorStore:
    # embeds every chunk once at startup into a local FAISS index (44 chunks).
    # IndexFlatIP = exact inner-product search; vectors are normalized so that's cosine sim
    def __init__(self, docs_dir: Path, region: str):
        self.bedrock = boto3.client("bedrock-runtime", region_name=region)
        self.chunks = load_chunks(docs_dir)
        vectors = np.vstack([self._embed(c["text"]) for c in self.chunks])
        self.index = faiss.IndexFlatIP(vectors.shape[1])
        self.index.add(vectors)

    def _embed(self, text: str) -> np.ndarray:
        resp = self.bedrock.invoke_model(
            modelId=EMBEDDING_MODEL_ID,
            body=json.dumps({"inputText": text, "dimensions": 1024, "normalize": True}),
        )
        return np.array(json.loads(resp["body"].read())["embedding"], dtype=np.float32)

    def search(self, query: str, k: int = TOP_K, doc_ids: set | None = None) -> list[dict]:
        # rank everything (only 44 chunks) so the doc_ids filter can't come up short
        _, order = self.index.search(self._embed(query)[None, :], self.index.ntotal)
        hits = [self.chunks[i] for i in order[0]
                if doc_ids is None or self.chunks[i]["doc_id"] in doc_ids]
        return hits[:k]


# ── pipeline ──────────────────────────────────────────────────────────────────

class OfferRAG:
    def __init__(self, docs_dir: Path = DEFAULT_DOCS_DIR, region: str = "us-east-1",
                 query_mode: str = "AB", interpret: bool = True,
                 grounding_rule: bool = True):
        self.store = PolicyVectorStore(docs_dir, region)
        self.bedrock = self.store.bedrock
        self.query_mode = query_mode
        self.interpret = interpret
        self.grounding_rule = grounding_rule

    def retrieve(self, customer: dict) -> list[dict]:
        # template 4 A, B, C are single passes. AB run a and b separate then drop dupl.
        passes: dict[str, list[tuple[str, set | None]]] = {
            "A": [(prompts.query_a(customer), None)],
            "B": [(prompts.query_b(customer), None)],
            "C": [(prompts.query_c(customer), prompts.QUERY_C_FILTER)],
            "AB": [(prompts.query_a(customer), None), (prompts.query_b(customer), None)],
        }

        seen, hits = set(), []
        for query, doc_filter in passes[self.query_mode]:
            for chunk in self.store.search(query, TOP_K, doc_filter):
                if chunk["text"] not in seen:
                    seen.add(chunk["text"])
                    hits.append(chunk)
        return hits

    def profile_text(self, customer: dict) -> str:
        return prompts.PROFILE.format(**{
            **customer,
            "customer_id": customer.get("customer_id", "unknown"),
            "top_categories": ", ".join(customer["top_categories"]),
        })

    def build_messages(self, customer: dict, contexts: list[str]) -> tuple[str, str]:
        tier = customer["loyalty_tier"]
        system = prompts.SYSTEM_PROMPT.format(
            loyalty_tier=tier,
            retrieved_context="\n\n---\n\n".join(contexts),
            tier_guidelines=prompts.tier_guidelines(tier),
            guardrails=prompts.GUARDRAILS,
            grounding_rule=prompts.GROUNDING_RULE if self.grounding_rule else "",
        )
        user = prompts.USER_PROMPT.format(
            profile=self.profile_text(customer),
            signal_notes=prompts.interpret_signals(customer) if self.interpret else "",
        )
        return system, user

    def generate(self, customer: dict, _question: str) -> dict:
        # harness calls this with (customer_context, question) and wants
        # {answer, contexts} back. question isn't in the prompt but ragas uses for scoring
        contexts = [c["text"] for c in self.retrieve(customer)]
        system, user = self.build_messages(customer, contexts)
        # the model is given the customer profile, so ragas gets it as a context too.
        # otherwise every true fact about the customer scores as unsupported
        contexts = contexts + [self.profile_text(customer)]
        resp = self.bedrock.converse(
            modelId=GENERATION_MODEL_ID,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"temperature": 0.2, "maxTokens": 500},
        )
        answer = resp["output"]["message"]["content"][0]["text"]
        return {"answer": answer, "contexts": contexts}


# ── cli ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NorthStar offer generation RAG")
    parser.add_argument("--demo", action="store_true", help="generate one offer")
    parser.add_argument("--eval", action="store_true", help="run RAGAS on the 4 test cases")
    parser.add_argument("--query", choices=["A", "B", "C", "AB"], default="AB")
    parser.add_argument("--raw-signals", action="store_true",
                        help="starter Template 2 behavior: no signal interpretation")
    parser.add_argument("--no-grounding-rule", action="store_true",
                        help="baseline: leave out the no-invented-details rule")
    parser.add_argument("--docs-dir", type=Path, default=DEFAULT_DOCS_DIR)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--output-dir", type=Path,
                        default=REPO_ROOT / "docs" / "lab3-evidence" / "track-b")
    args = parser.parse_args()

    sys.path.insert(0, str(REPO_ROOT))
    from evaluation_harness import OFFER_TEST_CASES

    rag = OfferRAG(args.docs_dir, args.region, args.query, not args.raw_signals,
                   not args.no_grounding_rule)
    print(f"Indexed {len(rag.store.chunks)} policy chunks from {args.docs_dir}")

    if args.demo or not args.eval:
        tc = OFFER_TEST_CASES[0]
        out = rag.generate({**tc.customer_context, "customer_id": tc.customer_id}, tc.question)
        print("\n── Retrieved sections ──")
        for ctx in out["contexts"]:
            print("  " + ctx.splitlines()[0])
        print("\n── Offer ──\n" + out["answer"])
        return

    from evaluation_harness import RAGEvaluator

    # harness doesn't pass the customer id so look up from the test cases
    ids = {id(tc.customer_context): tc.customer_id for tc in OFFER_TEST_CASES}

    def invoke(customer_context, question):
        cid = ids.get(id(customer_context), "unknown")
        return rag.generate({**customer_context, "customer_id": cid}, question)

    evaluator = RAGEvaluator(rag_invoke_fn=invoke, region=args.region)
    results = evaluator.run(OFFER_TEST_CASES)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    variant = (f"query{args.query}_{'raw' if args.raw_signals else 'interp'}_"
               f"{'baseline' if args.no_grounding_rule else 'improved'}")
    csv_path = args.output_dir / f"rag_eval_{variant}_{stamp}.csv"
    results.to_csv(csv_path, index=False)

    # save actual offers too for checking the checklist 
    offers_path = args.output_dir / f"offers_{variant}_{stamp}.md"
    with open(offers_path, "w", encoding="utf-8") as f:
        for tc in OFFER_TEST_CASES:
            f.write(f"## {tc.customer_id} ({tc.customer_context['loyalty_tier']})\n\n")
            f.write(tc.generated_answer + "\n\n")
    print(f"Saved {csv_path}\nSaved {offers_path}")


if __name__ == "__main__":
    main()
