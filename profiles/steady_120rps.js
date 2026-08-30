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
      // Duration is an OPERATIONAL parameter, not a load characteristic: the rate
      // and the open model are what the measurement depends on. A six-experiment
      // suite outlasts 10m, and a generator that expires mid-suite would return
      // INVALID for the later runs - correct behaviour, wrong cause.
      duration: __ENV.LOAD_DURATION || '10m',
      // 700 preAllocated. Sized for the WORST CASE THE ABORT THRESHOLDS PERMIT,
      // not for steady state - that distinction is the whole point.
      //
      // 200 proved insufficient (23 Aug 2026): a pod-kill latency spike pushed
      // demand past 200, k6 dropped 72 iterations, the validity gate correctly
      // returned INVALID. Raised to 400 on the arithmetic 120rps x 3s client
      // timeout = 360 in flight.
      //
      // 400 proved insufficient too (30 Aug 2026, defect D-F, execution #22).
      // That arithmetic used the 3s timeout as the worst case, but the timeout
      // bounds ONE hop - order-api -> payment-service - while a client request
      // also pays inventory, queueing and JIT time on top. Under a partition
      // against a build with no fallback, observed client p99 was 3.755s, so
      // demand was 120 x 3.755 = 451 VUs, k6 dropped 14 iterations, and a run
      // that had genuinely falsified came back INVALID instead.
      //
      // That failure mode is the dangerous one: the generator saturates
      // precisely when the system is WORST, so the more severe the fault, the
      // more likely the gate reports "we could not measure it" rather than a
      // verdict - and severe faults are the ones worth having a gate for.
      // 700 covers ~5.8s of client-side p99 at 120 rps, comfortably past the
      // point where the availability abort at 0.80 would have fired anyway.
      // Fix the generator, never the gate.
      preAllocatedVUs: 700, maxVUs: 1000,
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
