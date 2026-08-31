# ChaosProof - build targets (§27 quick start)
# Pins (resolved 23 Aug 2026): kind node v1.36.1 · kube-prometheus-stack 88.5.3
# · litmus-core 3.31.0 (operator + CRDs; the ChaosCenter UI chart is deliberately
# not installed - the framework applies ChaosEngine CRDs itself).

KIND_NODE        := kindest/node:v1.36.1
KPS_CHART_VER    := 88.5.3
LITMUS_CHART_VER := 3.31.0
K6OP_CHART_VER   := 4.6.0
METRICS_SERVER_VER := v0.8.0   # HPA needs it; kind needs --kubelet-insecure-tls
CLUSTER          := chaosproof
EVIDENCE_CONTAINER := chaosproof-evidence
EVIDENCE_PORT    := 5433
POSTGRES_IMAGE   := postgres:18.6-alpine
SERVICES         := order-api payment-service inventory-service

.PHONY: bootstrap kind-up evidence-up evidence-backup target-app load load-stop experiment score unit ci-gate dashboard dashboard-dev dashboard-verify bisect kind-down

bootstrap:      ## Everything from nothing: cluster + platform + evidence store + app + load
	$(MAKE) kind-up
	$(MAKE) evidence-up
	$(MAKE) target-app
	$(MAKE) load

kind-up:        ## kind 1.36 + kube-prometheus-stack (5s scrape on target) + Litmus operator
	kind create cluster --name $(CLUSTER) --image $(KIND_NODE) --wait 180s
	kubectl create namespace monitoring
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
	helm repo add litmuschaos https://litmuschaos.github.io/litmus-helm/
	helm repo update
	helm install kps prometheus-community/kube-prometheus-stack --version $(KPS_CHART_VER) \
	  --namespace monitoring -f monitoring/kps-values.yaml --wait --timeout 10m
	kubectl create namespace litmus
	helm install litmus litmuschaos/litmus-core --version $(LITMUS_CHART_VER) \
	  --namespace litmus --wait --timeout 5m
	helm repo add grafana https://grafana.github.io/helm-charts
	helm install k6-operator grafana/k6-operator --version $(K6OP_CHART_VER) \
	  --namespace k6-operator --create-namespace --wait --timeout 5m
	kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/download/$(METRICS_SERVER_VER)/components.yaml
	kubectl patch deployment metrics-server -n kube-system --type=json -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
	kubectl get namespace target-app >/dev/null 2>&1 || kubectl create namespace target-app
	for f in litmus-experiments/*-fault-3.31.yaml; do kubectl apply -f $$f -n target-app; done
	kubectl apply -f litmus-experiments/pod-delete-rbac.yaml

# Evidence store: postgres 18.6 + migrations, on a NAMED VOLUME so it survives a
# container recreate. It was created ad-hoc with a bare `docker run` until a Docker
# Desktop factory reset took executions 1-4 with it — a live demonstration of the
# plan's own §24: losing a night's schedule costs a night, losing the evidence store
# costs the entire resilience history and every retro-scoring capability with it.
evidence-up:    ## Evidence store: postgres 18.6 + migrations (idempotent)
	-docker rm -f $(EVIDENCE_CONTAINER)
	docker run -d --name $(EVIDENCE_CONTAINER) -p $(EVIDENCE_PORT):5432 -e POSTGRES_DB=chaosproof -e POSTGRES_USER=chaosproof -e POSTGRES_PASSWORD=chaosproof -v chaosproof-evidence-data:/var/lib/postgresql $(POSTGRES_IMAGE)
	until docker exec $(EVIDENCE_CONTAINER) pg_isready -U chaosproof >/dev/null 2>&1; do sleep 2; done
	for m in migrations/*.sql; do echo "applying $$m"; docker exec -i $(EVIDENCE_CONTAINER) psql -q -U chaosproof -d chaosproof -v ON_ERROR_STOP=1 < $$m; done
	@echo "evidence store ready on localhost:$(EVIDENCE_PORT)"

evidence-backup: ## pg_dump the evidence store. Losing it costs the whole resilience history.
	mkdir -p backups
	docker exec $(EVIDENCE_CONTAINER) pg_dump -U chaosproof -d chaosproof > backups/chaosproof-$$(date +%Y%m%d%H%M%S).sql
	@echo "backed up to backups/"

score:          ## GATE 4 - hermetic re-scoring of the SS4 worked example
	python -m evals.scoring_worked_example

# The CI `unit` job, verbatim. Runs in seconds with no cluster and no database:
# a developer must be able to fail cheaply before waiting on the chaos gate.
# --cov-fail-under=80 is scoped to the SCORER, and to nothing else - it is the
# one component whose bugs are invisible, because a wrong number looks exactly
# like a right number.
unit:           ## The CI unit gate: pytest + the scorer's 80% coverage floor
	cd chaos-framework && python -m pytest -m "not chaos" --cov=src --cov-report=term-missing
	cd chaos-framework && python -m pytest -m "not chaos" --cov=src/scoring --cov-fail-under=80

# The CI chaos gate against the LOCAL cluster. `make load` first, always -
# ci_runner refuses to inject into a cluster with no load plane, which is the
# whole reason the gate is worth anything.
ci-gate:        ## GATE 7 - the CI chaos gate, run locally (needs: make load)
	cd chaos-framework && TESTRUN=$${TESTRUN:-steady-120rps} python -m src.orchestrator.ci_runner --experiments network_partition_payment,pod_kill_payment_svc --override

target-app:     ## Build + load the three Spring Boot 4 service images, deploy the chart
	for s in $(SERVICES); do \
	  (cd target-app/$$s && ./mvnw -q -DskipTests package && docker build -q -t $$s:dev .); \
	done
	kind load docker-image $(addsuffix :dev,$(SERVICES)) --name $(CLUSTER)
	kubectl get namespace target-app >/dev/null 2>&1 || kubectl create namespace target-app
	helm upgrade --install target-app charts/target-app --namespace target-app --wait --timeout 8m

load:           ## k6 TestRun, 120 rps open model, remote-writing into Prometheus
	kubectl get namespace load >/dev/null 2>&1 || kubectl create namespace load
	kubectl delete testrun steady-120rps -n load --ignore-not-found
	kubectl delete configmap steady-120rps -n load --ignore-not-found
	kubectl create configmap steady-120rps -n load --from-file=archive.js=profiles/steady_120rps.js
	kubectl apply -f profiles/testrun-steady.yaml

load-stop:      ## Stop the load plane (the INVALID demo starts here)
	kubectl delete testrun steady-120rps -n load --ignore-not-found

experiment:     ## make experiment NAME=pod_kill_payment_svc
	cd chaos-framework && python -m src.chaosctl run $(NAME)

bisect:         ## make bisect NAME=pod_kill_payment_svc GOOD=<sha> BAD=<sha>  (prints the cost, runs nothing)
	cd chaos-framework && python -m src.chaosctl bisect $(NAME) --good $(GOOD) --bad $(BAD) --estimate

dashboard:      ## Next.js dashboard at :3000. READ_ONLY=false for the internal build.
	cd dashboard && npm install --silent && READ_ONLY=$${READ_ONLY:-true} npm run build && npm start

dashboard-dev:  ## Dashboard in dev mode, internal build (trigger controls present)
	cd dashboard && npm install --silent && READ_ONLY=false npm run dev

dashboard-verify: ## Prove the read-only build has NO trigger surface - and that the check can fail
	cd dashboard && READ_ONLY=false npm run build && npm run verify:internal
	cd dashboard && READ_ONLY=true  npm run build && npm run verify:readonly

kind-down:
	kind delete cluster --name $(CLUSTER)
