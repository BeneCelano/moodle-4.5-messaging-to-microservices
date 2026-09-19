# delivery-mock

`mock_delivery.py` replaces the two external services the DeliveryGateway talks to: an SMTP server
on port 1025 and an Airnotifier server on port 8081 (`POST /api/v2/push/`). One process, standard
library only, installed on VM4 as the `delivery-mock` systemd unit. What it receives is shown on
`http://192.168.72.74:8081/` and returned as JSON by `GET /events`; the store keeps the last 200
items per kind and is lost at restart.

`POST /fail` puts both services in fault (451 on SMTP, 503 on push, both temporary so that the
gateway retries), `POST /repair` clears it, `POST /reset` empties the store, `GET /health` answers
`{"status": "alive", "failing": false}`. The fault is used by the "no delivery lost" check of phase 4;
if a run is interrupted while it is on, `curl -s -X POST http://192.168.72.74:8081/repair`.

Install from VM1 (VM4 has no repository):

```bash
scp -P 2222 /home/tesista/repo-tesi/deploy/mocks/delivery-mock/mock_delivery.py /home/tesista/repo-tesi/deploy/mocks/delivery-mock/delivery-mock.service tesista@192.168.72.74:/home/tesista/delivery-mock/
```

then on VM4 `sudo cp /home/tesista/delivery-mock/delivery-mock.service /etc/systemd/system/ &&
sudo systemctl daemon-reload && sudo systemctl enable --now delivery-mock`.
