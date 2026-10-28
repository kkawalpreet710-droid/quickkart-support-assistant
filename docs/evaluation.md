# Evaluation, failures and limitations

This is the detailed write-up behind the results in the main README. Everything here was measured on questions I wrote myself, with one run per question unless stated.

## Test set

Before building the pipeline I wrote an answer key of 20 questions: 17 that the documents answer and 3 that they do not (change of address after ordering, alcohol, a membership plan). The 17 include single-fact questions, questions needing two facts, "trap" questions with similar numbers (for example a Rs. 20 vs Rs. 40 cancellation fee), and one question that needs two documents. The key is in `eval/answer_key.csv`.

## Part 1: the policy assistant (Stage 1)

### Retrieval: does the right passage come back?

A question counts as a hit when every required key phrase from the answer key appears in the retrieved chunks. Top-1 means all phrases are in the first chunk; top-3 means they are within the first three chunks.

| Chunk size / overlap | Chunks | Top-1 | Top-3 | Missed at top-3 (question ids) |
|---|---|---|---|---|
| 200 / 20 | 106 | 6/17 | 10/17 | 2, 3, 6, 7, 9, 10, 13 |
| 200 / 50 | 108 | 6/17 | 11/17 | 3, 6, 7, 9, 10, 13 |
| 500 / 50 | 42 | 9/17 | 15/17 | 13, 17 |
| 500 / 100 | 43 | 8/17 | 16/17 | 13 |
| 1000 / 100 | 22 | 10/17 | 16/17 | 8 |

- 200-character chunks are clearly worse. They hold only one or two clauses, so questions needing two facts, and questions where near-identical clauses compete, fail more often.
- 500 and 1000 characters are roughly tied. With 17 questions one question is about 6 percentage points, so I do not claim either is better.
- I used 500 / 100 for the app because its chunks stay focused on one topic, and the app retrieves 5 chunks instead of 3.

**Correction.** The required phrase for Q17 (a late order with a missing item) only checked for the special-cases clause, so the table counted Q17 as a hit at 500 / 100. A later check showed the Rs. 75 late-delivery clause was not among the 5 chunks the model received. The check was too lenient for that question, so the true top-3 score at 500 / 100 is probably lower than 16/17. I have not recomputed the table.

### Generation: is the final answer right?

I scored the answers by reading them against the expected answers.

| | Run 1 (original prompt) | Run 2 (two extra prompt rules) |
|---|---|---|
| Fully correct, of 17 answerable | 14 | 14 |
| Partly correct | 2 (Q9, Q17) | 2 (Q9, Q17) |
| Wrong | 1 (Q11) | 1 (Q11) |
| Correctly said "I don't know", of 3 | 3 | 3 |

Run 2 added rules telling the model to compare times and amounts with the question and to state conditions the question does not mention. Some answers became more complete (Q3 now says the wallet choice cannot be reversed, Q16 now mentions the possible surge fee) and nothing that passed got worse. The failures below remained.

### Failures

**Q11: scheduled-order cancellation (wrong answer, a generation failure).** The question asks the cost of cancelling a scheduled order 30 minutes before the slot. The policy says free until 60 minutes before, and Rs. 40 within the last 60 minutes. I checked what the model was shown: the Rs. 40 clause was in its context. It still answered "free" in both full runs, citing the earlier-window clause. In one short test after the prompt change it answered Rs. 40, so the behaviour is not consistent. I suspect it matched the wrong threshold, but I have not tested that.

**Q9: rain and a late order (partly correct).** The policy withholds late-delivery credit only when the app showed a "Weather Delay Mode" banner at checkout. The question does not say whether the banner appeared. The model answered a flat "No" in Run 1, and in Run 2 it assumed the banner had appeared. The rule is right, but the answer hides a condition.

**Q17: late order plus missing item (partly correct, a retrieval failure).** The model correctly said both claims apply but gave no amounts, because the late-delivery and refund clauses were not in the 5 chunks it saw. In a test with more chunks, the missing-item refund clause appeared at 8 chunks and the Rs. 75 clause only at 15, out of 43 chunks in total. Retrieving that many would defeat the purpose of retrieval, so I left it. My guess is that a question about two topics matches neither strongly. I have not tested whether splitting it into two questions retrieves both.

**Q13: expired yogurt (multi-step reasoning).** The answer needs the 24-hour reporting window, but the question never says "perishable" or "how long". The link is yogurt, dairy, perishable, 24 hours. With 3 chunks retrieval missed that clause. With 5 chunks, the answers I checked included it.

## Part 2: the agent with orders data (Stage 2)

### Design

The agent has four tools. The model chooses which to call, my code runs them and sends the results back, and this repeats until the model writes a final answer. I wrote the loop in plain Python, without an agent framework, so I could see every step.

| Tool | What it does |
|---|---|
| `search_policy` | The retrieval from Part 1 |
| `lookup_order` | An SQL lookup in a SQLite database: payment method, delay in minutes, Weather Delay Mode, and each item with a delivered flag |
| `late_delivery_credit` | A calculator for the Late Delivery Policy tiers (15-minute grace period, then Rs. 30, then Rs. 75) |
| `missing_item_refund` | A calculator for the refund amount, the refund timeline by payment method, and whether fees are also refunded |

The database has four tables (`customers`, `orders`, `order_items`, `deliveries`) linked by foreign keys, with 40 synthetic orders from a fixed random seed. Lateness is computed by SQL from the estimated and delivered timestamps. Order 1001 is a known test case (35 minutes late, one item missing). The data is not fully consistent with the policy (for example, some orders are below the Rs. 99 minimum order value).

Design decisions:
- **Calculators instead of model arithmetic.** In Part 1 the model misapplied a time threshold (Q11). The credit function is plain Python with tests on the exact boundaries (15, 30 and 60 minutes).
- **Parameterised SQL.** Order ids go in as `?` placeholders, never pasted into the query text.
- **Limits enforced in code, not in the prompt.** Maximum 6 steps, a maximum of 2 policy searches (after that the tool returns an error), tools switched off on the last step so the model must answer, one retry when the model returns an empty final message, and a fixed reply when an order id does not exist.

### Agent test

I ran 14 questions: 4 late-credit cases chosen from the database by SQL (the Rs. 30 band, the Rs. 75 band, a Weather Delay Mode order, an on-time order), 2 orders with only some items missing, 1 order that is late and has a missing item, 1 order id that does not exist, 4 policy questions, and 2 unanswerable questions. Expected amounts come from the tools and the policy. An automatic check looks for the right amounts or words, and I read the other answers myself.

| Result, first run | Count |
|---|---|
| Fully correct | 12 |
| Right outcome but cited the wrong clause | 2 |
| Wrong outcome | 0 |

Both unanswerable questions were refused correctly. The two wrong citations were fixed (see below) and re-run: they now cite clause 3.1 and clause 2.1. Each question was run once.

### What I found

- **The agent handles the question Part 1 could not.** For "late and missing item" the amounts (Rs. 75 and Rs. 25) come from tools. The two parts were tested on different questions, so I do not claim a score comparison.
- **A rule in the prompt was ignored; a rule in code worked.** I told the model to search at most twice. On an unanswerable question it searched six times and ran out of steps. The code limit fixed it.
- **The model repeats what the tools tell it.** My credit tool always returned a note citing clause 2.5, so for orders with no credit the model cited the wrong clause. I made the note depend on the result. The automatic check could not catch this; I found it by reading.
- **Answers vary between runs.** For "How long do I have to report a spoiled item?", the answer included both the 24-hour and 48-hour windows in 0 of 5 runs with my first prompt and 4 of 5 with the stricter prompt. The scheduled-order cancellation question (Rs. 40) was correct in the one agent run, but I have not measured it over repeated runs.

## Limitations

- I wrote the documents, the questions and the order data, so they share vocabulary and real users would probably score lower.
- The test sets are small and each question was run once (except the spoiled-item check).
- I tuned the chunk size and prompts against the same questions I report on, and I have not yet scored a separate set of new questions.
- I scored answers by reading them, so borderline cases are my judgment. The automatic check for the agent cannot verify which clause an answer cites.
- The agent uses several API requests per question, because the conversation is resent at every step, so it uses the free allowance faster. It is not yet in the live demo.
- The model sometimes rewrites small details, such as "Rs." as a rupee symbol.

## What I would do next

- Score a fresh set of questions written in different wording, including conditional ones
- Run the Q11 question and other conditional questions many times to measure consistency
- Test splitting a multi-topic question into sub-questions before retrieval
- Try a larger model on the questions where answers varied
