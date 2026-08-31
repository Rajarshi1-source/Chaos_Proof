# FW-004 — Litmus MCP server

**Status:** **declined**, not deferred · **Date:** 2026-08-31

## What it would be

A Model Context Protocol server exposing fault injection to an LLM agent, so
that "kill a payment-service pod and tell me what happened" becomes a tool call.
LitmusChaos ships one, so this is a real, available integration rather than a
hypothetical.

It would also demo extremely well, which is most of why it is worth writing down
the reason for saying no.

## Why this is a refusal rather than a backlog item

**A natural-language interface to fault injection is a safety surface, not a
feature.**

ChaosProof's entire safety argument is a chain of gates that run in a fixed
order before anything is injected: steady-state check, SLI floor, blast-radius
computation, error-budget gate, CEL policy evaluation. The chain is
load-bearing, and its value comes from being *unbypassable* — pre-flight returns
`proceed`, `skip` or `deny`, and injection happens only on the first.

An agent-facing tool call is a second entrance to the same building. Every one
of these has to be answered before it is safe, and none of them has a good
answer today:

1. **What blast radius did the caller consent to?** The whole point of computing
   radius pre-flight is that a human sees the number. An agent that reads the
   number and proceeds has consented on the human's behalf.
2. **What does prompt injection do here?** Every other input to this system is
   structured: a YAML spec, a CEL policy, a commit range. An MCP tool takes
   natural language, and text reaching an agent from a Prometheus label, an
   alert annotation or a GitHub comment is text the operator did not write. The
   consequence of a successful injection is not a wrong answer — it is a fault
   injected into a running system.
3. **What is the audit trail?** `preflight_decisions` records who asked, what
   was computed, and what was decided. "The agent decided to" is not an entry
   anyone can act on afterwards.
4. **How does the override flag survive contact with an agent?** `--override`
   satisfies a `require_override` policy rule, and it exists so that bypassing a
   guardrail is a deliberate human act that gets recorded. An agent with access
   to that flag has a guardrail-shaped suggestion rather than a guardrail.

Note the consistency with the rest of the system rather than treating this as a
special case. **The LLM already has a defined role here and it is deliberately
small**: `TemplateNarrator` is the default so the system works with no API key
and no network; `LLMNarrator` drafts prose from a bundle and its output is
discarded if it references any number not in that bundle, with discards counted
in `chaosproof_narrator_discards_total`. **The LLM never touches a verdict.**
Letting it touch an *injection* would be a much larger step than letting it
touch a verdict, taken in the opposite direction from every other decision here.

There is also a symmetry worth naming: the framework API has no trigger endpoint
at all — no HTTP route reachable from a browser can inject a fault — and the
public dashboard build has the trigger controls removed at build time rather
than disabled. Adding an agent-callable injection tool would reopen, for a
non-human caller, the exact door that was deliberately closed for human ones.

## What is not being claimed

The Litmus MCP server is not a bad piece of software, and read-only MCP access —
"what did the last pod-kill experiment conclude", "show me the flakiness table"
— raises none of the above. If this is ever built, **it is read-only, and the
injection tools are not exposed.** That is a narrower feature than the one
described at the top of this page, and it is the only version of it that fits.

## What would change this

Nothing near-term. This is on the deliberate-omissions list in the README rather
than on a roadmap.
