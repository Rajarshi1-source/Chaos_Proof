# ChaosProof - build targets (§27 quick start)
# Pins (resolved 23 Aug 2026): kind node v1.36.1 · kube-prometheus-stack 88.5.3
# · litmus-core 3.31.0 (operator + CRDs; the ChaosCenter UI chart is deliberately
# not installed - the framework applies ChaosEngine CRDs itself).

KIND_NODE        := kindest/node:v1.36.1
KPS_CHART_VER    := 88.5.3
LITMUS_CHART_VER := 3.31.0
K6OP_CHART_VER   := 4.6.0
CLUSTER          := chaosproof
SERVICES         := order-api payment-service inventory-service

.PHONY: kind-up target-app load experiment dashboard kind-down

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
	kubectl apply -f litmus-experiments/pod-delete-fault-3.31.yaml -n target-app
	kubectl apply -f litmus-experiments/pod-delete-rbac.yaml

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

dashboard:      ## Next.js dashboard at :3000 (Phase 6)
	@echo "[stub] Phase 6: cd dashboard && npm run dev"
	@exit 1

kind-down:
	kind delete cluster --name $(CLUSTER)
