# Data notices

## Bitext customer support dataset

`chatbot-variants.jsonl`, `chatbot-stream.jsonl` and `chatbot-meta.json` are derived from the
[Bitext customer support LLM chatbot training dataset](https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset)
(`Bitext_Sample_Customer_Support_Training_Dataset_27K_responses-v11.csv`) by Bitext Innovations,
licensed under the [Community Data License Agreement – Sharing, Version 1.0](https://cdla.dev/sharing-1-0/).
These files are shared under the same license. Changes: the `instruction` texts were sampled into a
request stream with a seeded Zipf popularity, and their `{{placeholders}}` were filled with sample
values (see `savings/savings/datasets.py`).

## Couchbase Capella documentation

`capella-docs.jsonl` is a text snapshot of public pages from https://docs.couchbase.com/cloud/.
`rag-questions.jsonl` holds test questions written by a model from that text. `rag-stream.jsonl` and
`rag-meta.json` are built from both.
