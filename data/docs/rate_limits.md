# AcmeCloud API Rate Limits

Rate limits are enforced per API key. When you exceed the limit, the API returns HTTP status 429 Too Many Requests with a Retry-After header.

The Pro plan allows bursts of up to 50 requests per second. The Free plan allows 5 requests per second.

To increase your rate limit on the Pro plan, go to Settings > API > Request limit increase. Increases are usually approved within 2 business days.

Clients should retry 429 responses with exponential backoff, starting at 1 second.
