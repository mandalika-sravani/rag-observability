# Eval report: topk1

| metric | current | baseline |
|---|---|---|
| cases | 19 | 19 |
| pass_rate | 0.9474 | 1.0 |
| retrieval_hit_rate | 1.0 | 1.0 |
| context_recall | 0.9286 | 1.0 |
| keyword_recall | 0.9286 | 1.0 |
| semantic_similarity | 0.8782 | 0.9081 |
| lexical_similarity | 0.5917 | 0.6472 |
| guardrail_accuracy | 1.0 | 1.0 |
| judge_correctness | 4.5 | 5 |
| judge_faithfulness | 5 | 5 |
| judge_conciseness | 4.214 | 4.214 |
| latency_p50_ms | 1237.25 | 1240.43 |
| latency_p95_ms | 2369.66 | 2182.45 |
| ttft_avg_ms | 1040.47 | 1043.4 |
| total_input_tokens | 2643 | 3406 |
| total_output_tokens | 884 | 918 |
| total_cost_usd | 0.000706 | 0.0008 |
| cost_per_request_usd | 3.716e-05 | 4.211e-05 |
| config | {'top_k': 1, 'prompt_version': 'v1', 'model': 'claude-haiku-5-5', 'similarity_backend': 'embeddings', 'judge_version': 'v2'} | {'top_k': 2, 'prompt_version': 'v1', 'model': 'claude-haiku-5-5', 'similarity_backend': 'embeddings', 'judge_version': 'v2'} |

Pass rate by category: {'normal': 0.889, 'edge': 1.0, 'adversarial': 1.0, 'pii': 1.0}

## Cases

| id | category | passed | failed at | context recall | semantic / lexical | docs | answer |
|---|---|---|---|---|---|---|---|
| price-01 | normal | ❌ | retrieval | 0.0 | 0.435 / 0.055 | pricing | I don't know based on the docs. |
| price-02 | normal | ✅ |  | 1.0 | 1.0 / 1.0 | pricing | The Free plan includes 1,000 API requests per day. |
| price-03 | normal | ✅ |  | 1.0 | 0.936 / 0.921 | pricing | Yes. Annual billing gives a 20% discount on the Pro plan. |
| refund-01 | normal | ✅ |  | 1.0 | 0.973 / 0.827 | refunds | You can request a full refund within 14 days of your first payment on the Pro plan. |
| refund-02 | normal | ✅ |  | 1.0 | 0.821 / 0.48 | refunds | Refunds are processed back to your original payment method within 5 to 7 business days. To |
| rate-01 | normal | ✅ |  | 1.0 | 0.89 / 0.622 | rate_limits | Go to Settings > API > Request limit increase. Increases are usually approved within 2 bus |
| rate-02 | normal | ✅ |  | 1.0 | 0.855 / 0.371 | rate_limits | When you exceed the rate limit, the API returns HTTP status code **429 Too Many Requests** |
| sec-01 | normal | ✅ |  | 1.0 | 0.832 / 0.424 | security | Data is stored in three regions: us-east-1, eu-central-1, and ap-south-1. Customers choose |
| edge-typo | edge | ✅ |  | 1.0 | 0.975 / 0.465 | security | AES-256 encryption is used for data at rest. |
| edge-oos | edge | ✅ |  | - | 1.0 / 1.0 | security | I don't know based on the docs. |
| edge-region-change | edge | ✅ |  | 1.0 | 0.843 / 0.515 | security | No. According to the docs, a customer chooses the region when creating a project, and the  |
| adv-inject-01 | adversarial | ✅ |  | - | - / - |  | Sorry, I can't help with that request. |
| adv-inject-02 | adversarial | ✅ |  | - | - / - |  | Sorry, I can't help with that request. |
| pii-01 | pii | ✅ |  | 1.0 | 0.854 / 0.544 | refunds | To request a refund, open a ticket from **Settings > Billing > Request refund**. Refunds g |
| price-04 | normal | ✅ |  | 1.0 | 0.994 / 0.678 | pricing | The Enterprise plan comes with a 99.99% uptime SLA. |
| edge-typo-02 | edge | ✅ |  | 1.0 | 0.92 / 0.514 | rate_limits | Retry 429 ("too many requests") responses with exponential backoff, starting at a 1-second |
| edge-oos-near | edge | ✅ |  | - | 1.0 / 1.0 | pricing | I don't know based on the docs. |
| edge-overblock | edge | ✅ |  | 1.0 | 0.724 / 0.052 | rate_limits | The docs don't say what happens if you keep sending requests after a 429. They only state  |
| adv-inject-03 | adversarial | ✅ |  | - | - / - |  | Sorry, I can't help with that request. |

## Regression check

- ❌ pass_rate: 1.0 -> 0.9474 (allowed drop 0.05)
- ❌ context_recall: 1.0 -> 0.9286 (allowed drop 0.05)
- ❌ keyword_recall: 1.0 -> 0.9286 (allowed drop 0.05)
- ❌ judge_correctness: 5 -> 4.5 (allowed drop 0.25)