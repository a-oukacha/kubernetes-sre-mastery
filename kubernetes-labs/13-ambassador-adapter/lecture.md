# Lecture 13 - Ambassador & Adapter: localhost contracts and uniform interfaces

## Answers to the lab checkpoints
- **(0) The `app` container connects to `127.0.0.1:6379` (`localhost`). It mentions no** Service name - not `backend-a`, not `backend-b`, nothing. The only network address in the app spec is `localhost`. That is the entire ambassador contract: the app addresses a fixed local port and is blind to where the real backend lives.
- **(1)** `backend-a` DBSIZE grows while `backend-b` stays `0`. The app wrote to `localhost:6379`; the ambassador (haproxy, `mode tcp`) shuttled those RESP bytes to the `backend-a:6379` Service named on its single `server target` line.
- **(2)** Grepping the app args returns only `127.0.0.1`/`localhost`. There is no backend address in the app container at all - discovery and routing were pushed entirely into the sidecar.
- **(3)** **backend-B** now climbs. You changed **zero** bytes of the app container: you applied a new ConfigMap (same name `ambassador-config`, one line different) and restarted the pod so haproxy re-read its file. The app manifest you re-applied is identical to step 1.
- **(4)** New writes land in `backend-b`; `backend-a` is frozen at its old DBSIZE. The app log still reads "via localhost:6379" - from the app's perspective nothing happened. Topology moved; the contract didn't.
- **(5)** The native interface is RESP `INFO` (key/value text over the redis protocol - Prometheus can't scrape that). The adapter (`redis_exporter`) connects to `localhost:6379`, runs `INFO`, and re-emits it as Prometheus exposition text: `# HELP`, `# TYPE`, then `redis_up`, `redis_connected_clients`, etc. The redis container itself was never instrumented or modified.
- **(6)** One generic scrape rule - "scrape the `metrics` port of services with this label" - covers this redis. It would cover a Go or Java app the same way, because the adapter made every app's output the *same shape*. Standardizing the interface is what lets a single scrape config serve a whole fleet.
- **(B1) No - the writes fail/hang even though `backend-b` answers `PONG` on its own. The failure is in the ambassador hop**: haproxy can't reach `backend-does-not-exist`, so `localhost:6379` has no working path even though the real backend is healthy.
- **(B2)** The app's `redis-cli` to `localhost:6379` errors or times out. The backend never saw the request; it died inside the sidecar. This is the cost of the pattern: the ambassador is now on the request path - a latency hop and a single point of failure you must monitor like any other dependency.

---

## What just happened (under the hood)
Both Ambassador and Adapter are specializations of the Sidecar pattern (lab 12): an extra container in the same pod that augments the main app. What makes them distinct is the *direction* of the interface they own.

- **Ambassador owns the OUTBOUND edge. The app makes a call to a fixed `localhost` port; the ambassador is what actually sits on that port and decides where the bytes go. Because containers in a pod share one network namespace**, the app's `localhost:6379` and the ambassador's `bind 127.0.0.1:6379` are the *same socket space* - no Service, no DNS, no IP from the app's side. The ambassador then does the messy real-world networking: service discovery, routing, sharding, retries, circuit-breaking, TLS origination, connection pooling. In the lab haproxy did the simplest version (an L4 TCP pipe), but the slot it occupies is where Envoy, a redis cluster proxy, or a smart router would live. Rerouting from A to B was a **config + restart**, never a code change, because the only topology knowledge in the whole pod lived on one line of the ambassador's config.

- **Adapter owns the OUTBOUND interface in the *other* sense: it presents a STANDARD face to the platform.** The app emits whatever its native format is (here, RESP `INFO`); the adapter reads that and re-publishes it in the canonical format the platform expects (Prometheus `/metrics`). The redis was untouched. This is the canonical adapter: every app - whatever language, whatever native stats - exposes the *same* `/metrics` shape via a small translator, so one scrape config and one set of dashboards work fleet-wide.

The shared lesson: push the cross-cutting concern out of the app and behind a stable contract. For the ambassador the contract is "talk to localhost." For the adapter the contract is "emit `/metrics`." Either way the app code gets simpler, more portable, and ignorant of concerns it shouldn't own.

## Dev notes
- **The localhost contract.** Your app should hard-code `localhost:<port>` and nothing else network-y. No backend hostnames, no DNS names, no IPs, no retry/TLS logic. That keeps the app trivial to run locally (point the same port at a local redis) and trivial to move between environments - the ambassador absorbs the difference.
- **Don't smuggle topology back in.** The moment the app reads a `BACKEND_HOST` env var or a discovery client, you've lost the decoupling. If the app needs to know, it isn't an ambassador anymore.
- Adapters mean you can stay un-instrumented. If you can't (or won't) add a Prometheus client library to the app, an adapter sidecar buys you standard telemetry without a code change or rebuild.

## DevOps / Platform notes
- Uniform telemetry across a polyglot fleet. Java, Go, Python, Rust services all emit different native stats. Put an adapter on each and the platform scrapes *one* format. One Prometheus config, one Grafana dashboard library, one alerting ruleset - independent of how each app was written. That uniformity is the whole reason the pattern exists at scale.
- Centralize cross-cutting networking in the ambassador. Retries, timeouts, TLS, mTLS, and connection pooling can live in the sidecar instead of being re-implemented (inconsistently) in every app. Update the sidecar image to roll out a networking policy fleet-wide.
- The ServiceMonitor is the bridge to the platform. With kube-prometheus-stack, a labelled Service plus a ServiceMonitor is all it takes; the operator turns your standard `/metrics` into scrape targets automatically.

## Architect notes (trade-offs)
- Ambassador sidecar vs a full service mesh. A per-pod ambassador is the do-it-yourself version of what Istio/Linkerd give you centrally: a mesh is essentially "ambassador as a managed platform" - Envoy injected as a sidecar everywhere, configured from a control plane, with mTLS, traffic policy, and telemetry for free. Hand-rolled ambassadors are fine for one or two concerns; once you need consistent policy across hundreds of services, a mesh is the same idea with central management (and its own operational weight).
- Adapter vs instrumenting the app directly. Native instrumentation (a Prometheus client in the app) is lower-latency and richer, but requires owning and changing the code. Reach for an adapter when you *can't* change the app (third-party/legacy/closed binary) or *won't* (polyglot fleet where one translator per app is cheaper than N instrumentations). The adapter should **translate, not compute** - keep it thin.
- **Where does the coupling go?** You don't remove coupling, you relocate it: from "app coupled to topology" to "app coupled to a healthy sidecar." That's usually a good trade, but it's a trade.

## SRE notes (failure modes, SLOs, toil)
- The ambassador is on the request path. As Break-it showed, a broken ambassador takes the app down even when the backend is perfectly healthy. Treat the sidecar as a first-class dependency: give it readiness/liveness, alert on its error rate and latency, and remember it adds a hop to every request's latency budget.
- Standardized `/metrics` everywhere is an observability PREREQUISITE. Lab 20 (and any fleet dashboard/alert) assumes every workload speaks the same metrics format. The adapter is how heterogeneous apps satisfy that assumption. Consistent metric *names* matter as much as the format - `redis_up`, `http_requests_total` mean the same thing everywhere, so one alert rule covers the fleet.
- **Toil reduction.** One scrape config and one dashboard set beats per-app bespoke monitoring. The up-front cost (an adapter per app) pays back every time you add a service or change an alert.
- **Watch the sidecar's resources.** An unprovisioned sidecar that gets OOMKilled or CPU-starved silently degrades the app it fronts. Both sidecars here carry explicit requests/limits.

## AI/ML notes (LLM/ML serving mapping - conceptual)
- **Ambassador as a shard router. Picture the app calling `localhost:8000` for inference; the ambassador fans the request to the right model shard** by consistent-hash or by key - directly analogous to KV-cache-aware routing (route a conversation to the replica that already holds its KV cache) or to a sharded vector index (route by document/key range). The model client stays shard-agnostic; the ambassador owns the topology.
- **Adapter as a metrics normalizer.** Triton, vLLM, and TGI each emit *different* native stats. An adapter per server can normalize them into one Prometheus schema - `tokens_per_second`, `queue_depth`, `time_to_first_token` - so a single dashboard covers heterogeneous model servers. Same pattern as the redis exporter, applied to inference telemetry. (All conceptual here - no GPU is used in this lab.)

## Pitfalls
- **Ambassador as an unmonitored SPOF.** The most common mistake: decouple from topology, then forget the sidecar you now depend on. Monitor it or you've just moved the outage, not removed it.
- **Adapter doing heavy transformation.** An adapter should reshape/translate, not aggregate or compute. Heavy work in the sidecar steals CPU from the app and becomes a hidden bottleneck. Translate, don't process.
- Forgetting the sidecar's resource requests. No requests -> poor scheduling and silent throttling/OOM of the very container the app now depends on.
- **localhost port collisions.** Containers share the pod's network namespace, so two of them cannot bind the same port. Plan the local port map (app, ambassador, adapter) before you wire it.
- **Stale config after a swap.** haproxy reads its file at start, so a ConfigMap change needs a restart (or a reload mechanism). If you swap config and forget to restart, you'll debug a "reroute that didn't happen."

## Further reading
- **KP "Ambassador"** and **KP "Adapter"** - the two structural patterns, with the localhost-contract and standard-interface framings.
- **DDS ch4 (Ambassadors)** and **DDS ch5 (Adapters)** - single-node multi-container patterns, with the monitoring/normalization use case spelled out.
- For where the ambassador idea goes at platform scale: service-mesh docs (Envoy/Istio) - "ambassador as a managed platform." Revisit standardized `/metrics` in **lab 20** (observability/operators).
