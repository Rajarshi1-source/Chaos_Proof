# FW-002 — Security chaos

**Status:** deferred · **Date:** 2026-08-31

## What it would be

Faults on the security plane rather than the availability plane: expiring a
certificate mid-request, revoking a service account's permissions, rotating a
secret without a reload, deleting a NetworkPolicy, or injecting an authorisation
failure between two services.

These are genuinely valuable — certificate expiry and secret rotation are among
the most common causes of real outages, and they are almost never tested.

## Why it is not built

**A security fault needs a threat model, and this project does not have one.**

The availability faults here are safe to inject because their consequences are
bounded and understood: a killed pod comes back, `tc netem` rules are removed by
the cleanup saga, a filled ephemeral volume is reclaimed on eviction. The
blast-radius arithmetic can bound them because "what does this affect" has a
concrete answer in pods and namespaces.

Security faults break that in three specific ways:

1. **The cleanup saga cannot un-revoke.** Deleting a NetworkPolicy is
   reversible; a rotated credential that some component cached is not, cleanly.
   Cleanup that runs on every exit path is a load-bearing invariant here, and a
   fault whose cleanup is best-effort does not belong in a system that promises
   it.
2. **The blast radius is not a pod count.** Revoking a service account's
   permissions affects everything holding that identity, which is a *policy*
   question, not a topology one. The existing 61–87 scale would report a
   confidently small number.
3. **A failed security fault can leave the system less safe than it started.**
   An availability fault that cleans up badly leaves a degraded system. A
   security fault that cleans up badly can leave an open one — and the failure
   is silent, because nothing is down.

There is also an ownership question. Injecting a policy revocation means someone
has decided that the resulting exposure window is acceptable, and that decision
is not the chaos framework's to make.

## What would change it

A defined trust boundary, an owner for the blast radius of a *security* fault
(distinct from the availability owner), and a cleanup path with the same
guarantee the current saga has — reversible on every exit path, with an
out-of-band sweeper.

The narrow version worth building first is certificate-expiry testing in
staging, because it is fully reversible and its blast radius is a topology
question after all.
