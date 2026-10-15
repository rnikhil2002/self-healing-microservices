# Self-Healing Microservices Platform

Self-healing platform for Spring Boot microservices on Kubernetes with AI-driven incident detection and automatic remediation.

## Tech Stack
- Java, Spring Boot
- Python, scikit-learn
- Kubernetes, Docker
- Prometheus, Grafana
- OpenAI API

## Structure
- `services/` Spring Boot microservices used to simulate production incidents.
- `detector/` Python anomaly detection service (Isolation Forest) on Prometheus metrics.
- `k8s/` Kubernetes manifests and remediation config.

## Status
In progress.
