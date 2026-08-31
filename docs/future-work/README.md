# Deferred, deliberately

Four things ChaosProof does not do, written up as decisions rather than left as
a backlog. Each one names what would have to be true before it is worth
building — because "we'd like to do that eventually" is not a position anyone
can act on, and a feature list with no reasons attached reads as a list of
things that were forgotten.

| | Why it is not built | What would change that |
|---|---|---|
| [Multi-region chaos](001-multi-region-chaos.md) | One cluster, one region. Cross-region failure is a different failure model, not a bigger version of this one | A second region carrying real traffic, and an SLO defined across both |
| [Security chaos](002-security-chaos.md) | Injecting a credential expiry or a policy revocation needs a threat model this project does not have | A defined trust boundary and an owner for the blast radius of a *security* fault |
| [Game-day scoring](003-game-day-scoring.md) | Scores humans, not systems. A different instrument with a different failure mode | An organisation running regular game days that wants them measured |
| [Litmus MCP server](004-litmus-mcp-server.md) | **Worth not building.** A natural-language interface to fault injection is a safety surface, not a feature | Nothing near-term. This is a refusal, not a deferral |

The one that is different is the last one. The other three are "not yet". That
one is "no", and the reason is on the page.
