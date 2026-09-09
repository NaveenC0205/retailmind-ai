# Learn AI testing in the Test Lab

Open **Test Lab → AI testing workshop** at `/shop/lab`.

## Your first session

1. Start with **Chatbot basics** and run the exercise once.
2. Expand a response and compare the question with the answer.
3. Inspect its structural checks. A non-empty answer can still be wrong.
4. Fill in **Your quality review** and record evidence in the notes.
5. Export the report before leaving the page. Reports are not saved automatically.

## Learning path

| Exercise | What you practice | What counts as evidence |
| --- | --- | --- |
| Chatbot basics | Relevance and role boundaries | Actual answer and your review |
| RAG grounding | Checking claims against documents | Citation IDs, source text, version and effective dates |
| Agent tool use | Tool choice and constraints | Reported trajectory, returned products and tool evidence |
| Conversation memory | Context across turns | Reused conversation ID and correct references in the follow-up |
| Prompt injection | Resistance to invented instructions | Actual policy answer; distinguish quoting an attack from obeying it |
| Multi-agent comparison | Selecting an appropriate approach | Returned mode, participating agents, completeness and response time |

Use **Compare all four modes** to send the same exercise through chat, RAG,
single-agent and multi-agent modes. Use 3 or 5 repetitions to look for variation.
Each mode and repetition starts a new conversation. Follow-up turns within it
reuse the conversation ID. Stop halts remaining requests; completed results remain
available for export. The runner disables long-term preference learning.

The provider label distinguishes mock from live execution. You cannot switch the
server's provider using these controls. Live requests may incur provider charges.
Mock runs exercise application behavior; they do not measure a real model's quality.

## Read the results carefully

Structural checks cover answer presence, reported tool use, citation presence,
conversation IDs and known write tools. They do not verify factual correctness,
complete authorization, or actual database side effects. Inspect server traces and
state for deeper testing. Missing trajectory evidence fails the no-write check.

The displayed p95 is the nearest-rank 95th percentile of successful client request
durations, including network time. This is a sequential exercise, not a concurrent
load test. A few requests are not enough to establish production performance.

The policy library exposes public policy chunks, including older versions so you
can inspect effective dates. It excludes internal procedures and seller content.
Match each citation to its chunk ID and verify claims against the applicable version.

## Next steps

Use the existing Framework tester for custom prompts and authenticated workflows.
For checkout, cancellation and approval tests, use test accounts and record order
state before and after each request. Verify repeated submissions cannot perform
the same action twice. Do not infer successful authorization solely from the answer.

Use the existing dataset evaluator for single-turn regression datasets. The workshop
handles its guided multi-turn exercises separately; it does not claim to score the
legacy multi-turn dataset automatically.

Advance to labeled retrieval datasets (precision@k, recall@k and ranking quality),
held-out test cases, live-model runs, controlled failed-tool simulations, concurrent
load tests and production monitoring. Compare prompt versions against the same
cases and keep human judgments alongside automated scores.
