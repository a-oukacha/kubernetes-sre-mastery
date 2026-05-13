# Lecture 17 - Event-driven & coordinated batch: tail latency and the fork/join barrier

## Answers to the lab checkpoints
- **(0)** Closer to the **slowest one**. A parallel scatter/gather starts all leaves at once and the gather `wait`s for the last to finish, so wall-clock ≈ max(leaves), not sum(leaves). With leaves at ~0s, ~0s, ~3s you expect ~3s - not ~3s + the two fast ones.
- **(2)** ~3s **plus the (tiny) cost of the two fast calls - call it ~3s. `seq.sh` blocks on each call before starting the next, so the wall-clock is the sum**. Almost all of it is `leaf-3` (`/delay/3`); the two `agnhost` calls are milliseconds. Sequential pays for every dependency in series.
- **(3)** Parallel ≈ **3s**; sequential ≈ **3s + ε** but conceptually the *sum*. The win is structural: fan-out converts a sum into a max. The trap is in the same sentence - a max is governed by your *worst* leaf, which is exactly what Break-it exploits.
- **(4)** **Three** `part-*.txt` files, one per shard (`part-0/1/2`). No mapper sees another's count - each reads only its own deterministic shard and writes one partial. The partials are independent; correctness comes later, from the reduce barrier, not from any mapper.
- **(5) `FINAL_WORD_COUNT=60`. The corpus is fixed at 3×4×5 = 60 words, so 60 is the known-correct answer and proves the pipeline aggregated all partials, not a subset. The reducer logged that it waited** for all 3 partials before summing - if it had read early it could have printed 20 or 40 (a silently wrong answer).
- **(6) The trigger scales the `queue-worker` Deployment on RabbitMQ queue length (a replica per 5 queued messages), with `minReplicaCount: 0` (scale-to-zero when idle) and `maxReplicaCount: 20` (the ceiling). It scales on work waiting**, not CPU. Without a broker the trigger errors - expected; the lesson is *what metric drives scaling*, not that it ran.
- **(B1)** **~10s** - pinned to the slow leaf. The gather `wait`s for all three; two fast leaves finishing in milliseconds change nothing. One slow shard set the latency of the whole request. The *average* (~3.3s) is a lie about what the user experienced.
- **(B2)** **~2s, bounded by the per-leaf `timeout 2`, with `2/3 leaves answered in time`. The slow leaf is dropped and the root returns a partial result. Latency is now governed by your budget**, not by your worst dependency. Before: one hang = total hang. After: bounded, degraded gracefully.

---

## What just happened (under the hood)

Scatter/gather is a max, not a sum - and the max is your tail. When a root fans out to N leaves in parallel, the response can't return until the *last* leaf does. So total latency = the **slowest** leaf on that request, not the average. This has a brutal consequence as N grows: even if each leaf is fast *most* of the time, the probability that *at least one* of N leaves hits its slow tail on a given request rises with N. If each leaf is slow (>p99) 1% of the time, a 1-leaf call is slow ~1% of the time, but a 100-leaf fan-out is slow `1 - 0.99^100 ≈ 63%` of the time. The fan-out's median latency is dominated by each leaf's tail. That is why "our p50 is great" tells you nothing about a scatter/gather - you must measure and defend the **tail of every leaf**.

Event-driven decouples producers from consumers with a queue. Instead of a synchronous call, stage A writes a message to a topic/queue and stage B consumes it. This buys three things: the queue **absorbs bursts (producer spikes don't crash the consumer - they just deepen the queue), producer and consumer scale independently**, and you can **replay (re-read the log). The cost: eventual consistency, ordering caveats, and a queue you must now watch. KEDA turns queue depth into a scaling signal - it polls the broker (RabbitMQ, Kafka lag, SQS `ApproximateNumberOfMessages`, ...) and sets the worker Deployment's replica count, including scale-to-zero** when the queue drains. CPU-based autoscaling can't do this: a worker blocked on I/O with 10,000 messages waiting shows *low* CPU. You scale on **work waiting**, not on CPU.

**Coordinated batch needs a barrier. Map/reduce is fork/join: fork N independent map tasks, each producing a partial; join at a barrier; then reduce the partials into one answer. The barrier is non-negotiable - reduce reading partials before all maps finish produces a silently wrong result (in the lab, 40 instead of 60, with no error). Two facts follow. First, the slowest mapper gates the whole job - same tail-latency story as scatter/gather, now over batch tasks: your job's completion time is `max(map_i) + reduce`, so one straggler is your SLA. Second, the shuffle needs storage all stages can see: separate Jobs are separate pods on possibly separate nodes, so an `emptyDir` (per-pod) can't bridge them - you need an RWX PVC (or a real object store / broker). The lab makes the barrier explicit by having the reducer spin until it counts 3 partials**; production expresses the same dependency with a workflow engine (Argo Workflows, Tekton) or `kubectl wait --for=condition=complete`.

**Dead-letter queues and backpressure are the two safety valves of any event-driven system. A poison message (one a worker can never process - malformed, references deleted data) will otherwise be retried forever, blocking the queue; route it to a dead-letter queue after K attempts so the pipeline keeps moving and a human can inspect it. Backpressure caps how fast producers may enqueue (or bounds the queue itself); an unbounded** queue under sustained overload is an OOM/disk-full outage waiting to happen - the broker fills, then everything stops at once.

## Dev notes
- Never let one slow dependency hang the request. Every fan-out call gets a **timeout** and a **partial-result** path (return what you have, mark the rest degraded). The lab's `timeout.sh` is the whole pattern in two lines: `timeout N <call> || echo MISS`. A hung leaf with no timeout is a guaranteed outage the day it slows down.
- **Make every stage idempotent.** Queues deliver *at-least-once*; a worker may see the same message twice (redelivery after a crash, retry after a timeout). Reducing twice must equal reducing once - key on a message/shard ID, write atomically (write-temp-then-rename, as the mapper does), and dedupe.
- **Budget the tail, not the average. Set per-leaf timeouts from the leaf's p99**, not its mean, and surface partial-result rate as a first-class metric. "How often did we drop a leaf?" is a real SLI.

## DevOps / Platform notes
- Pick a broker and wire KEDA to its depth/lag. Kafka (consumer-group **lag), SQS (`ApproximateNumberOfMessages`), RabbitMQ (queue length), NATS JetStream - all are first-class KEDA scalers. Scale workers on that signal and scale-to-zero** when idle. The Helm install is identical on EKS and OVH (`kedacore/keda`).
- Monitor topic lag and queue depth as platform SLIs, with alerts on *sustained growth* (consumers falling behind) not just absolute size. Growing lag = consumers losing the race; flat-high lag = a stuck consumer or poison message.
- Map/reduce over Jobs needs RWX or an object store. On EKS that's EFS (`efs-rwx`); OVH has no native RWX, so it's `nfs-subdir-external-provisioner` or NAS-HA. Both are **billed external shares** - they outlive the namespace, so Cleanup must delete them explicitly. For real data volumes, prefer S3/object storage over an RWX filesystem.

## Architect notes (trade-offs)
- Synchronous scatter/gather vs a broker. Use synchronous fan-out when the caller needs the answer *now* and leaves are fast and bounded (RAG retrieval, search). Introduce a **broker** when you need **decoupling (independent deploy/scale of stages), buffering** (burst absorption), or **replay** (reprocess history). The price is operational complexity and eventual consistency - don't add a queue to a problem that's a function call.
- **Batch vs streaming.** Batch (map/reduce) processes a bounded dataset with a clear start/end and a barrier; streaming processes an unbounded flow with windowing and no global barrier. Word-count over a fixed corpus is batch; word-count over a live event stream is streaming - same logic, different completion semantics.
- Fork/join barriers couple your SLA to your worst worker. Every barrier converts N parallel risks into one tail. Architecturally, you reduce barriers (fewer fan-out points), make leaves more uniform (avoid one giant shard), or add **hedging / speculative re-execution** (start a backup task for stragglers).

## SRE notes (failure modes, SLOs, toil)
- TAIL LATENCY is the headline failure mode. One slow shard makes the whole fan-out slow; one slow mapper makes the whole job slow. Your SLO must be on the **tail of each leaf** and on the **partial-result/timeout rate**, not on averages. When the scatter/gather p99 spikes, the question is always "*which* leaf?" - instrument per-leaf latency.
- **Bounded queues, always. An unbounded queue under overload is an outage on a timer: the broker fills, then producers and consumers stop together. Set max queue length / retention and a backpressure** policy (reject or shed at the edge) so you degrade gracefully instead of falling over.
- Topic-lag alerts + dead-letter monitoring. Alert on lag *trend* (sustained growth) and on dead-letter arrivals (poison messages = a real bug, not noise). A growing DLQ is toil that compounds.
- The map/reduce barrier is your job SLA. Completion = `max(mapper) + reduce`. Watch the **straggler** distribution; a single 10× mapper blows the whole job's deadline. Hedging or splitting hot shards is the fix, not bigger timeouts.

## AI/ML notes (LLM/ML serving mapping - conceptual)
- Scatter/gather over a sharded vector index = RAG retrieval. A query fans out to all index shards in parallel, each returns its top-k, and the root merges into a global top-k. Retrieval latency is the **slowest shard's** latency - the exact tail-latency law from this lab. More shards (bigger index) = higher odds one shard is slow = worse retrieval p99. Per-shard timeouts + "return the partial top-k we have" is the same fix as `timeout.sh`.
- Distributed embedding generation is a map step. Embed a large corpus by fanning chunks across N workers (Indexed Job, one shard each), writing vectors to shared/object storage - then a reduce/index-build barrier. Same map -> shuffle -> reduce shape, GPUs instead of busybox.
- Ensemble / multi-model inference is fan-out then aggregate. Query several models (or several prompts) in parallel and merge (vote, rerank, average). Latency = the slowest model; budget each call.
- Event-driven inference scales GPU workers by queue depth. A queue of prompts + KEDA scaling GPU workers on queue length, with **scale-to-zero** when idle - this is the single biggest GPU-cost lever, because idle accelerators are the dominant waste. Cold start = model load time, so tune `cooldownPeriod` against load latency. *(All conceptual - no GPU is used in this lab.)*

## Pitfalls
- **No timeout on the slowest leaf** -> one hang = total hang. The single most common fan-out outage.
- **Reasoning about averages** when the **tail** is what users feel - a great p50 hides a fan-out that's slow most of the time.
- **Ignoring the map/reduce barrier** -> reduce reads incomplete partials and returns a *silently wrong* answer (40, not 60) with no error.
- **Unbounded queue growth** -> broker OOM/disk-full; the whole pipeline stops at once under overload.
- **Non-idempotent workers** on at-least-once queues -> double-counting on redelivery.
- **`emptyDir` for the shuffle** -> it's per-pod; separate Jobs can't share it. Use RWX or object storage.

## Further reading
- **DDS ch8** - Scatter/Gather (fan-out, tail latency, root/leaf).
- **DDS ch12** - Event-Driven Batch (copier/filter/splitter/sharder/merger, queues, DLQ, backpressure).
- **DDS ch13** - Coordinated Batch (map / shuffle / reduce, the join barrier).
- KEDA docs (scalers: Kafka lag, RabbitMQ, SQS; scale-to-zero) - `https://keda.sh/docs`.
- Dean & Barroso, "The Tail at Scale" (CACM 2013) - the canonical treatment of why fan-out tails dominate.
