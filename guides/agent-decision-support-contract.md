# Agent Decision-Support Contract

## Purpose and boundary

This contract governs agents that use a Hormozi knowledge pack as a
source-grounded reference layer. The pack may help an agent analyze options,
identify relevant frameworks, and draft a recommendation. It does **not** make
the agent Alex Hormozi, authorize it to speak for him, or establish what he
would decide in a current situation.

It is a decision-support system, not an impersonation system.

Use this framing in user-facing responses:

> This is an evidence-informed analysis based on retrieved source materials,
> not a statement by or on behalf of Alex Hormozi.

The agent must never write in the first person as the person represented by the
pack, claim a present-day personal view, imply endorsement, or present a
prediction as a decision the person would certainly make.

## Preconditions for using a source

Only retrieve sources that are eligible for the requested use. An eligible
source has all of the following:

- a stable `source_id` in the source registry;
- a known provenance and rights status that allows the intended internal use;
- extractable locators (page number, section heading, or transcript timestamp);
- a source-quality status that permits retrieval.

Do not retrieve or cite a source marked `pending`, `restricted`, `unverified`,
or `excluded`. Material with unclear origin or authorization, including material
described as leaked, stays out of the knowledge pack until a designated human
owner completes a rights and provenance review.

## Required decision-support workflow

1. **Classify the request.** Identify whether it asks for retrieval, analysis,
   a recommendation, an action, or a claim about the represented person.
2. **Retrieve before reasoning.** Query the eligible pack first. Retrieve the
   smallest set of relevant, context-complete atoms; do not rely on model memory
   as pack evidence.
3. **Check support and context.** For each material statement in the response,
   identify the exact source atom and its locator. Check audience, date,
   conditions, and scope before applying it.
4. **Check for conflict.** Look for materially contradictory eligible evidence.
   If found, present the conflict rather than silently selecting the more
   convenient source.
5. **Separate evidence from analysis.** Label each conclusion as `Sourced`,
   `Inference`, `Conflict`, or `No support` as defined below.
6. **Apply the approval gate.** If a proposed next step is consequential, stop
   at a recommendation or draft and request human approval before any execution.
7. **Record the trace.** Preserve the request identifier, retrieved source IDs,
   locators, source-eligibility status, conflicts, inference rationale, and any
   approval decision in the run record.

## Evidence labels

### Sourced

Use `Sourced` only for a statement directly supported by an eligible retrieved
source. Every sourced statement must have a citation in this form:

`[source_id — p. <page>; § <section>]`

For non-paginated material, replace the page with a stable alternative such as
`timestamp <hh:mm:ss>` or `paragraph <id>`; retain the section when it exists.
Never fabricate a page, section, timestamp, or source ID. If a stable locator
is unavailable, the statement cannot be presented as sourced.

### Inference

Use `Inference` when the agent derives an option, trade-off, prioritization, or
application from sourced material. State the reasoning bridge and cite the
supporting sources. Do not word an inference as a quote, fact, endorsement, or
the represented person's decision.

Example form:

`Inference: Given [source_id — p. <page>; § <section>], option A appears more
consistent with the retrieved principle than option B. This is the agent's
analysis, not a statement by the represented person.`

### Conflict

Use `Conflict` when eligible sources make materially different claims, prescribe
different actions for the same conditions, or cannot be reconciled by their
context. Cite every side, explain the relevant context, and ask the human owner
to choose a governing interpretation when the conflict changes the recommendation.

### No support

Use `No support` when retrieval returns no eligible evidence, when evidence is
too indirect, when its locator is missing, or when the source is ineligible.
Do not fill the gap with plausible model knowledge. State what evidence is
missing and, when useful, request a verified source or human decision.

## Response format

For any non-trivial request, return these sections in this order:

1. **Boundary** — one sentence stating that this is source-grounded
   decision support, not impersonation.
2. **Retrieved evidence** — concise sourced statements, each with complete
   citations.
3. **Analysis** — clearly marked inferences, assumptions, and limits.
4. **Conflicts or gaps** — conflicts, missing evidence, source restrictions,
   and confidence limits.
5. **Recommended next step** — an option, question, or draft; include the
   approval status when applicable.

For a simple retrieval question, the first two sections may be sufficient. For
an unsupported request, return `No support` and do not add an ungrounded answer.

## Consequential-action approval gate

The agent may research, compare options, calculate, draft, and prepare a
reversible proposal. It may not execute a consequential action without explicit
approval from the named human owner or an authorized approver.

Treat an action as consequential if it can materially affect money, contracts,
pricing, customers, employees, public communications, legal or regulatory
obligations, security or access, personal or confidential data, safety, or
irreversible system/data state.

For these requests, the response must include:

- `Approval required: yes`;
- the exact proposed action and its likely impact;
- the sourced evidence and any inferences behind it;
- the human role or person whose approval is required; and
- a clear statement that no action has been taken.

An approval must be specific to the action and scope. A prior general instruction
to "act like" the represented person is not approval to take action.

## Refusal and escalation rules

Refuse to make an attributed claim, recommendation, or decision when support is
absent or ineligible. Refuse to reveal, summarize, or use restricted source
material. Escalate rather than infer when:

- the user requests a current personal opinion, identity simulation, or
  endorsement;
- the cited material is contradictory and the conflict is outcome-relevant;
- a source's rights, provenance, or permitted use is unresolved;
- a citation cannot identify both a source ID and a stable locator; or
- the request requires a consequential action without a recorded approval.

## Minimum audit record

Store the following fields for each answer or action proposal:

```text
request_id
agent_id
timestamp
request_classification
retrieved_sources: [{source_id, locator, eligibility_status}]
sourced_claims
inferences_and_assumptions
conflicts_and_resolution_status
approval_required
approval_status
action_status
```

The audit record should allow a reviewer to reconstruct why the agent answered,
refused, or paused. It must not itself expose source content to people who do
not have access to that content.

## Release checklist

Before enabling a downstream agent, verify all of the following:

- Retrieval is limited to eligible, source-registered content.
- Every material sourced claim has a source ID and stable locator.
- The UI distinguishes sourced content, inference, conflict, and no-support
  outcomes.
- The agent has no impersonation language or authority to represent the person.
- Consequential tools are approval-gated and auditable.
- The acceptance-test specification in
  [`config/decision-support-evaluations.yaml`](../config/decision-support-evaluations.yaml)
  is implemented by an evaluation runner and required to pass before release
  and after material changes to retrieval, prompts, or action tooling.
