# QuickKart Support Assistant (RAG + agent)

A question-answering assistant for a fictional quick-commerce company, QuickKart. It answers customer-support questions using only 7 policy documents, names the source file, and says "I don't know" when the documents do not cover the question. A second version adds an agent that can also look up an order in a database and calculate credits and refunds.

**Live demo:** https://quickkart-support-assistant-ldgudyn3pexd69sztwwqqm.streamlit.app/

> QuickKart is not a real company. I wrote the policy documents, the sample orders and the test questions myself. The demo runs on a free API tier, so it may be slow or temporarily unavailable.

## Results at a glance

These are on my own test questions, with each question run once, so they show the system works on these cases, not how often it works in general.

- **Retrieval:** the needed passages were in the top 3 results for 16 of 17 answerable questions at the chosen chunk size (one of those checks was later found too lenient, see the details).
- **Policy assistant (live demo):** 14 of 17 answerable questions fully correct, 2 partly correct, 1 wrong, and all 3 unanswerable questions correctly refused.
- **Agent with orders data (in the live demo’s Agent mode):** 14 of 14 test questions reached the right outcome after I fixed two wrong citations. Every amount came from a tool, not from the model.

**Full evaluation, failure analysis and limitations: [docs/evaluation.md](docs/evaluation.md)**

## How it works

```
policy files (.txt, .pdf)
   -> split into chunks (500 characters, 100 overlap)
   -> embed and store in a Chroma vector store

question
   -> retrieve the 5 most similar chunks
   -> prompt: answer only from these passages, cite sources, say "I don't know" if missing
   -> LLM answer + sources
```

The agent version gives the model four tools (policy search, an SQL order lookup, a credit calculator and a refund calculator) and lets it choose which to call.

| Part | What I used |
| Loading and chunking | `pypdf`, LangChain `RecursiveCharacterTextSplitter` |
| Search | ChromaDB with its default embedding model |
| Language model | `openai/gpt-oss-20b` on Groq |
| Orders data (agent) | SQLite, 4 tables, 40 synthetic orders |
| App and hosting | Streamlit, Streamlit Community Cloud |

## Run it locally

```
pip install -r requirements.txt
```
Create `.streamlit/secrets.toml` (do not commit it) with your own Groq key:
```
GROQ_API_KEY = "your-key-here"
```
streamlit run app.py
```

## Files

```
app.py               the Streamlit app
requirements.txt     dependencies
policies/            the 7 fictional policy documents
eval/answer_key.csv  the 20 test questions with expected answers
notebooks/           Colab experiments (chunk sizes, scoring, the agent)
docs/evaluation.md   detailed results, failures and limitations
```

## Main limitations

My own documents and questions share vocabulary, the test sets are small, and the order data is synthetic. See [docs/evaluation.md](docs/evaluation.md) for the full list.
