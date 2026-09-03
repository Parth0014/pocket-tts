# App API Auth V1

`pocket-tts-app-api` is the authenticated HTTP boundary for the internal Narration Studio.

Auth secrets are SSM Parameter Store **Standard SecureString** values:

- `/pocket-tts/app-api/dashboard-secret-code`
- `/pocket-tts/app-api/session-signing-secret`

The Lambda environment stores only those parameter names, never the secret values.

Session cookie:

- `pocket_tts_session`
- `HttpOnly`
- `Secure`
- `SameSite=Lax`
- `Path=/`
- 12-hour maximum age

Routes in this deployment:

Public:

- `GET /health`
- `POST /auth/login`
- `POST /auth/logout`

Authenticated:

- `GET /auth/session`
- `GET /rooms`
- `GET /voices`

Generation creation and FIFO dispatch are deliberately not exposed yet.

CORS is intentionally deferred until the frontend origin is frozen. Credentialed CORS must use an explicit trusted origin and must never combine cookies with a wildcard origin.

IAM in this sprint grants no S3 permission, no SQS permission, no production audio permission, and no legacy `NarrationJobs` access.
