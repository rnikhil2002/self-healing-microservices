# Self-Healing Microservices Platform

A platform that watches Spring Boot microservices on Kubernetes, spots incidents before
users feel them, and fixes them automatically. A Python service learns each service's
normal behaviour from Prometheus metrics with an Isolation Forest, decides what kind of
incident it is seeing, and acts through the Kubernetes API: restart pods, roll back a bad
release, or scale out. Every incident gets a plain-English root-cause summary written by
an LLM.

![CI](https://github.com/rnikhil2002/self-healing-microservices/actions/workflows/ci.yml/badge.svg)

## How it works

```
  loadgen ──▶ order-service ──▶ payment-service        (Spring Boot, Actuator + Micrometer)
                  │                    │
                  └───── /actuator/prometheus ─────┐
                                                   ▼
                                             Prometheus ──▶ Grafana dashboard
                                                   │
                                     PromQL every 5s (p95 latency, 5xx rate, heap, rps)
                                                   ▼
                       ┌────────────────── Healer (Python) ──────────────────┐
                       │ 1. learn a baseline per service (warm-up window)     │
                       │ 2. Isolation Forest + z-score: is this sample odd?   │
                       │ 3. classify: ERROR_SPIKE / HIGH_LATENCY / MEMORY      │
                       │ 4. pick a fix, act through the Kubernetes API         │
                       │ 5. LLM writes the incident summary, check recovery    │
                       └──────────────────────────────────────────────────────┘
```

| Incident | Likely cause | Action |
|---|---|---|
| Error spike right after a rollout | bad release | **Roll back** to the previous ReplicaSet (same as `kubectl rollout undo`) |
| Error spike with no recent rollout | stuck instance | **Restart** the pods |
| p95 latency far above baseline | saturation | **Scale out** one replica at a time (up to `MAX_REPLICAS`), restart once at the cap |
| Heap climbing toward its limit | memory leak | **Restart** the pods |

Detection needs three anomalous samples in a row (about 15 seconds) and each service has a
cooldown after an action, so the healer doesn't flap. About 90 seconds after acting, it
checks the metrics again and marks the incident as recovered or still unhealthy.

### Detection details

- Each service gets its own baseline from the first 24 healthy samples (about 2 minutes).
- Features are p95 latency, 5xx error rate, heap usage and request rate, scaled against the
  baseline with a minimum spread per feature so very steady services don't over-trigger.
- An **Isolation Forest** flags unusual combinations of metrics. Its splits only span the
  range seen in training, so values far beyond the baseline are caught by a z-score check
  and a few hard limits (heap ≥ 80%, errors ≥ 20%).
- To become an incident, a metric also has to move by a meaningful absolute amount
  (+150 ms p95, +5 points of errors, +15 points of heap). Traffic changes alone never trigger
  an action.

## Repository layout

| Folder | What |
|---|---|
| `services/` | Maven multi-module project: `order-service`, `payment-service` and `chaos-kit`, a small library that injects latency, errors and memory leaks on demand (`/chaos/*`) |
| `detector/` | The healer: Prometheus client, anomaly detection, remediation (Kubernetes, Docker, dry-run), LLM summarizer, FastAPI (`/incidents`, `/status`, `/metrics`) |
| `k8s/` | Deployments, Services and least-privilege RBAC for the healer and Prometheus |
| `prometheus/`, `grafana/` | Scrape config and a provisioned dashboard |
| `load/` | Steady order traffic so there's a baseline to learn |
| `scripts/` | Cluster setup and the incident scenarios |

## Run it on Kubernetes (kind)

Requires Docker, [kind](https://kind.sigs.k8s.io/) and kubectl.

```bash
bash scripts/kind-up.sh                       # build images, create cluster, deploy
kubectl -n self-healing port-forward svc/grafana 3000:3000 &
kubectl -n self-healing port-forward svc/detector 8090:8090 &
```

Give it about two minutes to learn the baseline (`curl localhost:8090/status`), then break
something:

```bash
bash scripts/bad-release.sh     # payment-service v1.1.0 fails 60% of requests -> ROLLBACK
bash scripts/memory-leak.sh     # order-service leaks heap                     -> RESTART
```

Watch the incident:

```bash
curl -s localhost:8090/incidents | jq '.[0] | {incident_type, reason, action, action_detail, recovered, summary}'
kubectl -n self-healing rollout history deployment/payment-service
```

For LLM summaries, pass a key when deploying: `OPENAI_API_KEY=sk-... bash scripts/kind-up.sh`.
Without one the healer writes a template summary instead.

## Run it with Docker Compose

```bash
docker compose up --build
```

Grafana is at http://localhost:3000 and the healer at http://localhost:8090. In compose
mode the healer can only restart containers, so rollback and scaling fall back to a
restart. Inject faults directly:

```bash
curl -X POST 'localhost:8082/chaos/errors?rate=0.5'
curl -X POST 'localhost:8081/chaos/latency?ms=800'
curl -X POST 'localhost:8081/chaos/memory-leak?mbPerSecond=15'
curl -X POST 'localhost:8081/chaos/reset'
```

## Try the healer without Java

`detector/tools/fake_service.py` serves the same Micrometer metrics as the Spring Boot apps
and accepts the same `/chaos` calls, so you can run the healer against a real Prometheus
on any laptop:

```bash
cd detector && pip install -r requirements.txt
python tools/fake_service.py --name order-service --port 9101 &
python tools/fake_service.py --name payment-service --port 9102 &
# point a local Prometheus at localhost:9101 and :9102 (path /actuator/prometheus), then:
PROMETHEUS_URL=http://localhost:9090 WARMUP_SAMPLES=12 uvicorn healer.main:app --port 8090
curl -X POST 'localhost:9102/chaos/errors?rate=0.4'
```

## Tests

| Part | Command | Covers |
|---|---|---|
| Services | `cd services && mvn verify` | chaos kit, order and payment APIs, injected failures, metrics endpoint |
| Healer | `cd detector && pytest` | detection on healthy, error, latency and memory scenarios; streaks; rollback logic against a fake Kubernetes API; Prometheus parsing; LLM fallback; full detect, act and recover cycle |
| Config | CI | `promtool check config`, `kubeconform` on every manifest |
| End to end | CI | kind cluster, bad release must be rolled back, memory leak must trigger a pod restart |

## Configuration (healer)

| Variable | Default | |
|---|---|---|
| `PROMETHEUS_URL` | `http://localhost:9090` | |
| `SERVICES` | `order-service,payment-service` | Deployment names to watch |
| `REMEDIATION_MODE` | `dry-run` | `kubernetes`, `docker` or `dry-run` |
| `POLL_INTERVAL_SECONDS` | `5` | |
| `WARMUP_SAMPLES` | `24` | baseline size |
| `ANOMALY_STREAK` | `3` | consecutive anomalous samples before acting |
| `COOLDOWN_SECONDS` | `120` | per service, after an action |
| `ROLLBACK_WINDOW_SECONDS` | `900` | a rollout this recent is blamed for an error spike |
| `MAX_REPLICAS` | `4` | |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | none / `gpt-4o-mini` | LLM summaries |

## What I learned

- Anomaly detection is the easy half. Deciding *what to do* and not making things worse
  (cooldowns, streaks, least-privilege RBAC, recovery checks) is most of the work.
- Tree-based models don't extrapolate. Pairing an Isolation Forest with simple
  statistical guards made detection both sensitive and quiet.
- Observability has to be designed in: consistent `application` tags and histogram buckets
  in Micrometer are what make one PromQL query work for every service.
