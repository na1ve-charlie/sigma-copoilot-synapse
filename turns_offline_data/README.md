# Turns Offline Data Export

This directory contains data-only fixtures for exercising `POST /turns` when the
real SigMA backend is unavailable.

It intentionally does not include mock code, app wiring, or behavior changes.
Use `manifest.yaml` as the entry point.

Contents:

- `manifest.yaml`: inventory and source notes.
- `turns/request_examples.json`: public `/turns` request payload examples.
- `turns/response_samples.yaml`: public `/turns` response plan samples.
- `sigma/offline_1152.workspace_context.json`: workspace context fixture.
- `sigma/offline_1152.snapshot.json`: small offline domain snapshot.
- `sigma/backend_endpoint_samples.json`: SigMA adapter endpoint request and
  response samples captured from current characterization tests.

