# HappyRobot Intake Webhook (AWS Lambda + API Gateway)

An inbound webhook for HappyRobot that:
- Validates and normalizes an MC number via FMCSA
- Matches inbound requests to demo loads
- Persists results to DynamoDB (in AWS)
- View workflow here: https://v2.platform.happyrobot.ai/fde-huy/workflow/qa4zd2h1iw0a/editor/k71263xiq5bb

This README shows how to:
- Recreate a local development environment and run the same commands
- Access the deployed API
- Reproduce the deployment using AWS SAM (recommended) or manual console steps

---

## Repository layout

- `src/handler.py` — Lambda entry point (OPTIONS/GET/POST). Validation, orchestration, CORS.
- `src/services/matching_service.py` — exact-match rules and response shape for matches.
- `src/clients/fmcsa_client.py` — FMCSA verification client (env-configured).
- `src/repos/load_repo.py` — local demo loads reader.
- `src/repos/result_repo.py` — DynamoDB save/get with TTL.
- `src/utils.py` — helpers: JSON-safe Decimal, date extraction, MC sanitize.
- `src/data/fake_loads.json` — demo loads for matching.
- `events/` — example API Gateway proxy event payloads for local invocation.
- `template.yaml` — AWS SAM/CloudFormation template (API Gateway, Lambda, DynamoDB, API key, CORS).
- `Dockerfile` — Lambda base image for local runs.
- `docker-compose.yml` — runs the Lambda Runtime Interface on port 9000.

---

## Prerequisites

- Docker Desktop (Compose v2 preferred; if you have v1, use `docker-compose` instead of `docker compose`)
- AWS account and IAM credentials configured locally (`aws configure`)
- AWS SAM CLI (for deployment): https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html
- Optional: curl or PowerShell (Invoke-RestMethod) for HTTP calls

---

## Quick start: local development

Local runs use the Lambda Runtime Interface Emulator exposed by the base image and `docker-compose.yml`.

1) Create a `.env` file in the project root

You can leave the FMCSA key blank for offline demos (the response will show `mc_valid: false`).

```env
# Used locally by docker-compose; safe defaults
FMCSA_WEBKEY=
FMCSA_BASE_URL=https://mobile.fmcsa.dot.gov/qc/services
FMCSA_MAX_RETRIES=0
FMCSA_BACKOFF_SECONDS=0.75
FMCSA_TIMEOUT_SECONDS=28
# RESULTS_TABLE is intentionally unset locally so DynamoDB calls are no-ops
```

2) Build and run the container

- Compose v2:
```powershell
docker compose up --build
```
- Compose v1:
```powershell
docker-compose up --build
```
This exposes the Lambda invoke endpoint at `http://localhost:9000/2015-03-31/functions/function/invocations`.

3) Invoke the Lambda locally with the sample POST event

PowerShell:
```powershell
$body = Get-Content -Raw .\events\event-post.json
Invoke-RestMethod -Uri http://localhost:9000/2015-03-31/functions/function/invocations -Method Post -Body $body -ContentType 'application/json' | ConvertTo-Json -Depth 6
```

curl:
```bash
curl -s -H 'Content-Type: application/json' \
  --data @events/event-post.json \
  http://localhost:9000/2015-03-31/functions/function/invocations | jq .
```
Expected 200 with a body containing a new `request_id`, `received_at`, and a `summary` with `mc_valid`, `matches_count`, and `status`.

4) Note about GET/update flows locally

- The repository `src/repos/result_repo.py` uses DynamoDB when the `RESULTS_TABLE` env var is set (in AWS).
- Locally, `RESULTS_TABLE` is not set, so POST will not persist and GET will return `404 Not found`.
- These flows work end-to-end after deployment (see below).

---

## API overview (deployed)

Base path: from CloudFormation/SAM outputs (see "Access the deployment"). CORS enabled, API key required.

- POST `/intake`
  - Required JSON body fields: `mc_number`, `origin`, `destination`, `pickup_datetime`, `equipment_type`
  - Response (create): `{ ok: true, request_id, received_at, summary: { mc_valid, matches_count, status } }`
  - Response (update: include `request_id` and any of: `delivery_datetime`, `carrier_name`, `rate_offer`, `counter_offer`, `outcome`, `sentiment`): `{ ok: true, request_id, updated_at }`

- GET `/intake/{request_id}`
  - Response: `{ ok: true, result: <full saved document> }`

Header for all calls: `x-api-key: <YourApiKey>`

---

## Deploy with AWS SAM (recommended)

1) Configure AWS credentials and region:
```powershell
aws configure
```

2) Build the application:
```powershell
sam build
```

3) Deploy (guided, once):
```powershell
sam deploy --guided
```
Recommended answers when prompted:
- Stack Name: `happyrobot-intake`
- Region: your preferred region (e.g., `us-east-1`)
- Parameter `ApiKeyValue`: choose a string, e.g., `hr-intake-dev-key`
- Parameter `FmcsaWebKey`: your FMCSA API WebKey (leave empty to disable live verification)
- Confirm changes before deploy: `y`
- Allow SAM to create roles: `y`
- Save arguments to `samconfig.toml`: `y`

4) Get the outputs (endpoints):
```powershell
aws cloudformation describe-stacks `
  --stack-name happyrobot-intake `
  --query "Stacks[0].Outputs" `
  --output table
```
Look for `IntakeUrl` and `GetResultUrlTemplate`.

---

## Access the deployment
 
You can access and manage the deployed workflow via the HappyRobot Platform:
https://v2.platform.happyrobot.ai/fde-huy/workflow/qa4zd2h1iw0a/editor/k71263xiq5bb
 
- Configure AI agent prompts and HTTP GET/POST request settings in the Editor.
- Publish the workflow.
- Test after publishing using:
   - Generate Output Schema to validate/preview the response structure.
   - Web Call Trigger to invoke the workflow endpoint and test AI agent functionalities.
 
Assume the following from stack outputs:
- Intake URL (POST): `https://{restapiid}.execute-api.{region}.amazonaws.com/prod/intake`
- Get URL template (GET): `https://{restapiid}.execute-api.{region}.amazonaws.com/prod/intake/{request_id}`
- API Key: the `ApiKeyValue` you provided at deploy time

Create:
```powershell
$INTAKE = 'https://{restapiid}.execute-api.{region}.amazonaws.com/prod/intake'
$APIKEY = 'hr-intake-dev-key'   # replace if different

$payload = @{ 
  mc_number='MC123456';
  origin='Chicago, IL';
  destination='Dallas, TX';
  pickup_datetime='2025-08-22';
  equipment_type='Dry Van'
} | ConvertTo-Json

Invoke-RestMethod -Uri $INTAKE -Method Post -Headers @{ 'x-api-key'=$APIKEY; 'Content-Type'='application/json' } -Body $payload | ConvertTo-Json -Depth 6
```
The response includes `request_id`.

Fetch by id:
```powershell
$REQID = '<paste-request-id>'
$GETURL = "https://{restapiid}.execute-api.{region}.amazonaws.com/prod/intake/$REQID"
Invoke-RestMethod -Uri $GETURL -Method Get -Headers @{ 'x-api-key'=$APIKEY } | ConvertTo-Json -Depth 8
```

Update (optional fields):
```powershell
$update = @{ request_id=$REQID; rate_offer=1500; outcome='accepted' } | ConvertTo-Json
Invoke-RestMethod -Uri $INTAKE -Method Post -Headers @{ 'x-api-key'=$APIKEY; 'Content-Type'='application/json' } -Body $update | ConvertTo-Json -Depth 6
```

curl equivalents:
```bash
APIKEY='hr-intake-dev-key'
INTAKE='https://{restapiid}.execute-api.{region}.amazonaws.com/prod/intake'

curl -s -H "x-api-key: $APIKEY" -H 'Content-Type: application/json' \
  -d '{"mc_number":"MC123456","origin":"Chicago, IL","destination":"Dallas, TX","pickup_datetime":"2025-08-22","equipment_type":"Dry Van"}' \
  "$INTAKE" | jq .

REQID=<paste-request-id>
GETURL="https://{restapiid}.execute-api.{region}.amazonaws.com/prod/intake/$REQID"
curl -s -H "x-api-key: $APIKEY" "$GETURL" | jq .
```

---

## Reproduce the deployment (manual console steps)

1) Open AWS CloudFormation Console → Create stack → With new resources (standard)
2) Upload `template.yaml` from this repo
3) Set parameters:
   - `ApiKeyValue` — the API key to require on the API Gateway
   - `FmcsaWebKey` — your FMCSA WebKey (optional; leave empty to disable live verification)
4) Next → Next → Create stack
5) When complete, open the stack Outputs to get `IntakeUrl` and `GetResultUrlTemplate`
6) Test using the commands above with the `x-api-key` header

What the template provisions:
- API Gateway (regional) with CORS and API key requirement
- Lambda function `src/handler.py` (Python 3.11)
- DynamoDB table for results with TTL
- Usage plan + API key wired to the API stage

---

## Running the exact same local commands I use

From the repo root on Windows PowerShell:

```powershell
# 1) Start local Lambda
docker compose up --build

# 2) Invoke with the sample API Gateway event
$body = Get-Content -Raw .\events\event-post.json
Invoke-RestMethod -Uri http://localhost:9000/2015-03-31/functions/function/invocations -Method Post -Body $body -ContentType 'application/json' | ConvertTo-Json -Depth 6
```

These commands produce a 200 response with a `request_id` and a summary. The GET/update flows are only fully functional after deploying (because DynamoDB is provisioned in AWS).

---

## Troubleshooting

- FMCSA verification returns `valid: false` locally — expected if `FMCSA_WEBKEY` is blank. Set a real key to test live calls.
- Local GET returns 404 — expected; DynamoDB is only provisioned in AWS.
- Compose command not found — on some systems use `docker-compose` instead of `docker compose`.
- Port conflicts — ensure `9000` (host) and `8080` (container) are free, or adjust `docker-compose.yml`.

---

## Security notes

- Do not commit real secrets. `.env` is git-ignored. Use `FmcsaWebKey` parameter at deploy time.
- The template creates an API key and usage plan; always send `x-api-key` when calling the API.

---

## License

Proprietary — internal demo/workflow code for HappyRobot.
