// ChaosProof load profile: OPEN workload model, constant arrival rate.
// Never constant-vus: a closed model couples offered load to system health,
// so the fault hides itself (measurement-integrity skill, scaling decision 2).
import http from 'k6/http';

export const options = {
  discardResponseBodies: true,
  scenarios: {
    steady: {
      executor: 'constant-arrival-rate',   // OPEN model. Never constant-vus.
      rate: 120, timeUnit: '1s',
      duration: '10m',
      preAllocatedVUs: 200, maxVUs: 800,   // headroom so k6 never becomes the bottleneck
    },
  },
  thresholds: {
    'http_req_failed':    ['rate<0.01'],
    'http_req_duration':  ['p(99)<800'],
    'dropped_iterations': ['count<10'],    // exceeded => the run is INVALID
  },
};

export default function () {
  http.get(`${__ENV.TARGET_URL}/api/orders/checkout`, { tags: { endpoint: 'checkout' } });
}
