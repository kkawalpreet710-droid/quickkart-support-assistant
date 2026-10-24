# QuickKart Support Assistant (RAG)

A question-answering assistant for a fictional quick-commerce company, QuickKart. It answers customer-support questions using only 7 policy documents. It finds the relevant passages, asks a language model to answer from them, names the source files, and says "I don't know" when the documents do not cover the question.

**Live demo:** https://quickkart-support-assistant-ldgudyn3pexd69sztwwqqm.streamlit.app/

> QuickKart is not a real company. I wrote the policy documents and the test questions myself for this project. The demo runs on a free API tier, so it may be slow or temporarily unavailable.

## What it does

- Answers questions on refunds, late delivery, cancellations, item issues, payments, fees and special cases
- Cites the policy file (and usually the clause number) behind each answer
- Refuses to guess: if the answer is not in the documents, it says so
- Shows the exact passages the model was given, so an answer can be checked

## How it works

```
policy files (.txt, .pdf)
   -> split into chunks (500 characters, 100 overlap)
   -> embed and store in a Chroma vector store

question
   -> retrieve the 5 most similar chunks
   -> build a prompt (answer only from these passages, cite sources, say "I don't know" if missing)
   -> LLM answer + sources
```

| Part | What I used |
| Document loading | `pypdf` for PDFs, plain file reading for `.txt` |
| Chunking | LangChain `RecursiveCharacterTextSplitter`, 500 characters, 100 overlap |
| Embeddings and search | ChromaDB with its built-in default embedding model |
| Language model | `openai/gpt-oss-20b` on Groq, temperature 0 |
| App and hosting | Streamlit, Streamlit Community Cloud |

I first prototyped generation with a Gemini model, then moved to Groq because the free Gemini tier allowed only 20 requests per day, which was too few to evaluate 20 questions.

## Evaluation

I wrote an answer key of 20 questions before building the pipeline: 17 that the documents answer and 3 that they do not (address change after ordering, alcohol, a membership plan). The 17 include single-fact questions, questions needing two facts, "trap" questions with similar numbers (for example a Rs. 20 vs Rs. 40 cancellation fee), and one question that needs two documents.

### 1. Retrieval: does the right passage come back?

A question counts as a hit when every required key phrase from the answer key appears in the retrieved chunks. Top-1 means the phrases are all in the first chunk; top-3 means they are all within the first three chunks. I compared chunk sizes on the 17 answerable questions:

| Chunk size / overlap | Chunks | Top-1 | Top-3 | Missed at top-3 (question ids) |
| 200 / 20 | 106 | 6/17 | 10/17 | 2, 3, 6, 7, 9, 10, 13 |
| 200 / 50 | 108 | 6/17 | 11/17 | 3, 6, 7, 9, 10, 13 |
| 500 / 50 | 42 | 9/17 | 15/17 | 13, 17 |
| 500 / 100 | 43 | 8/17 | 16/17 | 13 |
| 1000 / 100 | 22 | 10/17 | 16/17 | 8 |

What I take from it:
- 200-character chunks are clearly worse. They hold only one or two clauses, so questions needing two facts, and questions where near-identical clauses compete, fail more often.
- 500 and 1000 characters are roughly tied. With 17 questions, one question is about 6 percentage points, so I do not claim either is better.
- I used 500 / 100 for the app because its chunks stay focused on one topic. The app retrieves 5 chunks instead of 3.

**Correction to this table.** The required phrase for Q17 (a late order with a missing item) only checked for the special-cases clause, so the table counted Q17 as a hit at 500 / 100. When I later checked generation, the model's answer had no amounts, and a direct check showed the Rs. 75 late-delivery clause was not among the 5 chunks it was given. The check was too lenient for that question, and the true top-3 score at 500 / 100 is probably lower than 16/17. I have not recomputed the table.

### 2. Generation: is the final answer right?

I scored the model's answers by reading them against the expected answers. Each question was run once per setting, so differences of one question are not meaningful.

| | Run 1 (original prompt) | Run 2 (two extra prompt rules) |
| Fully correct, of 17 answerable | 14 | 14 |
| Partly correct | 2 (Q9, Q17) | 2 (Q9, Q17) |
| Wrong | 1 (Q11) | 1 (Q11) |
| Correctly said "I don't know", of 3 | 3 | 3 |

Run 2 added rules telling the model to compare times and amounts with the question and to state conditions the question does not mention. Some answers became more complete (Q3 now says the wallet choice cannot be reversed, Q16 now mentions the possible surge fee). Nothing that passed in Run 1 got worse. It did not fix the failures below.

## Failures I found

**Q11: scheduled-order cancellation (wrong answer, a generation failure).** The question asks the cost of cancelling a scheduled order 30 minutes before the slot. The policy says free until 60 minutes before, and Rs. 40 within the last 60 minutes. I checked what the model was shown: the Rs. 40 clause was in its context. It still answered "free" in both full runs, citing the clause for the earlier window. In a short test after the prompt change it once answered Rs. 40, so the behaviour is not consistent. I suspect it matched the question to the wrong threshold, but I have not tested that. I did not measure how often it gets this right over repeated runs.

**Q9: rain and a late order (partly correct).** The policy withholds late-delivery credit only when the app showed a "Weather Delay Mode" banner at checkout. The question does not say whether the banner appeared. The model answered a flat "No" in Run 1, and in Run 2 it assumed the banner had appeared. The rule is right, but the answer hides a condition.

**Q17: late order plus missing item (partly correct, a retrieval failure).** The model correctly said both claims apply, but gave no amounts, because the late-delivery and refund clauses were not in the 5 chunks it saw. In a test with more chunks, the missing-item refund clause appeared at 8 chunks and the Rs. 75 clause only at 15 chunks, out of 43 chunks in total. Retrieving that many would defeat the purpose of retrieval, so I left it as a known limitation. My guess is that a question about two topics matches neither topic strongly. I have not yet tested whether asking the two parts as separate questions retrieves them.

**Q13: expired yogurt (multi-step reasoning).** The answer needs the 24-hour reporting window, but the question never says "perishable" or "how long". The link is yogurt, dairy, perishable, 24 hours. With 3 chunks, retrieval missed that clause. With 5 chunks, the model's answers included it in the runs I checked.

## Limitations

- I wrote both the documents and the questions, so they share vocabulary. Real users would phrase things differently, and real scores would probably be lower.
- The test set is small (17 answerable and 3 unanswerable questions), and each question was run once per setting.
- I tuned the chunk size and the prompt against the same questions I report results on. I have not yet scored a separate set of new questions.
- Answers were scored by me, by reading them, so borderline cases are my judgment.
- The demo depends on a free API tier. It caps each visitor at 10 new questions, and it can stop answering when the daily allowance is used up.
- The model sometimes rewrites small details, such as "Rs." as a rupee symbol.

## Run it locally

```
pip install -r requirements.txt
```

Create `.streamlit/secrets.toml` (do not commit this file) with your own Groq key:

```
GROQ_API_KEY = "your-key-here"
```

Then run:

```
streamlit run app.py
```

## Files

```
app.py              the Streamlit app (loading, chunking, retrieval, generation)
requirements.txt    dependencies
policies/           the 7 fictional policy documents (4 .txt, 3 .pdf)
eval/answer_key.csv the 20 test questions with expected answers and sources
notebooks/          the Colab experiments (chunk-size comparison, scoring)
```
