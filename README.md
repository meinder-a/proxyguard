# proxy-guard

proxy-guard is a small utility for rotating upstream http proxies - it acts as a gateway that accepts connect requests and tunnels them through a pool of proxies

this was built because Scrapoxy has been discontinued

## what it does

- rotates requests across a list of upstream proxies
- supports hmac-sha256 signatures for authentication
- handles sticky sessions (pinning a client id to a specific proxy)
- basic health checks and circuit breaking for dead proxies
- hot-reloads the proxy list from a file
- simple web dashboard and prometheus metrics

## what it will do

- fetches proxies from various proxie providers
- auto geolocation proxy picking

## setup

### docker

1. put your proxies in a `proxies.txt` file:

   ```text
   http://user:pass@1.2.3.4:8080
   http://user:pass@5.6.7.8:3128
   ```

2. run it:
   ```bash
   make docker-run
   ```

the dashboard is at `http://localhost:9090/dashboard`

### local

```bash
make install

# set your proxies in env or a file
export PG_PROXY_LIST="http://user:pass@1.2.3.4:8080"

# you really should set a secret if using auth
export PG_SECRET="some-long-random-string"

make run
```

it listens on port 8888 for proxies and 9090 for metrics/dashboard

## config

| variable       | default                                | description                         |
| :------------- | :------------------------------------- | :---------------------------------- |
| PG_SECRET      | dev-secret-do-not-use-in-prod (unsafe) | secret for hmac signatures          |
| PG_ENABLE_AUTH | true                                   | toggle hmac auth                    |
| PG_PROXY_FILE  | proxies.txt                            | file with one proxy per line        |
| PG_STICKY_TTL  | 0                                      | how long to pin a session (seconds) |
| PROXY_PORT     | 8888                                   | port for incoming connections       |
| METRICS_PORT   | 9090                                   | dashboard and metrics port          |

## auth

it uses hmac-sha256 by default. the token format is `client_id:timestamp:signature`

you can send it via `Proxy-Authorization: Basic` where the password is `timestamp:signature`

```python
import time, hmac, hashlib, requests

def get_proxy_url(secret, client_id):
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{client_id}{ts}".encode(), hashlib.sha256).hexdigest()
    return f"http://{client_id}:{ts}:{sig}@localhost:8888"

# then use it with requests or whatever
proxies = {"https": get_proxy_url("your-secret", "my-bot")}
requests.get("https://httpbin.org/ip", proxies=proxies)
```

## license

see the LICENSE file
