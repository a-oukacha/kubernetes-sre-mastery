# Lecture 03 - Health probes & graceful lifecycle: independence, draining, and the SIGTERM race

## Answers to the lab checkpoints
- **(0)** Only the **startupProbe**. While `startupProbe` is still failing, the kubelet disables both the liveness and readiness probes entirely - they don't run at all until startup succeeds once. That's the whole point of the probe: give a slow booter a long grace window without lengthening anything else.
- **(1)** `0/1` with `RESTARTS=0` is exactly right: not ready (startup hasn't succeeded, so readiness reports nothing and the pod is held out of the Service) but not killed (liveness is suppressed). `READY` flips to `1/1` the first time `cat /tmp/started` succeeds - at which point liveness and readiness switch on.
- **(2) The `startupProbe` block shows `failureThreshold: 30`, `periodSeconds: 2` -> a 60-second** boot budget, and the events show `Started` with no `Unhealthy`/`Killing` during boot. The app booted in ~20 s, comfortably inside the budget.
- **(3)** Steady `Code 200` once the pods are ready. fortio is your live "are we serving?" meter - you'll watch this count *not* move into errors during the readiness flip and the rollout.
- **(4) `READY` goes `0/1`; `STATUS` stays Running**; `RESTARTS` stays **0**; and the pod is removed from the Service's EndpointSlice (`ready=false`). Readiness failing does exactly one thing: stop sending it traffic. It does not restart, kill, or reschedule the pod.
- **(5)** Confirmed: the targeted pod shows `ready=false` (the other two stay `ready=true`), `STATUS=Running`, `RESTARTS=0`. fortio's error count stays flat because kube-proxy now only load-balances to the two ready endpoints.
- **(6)** `touch /tmp/ready` -> next probe succeeds (`successThreshold: 1`) -> the pod flips back to `ready=true` and rejoins the EndpointSlice. No restart, no respec - readiness is pure, reversible set-membership.
- **(7)** It keeps its **name and `RESTARTS` increments - the container is restarted in place** by the kubelet on the same node, not rescheduled. (Rescheduling to a new pod is what a controller does when the *pod* is deleted/evicted; liveness operates on the *container* inside the existing pod.) It comes back ready on its own because the wrapper command re-creates all three sentinels on restart.
- **(8) `restartCount` is now `1`, the pod name is unchanged, and events show `Unhealthy` (liveness) -> `Killing` -> `Started`. The container re-ran its 20-s boot and returned to `1/1`. Mental note: if that liveness check had been an HTTP call to your database**, a DB blip would have just restarted this container - multiply across the fleet and that's a self-inflicted outage.
- **(9)** You catch a pod in `STATUS=Terminating`. With `maxUnavailable: 0` and `maxSurge: 1`, the Deployment brings up a new ready pod *before* removing an old one, and the old one lingers for its `preStop` sleep + grace while it drains.
- **(10) fortio's `Code 200` count climbed straight through the rollout with zero** non-200s or connection errors. Zero-downtime requires *both* halves: readiness gates new pods in (they don't receive traffic until ready), and `preStop`+grace drains old pods out (they keep serving in-flight requests until the endpoint removal has propagated).
- **(B1)** `CrashLoopBackOff`, with `RESTARTS` climbing then slowing. The app needs 20 s to listen; the aggressive liveness kills it at ~6 s, every time, before it can ever serve. The kubelet inserts an exponential back-off (10s, 20s, 40s... capped at 5m) between restarts - that back-off *is* the "CrashLoopBackOff" status.
- **(B2) Repeating `Unhealthy` -> `Killing` -> `Started` in the events and a climbing `restartCount`. Logs are nearly useless here (the process is killed mid-boot every time) - events tell the story**, same lesson as lab 01.
- **(B3) Adding a `startupProbe` (60-s budget) suppresses liveness during boot; the pod boots fully, goes `1/1`, and the thrash stops. The fix for "my slow app gets killed on startup" is never** a longer liveness `initialDelaySeconds` - it's a startupProbe.

---

## What just happened (under the hood)
Three probes, three independent jobs. The kubelet runs all probes locally on the node (it does *not* go through the Service or kube-proxy). Each does exactly one thing on failure, and they do not coordinate:

| Probe | Question it asks | Action on failure | Side effects |
|-------|------------------|-------------------|--------------|
| **liveness | "Is this process wedged / unrecoverable?" | restart the container** in place (`RESTARTS++`) | none on Service membership |
| **readiness** | "Can I serve a request *right now*?" | remove the pod from Service endpoints | no restart, pod stays Running |
| **startup | "Has the app finished booting yet?" | restart (after its budget) and** keep liveness+readiness disabled until it first succeeds | gates the other two |

Readiness writes into the pod's `Ready` condition. The **EndpointSlice controller watches pod readiness and adds/removes the pod's IP from the Service's EndpointSlice; kube-proxy** programs that set into iptables/IPVS. So "readiness false" -> "dropped from EndpointSlice" -> "kube-proxy stops DNAT'ing to it" is the chain you watched in step 3. Liveness never touches that chain - which is why a liveness-failed-and-restarting pod can still be a Service member if its readiness happens to pass.

**The SIGTERM contract and the race. When you delete a pod (or a rollout replaces it), two things start at the same instant**:
1. The pod is marked `Terminating`, and the EndpointSlice controller begins **removing it from endpoints**.
2. The kubelet runs the **`preStop`** hook, then sends **SIGTERM to PID 1, then waits up to `terminationGracePeriodSeconds`, then SIGKILL**.

The race: endpoint removal is *eventually consistent* - it has to propagate to the EndpointSlice, then to every node's kube-proxy, then into iptables. For a few hundred milliseconds, clients can still be routed to a pod that has already received SIGTERM. If your process exits immediately on SIGTERM, those in-flight connections get a `connection reset` -> 5xx. The **`preStop` sleep** (here, 10 s) keeps the old pod alive and serving *through* that propagation window, so it drains cleanly. The grace period must be **longer** than `preStop` + your longest in-flight request, or SIGKILL cuts a live request. That is the entire mechanism behind a zero-downtime rollout - lab 02's `maxUnavailable: 0` brings the *new* capacity up; this lab's `preStop`+grace takes the *old* capacity down without dropping anything.

**Drain order, concretely:** delete -> (preStop runs *and* endpoint removal begins, concurrently) -> SIGTERM -> app stops accepting new conns but finishes in-flight ones -> process exits 0 (or grace expires -> SIGKILL). Get the ordering wrong - no preStop, or grace shorter than drain - and you drop requests on every single rollout.

## Dev notes
- **Write honest endpoints.** Liveness must answer "am *I* broken beyond self-recovery?" - process deadlock, exhausted thread pool, corrupt in-memory state. It must **not** check your database, cache, or any downstream. Readiness answers "can I serve *right now*?" - and *that* one may legitimately go red when a critical dependency is gone (so you stop taking traffic) without the kubelet killing you.
- Liveness and readiness need different endpoints. Pointing both at the same naive `GET /` defeats the purpose: either you get pointless restarts on transient blips, or your readiness never meaningfully changes. Give them separate handlers with different semantics.
- **Make startup cheap and explicit.** If your app has a real boot cost (cache warm, JIT, weight load), expose a startup signal and use a `startupProbe`, not a fat liveness `initialDelaySeconds` - the startupProbe budget applies *only* at boot, then gets out of the way and lets a tight liveness period catch real wedges fast.
- **Handle SIGTERM.** Stop accepting new work, drain in-flight, exit 0 *within* the grace period. If your runtime ignores SIGTERM (some shells, some PID-1 setups), wire it explicitly or the grace period buys you nothing and every pod gets SIGKILLed.

## DevOps / Platform notes
- **The defaults will thrash you.** A liveness probe with `periodSeconds: 10`, `failureThreshold: 3`, `timeoutSeconds: 1` kills a container after ~30 s of a slow-but-alive response - easy to hit under GC pause or a noisy node. Tune `failureThreshold` and `periodSeconds` to your real p99 health-check latency; budget = `failureThreshold × periodSeconds`.
- **Probe types: `exec` (what we used - runs a command in the container; costs a process fork each period, so keep it light), `httpGet` (cheapest and most common for HTTP apps), `tcpSocket` (just checks the port opens - weak signal), and `grpc`** (`grpc:` probe, GA since 1.27; uses the standard gRPC Health Checking Protocol - prefer it over shelling out to `grpc_health_probe`).
- `preStop` and grace are platform policy, not app trivia. Standardize a sane `terminationGracePeriodSeconds` and a `preStop` sleep in your base template / admission policy so teams get drain-safe shutdowns by default.
- Watch the signals you'd actually alert on: `kube_pod_container_status_restarts_total` (liveness thrash), endpoint count vs replica count (readiness gaps), and pods stuck `Terminating` (grace/finalizer problems).

## Architect notes (trade-offs)
- **The cascading-restart anti-pattern.** A liveness probe that deep-checks a shared dependency (DB, auth service, another microservice) turns *that* dependency's hiccup into a fleet-wide restart storm: the dep blips -> every replica's liveness fails -> the kubelet kills them all -> they restart into the same down dependency -> CrashLoopBackOff across the service -> now you've amplified a partial outage into a total one, and added a thundering-herd reconnect when the dep recovers. **Liveness ≠ dependency health.** Dependency health belongs in *readiness* (stop taking traffic) at most, and usually in metrics/alerts, not probes.
- **Restart is a blunt instrument.** Liveness assumes "restart fixes it." That's true for memory leaks and deadlocks; it's false for bad config, a poisoned message, or a downstream outage - there, restarting just loops. Reserve liveness for genuinely unrecoverable in-process states.
- **Where draining lives.** `preStop`+grace handles *connection* draining at the pod edge. Long-lived streams, in-flight transactions, and "finish the current unit of work" semantics are an application concern the grace period must be sized around - the platform gives you the window; the app has to use it.

## SRE notes (failure modes, SLOs, toil)
- Graceful shutdown is an availability lever. Every deploy, every node drain, every scale-down terminates pods. If termination drops requests, your error budget bleeds on *routine* operations - and rollouts become scary instead of boring. `preStop`+grace (sized > drain time) converts "deploys cause a blip" into "deploys are invisible," which is the precondition for deploying frequently.
- Readiness gating is how lab 02's zero-downtime rollout actually works. `maxUnavailable: 0` is only safe because new pods don't receive traffic until readiness passes and old pods drain before exit. Remove the readiness probe and your rollout "succeeds" straight into a brownout (you serve requests from pods that aren't actually ready).
- **Probe tuning is toil reduction.** Mis-tuned probes generate phantom restarts and 3am pages for healthy apps. The fix is data: set `failureThreshold × periodSeconds` above your worst legitimate health-check latency, and use a `startupProbe` for boot cost so you can keep a *tight* liveness for real wedges.
- The race is the classic "0.1% of requests fail during deploys" ticket. Symptom: a small, deploy-correlated spike of 5xx/connection-resets. Cause: no `preStop`, or grace shorter than drain, so endpoints-removal loses the race to SIGTERM. Fix: add a `preStop` sleep ≥ endpoint-propagation time and ensure grace > preStop + longest in-flight request.
- **Know the statuses cold:** `CrashLoopBackOff` (process exits or is liveness-killed repeatedly - read events, then logs), endpoints `< replicas` (readiness gap - check the readiness probe + selector), pod stuck `Terminating` (grace too long, a wedged preStop, or a finalizer).

## AI/ML notes (LLM/ML serving mapping - conceptual)
- Long model load -> `startupProbe`, never a long liveness delay. A multi-GB weight load (and CUDA graph capture / KV-cache allocation) can take minutes. Give the server a generous `startupProbe` budget (`failureThreshold × periodSeconds` covering worst-case load) so the orchestrator *waits* for the cold replica instead of killing it mid-load and thrashing - exactly the Break-it failure, scaled to expensive GPU nodes.
- Readiness false while loading weights. The router/Service must not send a prompt to a replica that hasn't finished loading - a cold replica returns errors or stalls. Keep readiness red until weights are resident and the model can produce a token, so traffic only lands on warm replicas (this is the inference-serving version of step 3).
- Drain in-flight generations before exit. Streaming/token-by-token responses are long-lived connections; a chat completion can run for tens of seconds. Your `terminationGracePeriodSeconds` must exceed the **longest in-flight generation** plus `preStop`, or a rollout/scale-down SIGKILLs a request mid-stream and the user gets a truncated answer. Drain = "stop admitting new prompts, finish the ones in flight, then exit."
- **Liveness stays shallow.** A liveness check that exercises the GPU or runs a real inference is expensive and flaky under load; keep it a cheap "is the server loop alive?" check. Deep "is the model actually producing sane tokens?" belongs in readiness or quality SLIs (lab 20), not liveness.

## Pitfalls
- **Liveness that calls the database** (or any downstream) -> one dependency blip restarts your whole fleet (cascading-restart). Dependencies go in readiness at most.
- Grace period shorter than drain time -> SIGKILL cuts in-flight requests on every rollout; the canonical "deploys cause a tiny 5xx spike" bug.
- **No `startupProbe` on a slow boot** -> aggressive liveness CrashLoopBackOffs the app before it can start (Break-it).
- **No `preStop`, fast SIGTERM exit** -> you lose the endpoint-removal race and reset in-flight connections.
- Readiness and liveness pointing at the same naive endpoint -> either pointless restarts on transient errors, or a readiness that never meaningfully changes.
- Fixing slow start with a huge liveness `initialDelaySeconds` -> liveness is now slow to catch *real* wedges forever, not just at boot. Use a startupProbe.

## Further reading
- **KP "Health Probe"** - liveness vs readiness vs startup as a foundational pattern; process/port/HTTP/exec/gRPC probe variants.
- **KP "Managed Lifecycle"** - SIGTERM/SIGKILL contract, `preStop`, graceful shutdown, lifecycle hooks.
- **KIA ch4 (liveness probes & restart behavior), KIA ch5 (readiness probes & how they gate Service endpoints), KIA ch17** (lifecycle hooks, graceful shutdown, `terminationGracePeriodSeconds`).
- **DDS** - health checks in the foundational concepts (why readiness ≠ liveness for reliable serving).
