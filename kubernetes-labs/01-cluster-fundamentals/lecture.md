# Lecture 01 - Cluster fundamentals: the declarative model & labels

## Answers to the lab checkpoints
- **(0)** Empty. A namespace is just a scope/boundary; creating it allocates no workloads. `kubectl get pods` returns "No resources found."
- **(1)** The `namespace:` field under the `context:` in `--minify` output. `kubens` edited your kubeconfig's current-context to pin that namespace, so every command now defaults to it.
- **(2)** Typically `Pending` -> `ContainerCreating` -> `Running`. `Pending` = the scheduler hasn't placed it (or it's placed but image/volume not ready); `ContainerCreating` = kubelet is pulling the image and setting up; `Running` = at least one container started. On a warm node with the image cached you may blink past the first two.
- **(3)** Zero pods created - `--dry-run=client` renders the object locally and never sends a create. This is the canonical way to scaffold a manifest.
- **(4)** **Both.** `app=demo` is on both pods; `color` is what differs. The selector matches on the keys you name and ignores the rest. This is *the* mental model: a selector is an AND over the labels you list, blind to labels you don't.
- **(5)** `echo-green` gains `tier=frontend` and now matches `-l tier=frontend`; `echo-blue` does not. You changed *membership in a set* by editing a label on a live object - no restart, no respec.
- **(6)** `Scheduled` -> `Pulling` -> `Pulled` -> `Created` -> `Started`. That ordered story is your first debugging tool: wherever it *stops* tells you the failing stage.
- **(7)** `explain` reads the OpenAPI schema the API server publishes - the same schema that validates your YAML and that `--dry-run` rendered. The cluster documents itself.
- **(B1) It settles into `ImagePullBackOff` (after a transient `ErrImagePull`) and never** runs - the image tag doesn't exist, so there's nothing to start.
- **(B2) `Failed to pull image ... not found` / reason `Failed`, then `BackOff`. Logs are empty because no container process ever existed - which is exactly why events, not logs, are the first move** for a pod that won't start.
- **(B3)** The original `echo` container did **not** restart (`RESTARTS` stays 0). Ephemeral debug containers are added to the running pod's sandbox without disturbing existing containers - that's their whole point.

---

## What just happened (under the hood)
You typed `kubectl apply`. Here's the real path that request took:

1. **kubectl -> API server.** kubectl is a thin HTTP client. It POSTs your object to the kube-apiserver, the *only* component that talks to etcd.
2. **authn -> authz -> admission.** The API server authenticates you (your kubeconfig credential), authorizes the action (RBAC - lab 19), then runs admission controllers (mutating + validating) that can default or reject the object. *Nothing* reaches storage without clearing this gate.
3. **etcd. The validated object is persisted as desired state**. At this instant the pod "exists" in the cluster's mind - but no container is running.
4. **Controllers reconcile. Control-loop components watch etcd (via the API server) for objects that don't match reality. For a bare Pod, the scheduler** notices it has no node assigned and binds it to one (filter then score the nodes). 
5. **kubelet acts.** The kubelet on the chosen node sees a pod bound to it, pulls the image (the `Pulling`/`Pulled` events), asks the container runtime to create and start containers (`Created`/`Started`), and reports status back up to the API server.

Two durable lessons:
- **Declarative, not imperative.** You never told anyone "pull this image, start this process." You declared *what should be true*; independent controllers drove reality toward it. Everything in Kubernetes is this loop - Deployments, Services, PVCs, autoscalers - all the way up. The broken pod stuck in `ImagePullBackOff` is the loop *still trying*, not a one-shot failure.
- **Labels are the universal join key.** A bare label string (`app=demo`) is how Services find pods, how Deployments own ReplicaSets, how you query and operate. There are no foreign keys in Kubernetes; there are labels and selectors.

## Dev notes
- Label discipline is an API contract. Use the well-known `app.kubernetes.io/*` labels (`name`, `part-of`, `component`, `version`). Downstream things (Services, dashboards, cost tools) select on them. Picking sloppy labels now is a refactor later.
- **Labels vs annotations:** labels are for *selection* (indexed, queryable, size-limited); annotations are for *non-identifying metadata* (build SHA, links, tool hints) and can be large. Don't put a git SHA in a label you'll never select on.
- `--dry-run=client -o yaml` is your manifest generator. Scaffold with it, then edit and commit. Don't hand-write YAML from memory; let the cluster's schema (`kubectl explain`) guide you.

## DevOps / Platform notes
- One context per cluster, namespaces as soft tenancy. `kubectx`/`kubens` prevent the classic "I ran it against prod" incident. Scope kubeconfigs with RBAC so a user's token *can't* reach namespaces they shouldn't.
- Namespaces are a boundary, not a wall. They scope names, RBAC, quotas, and NetworkPolicy targets - but by default pods in different namespaces can still talk over the network (lab 08 fixes that) and a namespace is not a security sandbox by itself.
- Imperative commands are for exploration; GitOps is for production. Anything you `kubectl run`/`edit` live is drift waiting to happen. The end state is "the manifest in Git is the truth" (lab 02's lecture).

## Architect notes (trade-offs)
- **Reconciliation vs orchestration.** Kubernetes is not a workflow engine that runs steps once; it's a set of controllers continuously closing the gap between desired and actual. This is why it self-heals (kill a pod, it comes back) *and* why "I deleted it but it reappeared" happens - something still desires it.
- The API server is the hub-and-spoke center. Every component is a client of the API server; none talk to etcd directly. That single chokepoint is what makes extension (CRDs/operators, lab 20) and uniform authz possible - and also what you must keep healthy.

## SRE notes (failure modes, SLOs, toil)
- First move on a sick pod: `describe` + events, not `logs`. As you saw in Break-it, a pod that never started has no logs but a clear event trail. Internalize the event sequence (`Scheduled->Pulling->Pulled->Created->Started`): *where it stops names the failing subsystem.*
- Know the Pod phases and common reasons cold: `Pending` (unschedulable? check `describe` -> events for "Insufficient cpu/memory" or taints), `ImagePullBackOff` (bad tag/registry auth), `CrashLoopBackOff` (process exits - *now* you read logs, lab 03), `OOMKilled` (lab 05), `Terminating` stuck (finalizers/grace, lab 03).
- **`kubectl debug` is your scalpel.** Distroless/no-shell images are common in prod; ephemeral containers let you attach `busybox` to a *running* pod without a restart (which would destroy the evidence). Restarting a crashed pod to "look inside" often erases the very state you needed.

## AI/ML notes (LLM/ML serving mapping - conceptual)
- **Namespace-per-model/team** is the standard isolation unit for an inference platform - separate quotas, RBAC, and NetworkPolicy per tenant/model.
- **Labels carry model identity:** `model=llama-3-8b`, `quant=awq`, `version=2024-06` let a router/Service select a specific variant - the same selector mechanism you used here is how canary traffic gets split between model versions (lab 02).
- Declarative reconciliation is why serving operators work: KServe/Ray Serve/vLLM-on-K8s all express "I want N replicas of this model server" as desired state and let controllers converge - exactly the loop you just watched, scaled to GPUs.

## Pitfalls
- **Editing live objects** (`kubectl edit`) -> silent drift from Git. Fine for learning, dangerous in prod.
- **`:latest` tags** -> you can't reason about what's running or roll back deterministically (lab 02). Always pin.
- **Reaching for `logs` first** on a non-starting pod -> you'll stare at emptiness. Events first.
- Treating namespaces as security isolation -> they aren't, by default (network + node are shared).

## Further reading
- **KIA ch2 - first steps with Docker & kubectl; KIA ch3** - Pods, labels, annotations, namespaces, label selectors.
- For the control-plane request flow in depth: **KIA ch11** (API server, etcd, scheduler, controllers, kubelet) - you'll revisit this in lab 20 when you write a controller.
