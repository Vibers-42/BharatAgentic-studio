# Phase 8 demo: one person adds knowledge, another asks, a third is kept out

Everything runs at http://localhost:3000 on the normal stack. Use three browser profiles (or one normal and one
private window per person), so each person has their own session. The sample file is
`samples/ecommerce-architecture.pdf` (fictional). The same flow is automated by
`scripts/verify_phase8.ps1` (API) and `scripts/ui_journeys.py --only phase8` (browser).

1. **Register the owner.** Open the landing page → *Get started* → Demo Owner, `owner@example.test`, a password
   (10+ characters, a letter and a digit). You land in your personal workspace.
2. **Create the company.** Sidebar → Workspace → *+ Create a company…* → "Bharat Technologies".
3. **Create the project.** Projects → *New project* → "E-commerce Platform".
4. **Invite two employees.** Members → *Invite a colleague* → `contributor@example.test` (member), then
   `consumer@example.test`. Copy each link (no email is sent; share it yourself).
5. **Accept the invitation.** In a second profile open the contributor's link → *Create an account* with that
   email → *Accept invitation*. It says the membership waits for confirmation. Do the same for the consumer in a
   third profile. Back as the owner: Members → *Confirm* both. Projects → E-commerce Platform → add the
   contributor as **contributor** and the consumer as **viewer**.
6. **Upload knowledge (contributor).** Workspace → Bharat Technologies, Project → E-commerce Platform →
   Knowledge: drop `ecommerce-architecture.pdf`; add the GitHub repo `https://github.com/octocat/Spoon-Knife`; add
   the docs page `https://example.com`. Each turns **ready** with its chunk count and "added by" name.
7. **Create the shared agent (owner).** Agents → *New* → add PDF, GitHub repo and Docs link blocks, pick the
   existing project sources in the inspector (nothing is uploaded again), draw them to the Agent, choose
   Qwen 3 14B → *Save agent*.
8. **Log in as the consumer** (third profile). Knowledge shows the three sources; there is nothing to upload.
9. **Ask.** Chat → *Agent: …* → "Above what order value is delivery free?" → Rs. 499, cited [1] from the PDF.
   "What is the Spoon-Knife repository meant for?" → forking practice, cited README.md.
10. **Check citations.** Click a citation number to open the quoted chunk; the docs page links to its URL.
11. **Continue the conversation.** "Which service talks to the payment gateway?" then "What happens to new orders
    if it is down?" → 30 minutes pending payment (the follow-up understood "it"). Reload the page: the
    conversation is in the list; it stays after sign-out, sign-in and restarts. The contributor cannot see it.
12. **Telugu or Hindi.** "కస్టమర్లు ఎన్ని రోజుల్లోగా వస్తువులను తిరిగి ఇవ్వవచ్చు?" → 10 days, answered in Telugu.
13. **Local-only privacy.** As owner: Projects → New project "Confidential" with *Local only* on. In its chat the
    model list offers only local models and the switch reads "Local-only project"; a direct API request with
    `"model": "groq"` gets 400.
14. **Unauthorized access is rejected.** Register a fourth account (`outsider@example.test`) that is not invited.
    Its workspace shows none of Bharat Technologies; opening `/api/projects/<id>`, `/api/graphs/<id>`,
    `/api/sources/<id>/file` or `/api/conversations/<id>` of the company returns 404. Remove the consumer from
    the project: their open tab loses access on the next action, without being signed out.

Legacy data from before Phase 8 stays hidden until an operator runs
`docker compose exec api python -m app.admin claim-legacy owner@example.test`.
