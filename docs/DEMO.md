# Demo: BharatAgent Studio in 10 minutes

Everything below uses the running app at http://localhost:3000 and the sample files in [samples/](../samples).
Each step says what you should see. The same flows run automatically with `python scripts/ui_journeys.py`,
which also saves the screenshots in [screenshots/](screenshots).

## 0. Start

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build   # without a GPU: docker compose up -d --build
```

Open http://localhost:3000/#status. All four services should say **Healthy**, and the models you set up
say **Ready** (Qwen 3 14B and Qwen 2.5 3B after `ollama pull`; Gemini and Groq with their keys in `.env`).
The Tools card lists `get_order, find_orders, order_totals` from the MCP runner.

## 1. Build an agent with a document and two tools (Builder)

1. **Builder** → **New**. Rename it "Clinic refunds".
2. Drag **PDF**, **Calculator** and **MCP tool** from the palette onto the canvas.
3. Draw a line from each new block's handle to the **Agent** (sources into its left side, tools into its
   right side), or select the block and switch on **Connected to Agent**.
   *Try it unconnected first:* **Validate** marks the block in red ("not connected to the agent").
4. Click the **Agent**: choose **Qwen 3 14B (local)**, English, and switch **Local only** on.
5. **Save agent**. The pill says "Saved · #N".
6. Click the **PDF** block and drop `samples/refund-policy.pdf` on its drop zone. Upload progress, then
   "Indexed refund-policy.pdf: 3 chunks"; the block itself now shows "3 chunks · refund-policy.pdf".

## 2. Run it and watch the canvas (Builder → Run)

Ask: **My lab test cost Rs. 1200 and I cancelled before the sample was collected. How much will I get back?**

- Input, Guardrails and Router turn **Done**; the run log says the router chose "tool" because the question
  needs a calculation and a calculator is connected.
- The Agent is **Running** while the model plans; the Calculator turns **Running**, then shows
  `1200 * 80% = 960`.
- The answer streams in: "You will get back 80 percent of the test amount, which is Rs. 960 [1][4]".
- The PDF and the Calculator turn **Cited** and their lines glow; [1] is `refund-policy.pdf`, [4] is
  `calculator (tool)`. The badges show Local only, the tokens of both model calls, "No API cost" and the time.

Switch the Agent to **GPT-OSS 120B (Groq)** (Local only off) and run again: same tools, same citations, about
2 seconds, with a cost of a few paise.

## 3. Ask about an order (Chat)

1. **Chat**, agent "Clinic refunds" (or Quick chat with **MCP tool · orders-db** switched on).
2. **Local only** on. Ask: **What is the status and refund of order ORD-1002?**
3. The answer card shows the tool call `get_order("ORD-1002")`, then "The status of order ORD-1002 is
   cancelled before sample collection [1]. The refund amount is 960 ...", citing `orders-db.get_order (tool)`.
4. Ask in Telugu or Hindi too (for example "రీఫండ్ పాలసీ ఏమిటి?"); the answer comes back in that language.

## 4. Manage documents (Knowledge)

Pick "Quick chat sources" or a saved agent. Upload another PDF, add a docs link (`https://example.com`) or a
GitHub repo (`https://github.com/octocat/Spoon-Knife`); each card shows chunks, files and when it was
indexed. **Clear content** asks before removing anything.

## 5. See the cost (Usage)

**Usage** shows requests, tokens, estimated cost and answer time. Filter to one model or one user, or
change the period. Local answers cost ₹0 (only compute time); Groq answers show their estimated cost.

## What to point out

- Unconnected sources and tools are never used, and the api refuses a graph that has one.
- The MCP tool is read-only, its server is on a network with no route to the database, and the api can only
  reach servers it knows.
- Local only: no question or document text goes to a cloud AI, not even when the local model fails.
- Every number in a cited sentence is checked against the source or tool result it cites.
