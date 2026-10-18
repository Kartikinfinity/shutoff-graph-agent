# Optional hosting: one container (not deployed)

There is no hosted ShutOff, because hosting needs an account that only the authors can create. The supported way to
run it is `docker compose up -d` (see the main README). This folder holds a tested route for authors who want a
public demo.

## Why one container

- **FalkorDB Cloud's free plan has no TLS** ([plans](https://www.falkordb.com/plans/), checked 18 Oct 2026). An app
  hosted elsewhere would send the database password and queries over the internet unencrypted.
- **`Dockerfile.single` avoids that.** It runs FalkorDB inside the same container, bound to `127.0.0.1`, and publishes
  only the web app's port. Storage is ephemeral: the app seeds the synthetic network on start-up, and *Reset demo*
  restores it.

## Tested locally (18 Oct 2026)

`docker build -f deploy/Dockerfile.single -t shutoff-single .`, then `docker run --user 1000:1000 -p 7860:7860
shutoff-single`. This runs as an unprivileged user, as Hugging Face Spaces does. The checks:
- `/api/health` reported FalkorDB up with 242 nodes;
- the Maple St report gave the 4-valve, 34-account plan;
- "Crew reports V-265 is seized" gave the 7-valve, 66-account plan;
- Redis `bind` was `127.0.0.1`, only port 7860 was published, and memory use was about 60 MB.

## To publish on a free Hugging Face Docker Space (author's account)

1. Create a Space with SDK **Docker** on the free **CPU basic** hardware.
2. Clone the Space repository.
3. Copy `requirements.txt`, `shutoff/`, `web/` and `deploy/start-single.sh` into it, and `deploy/Dockerfile.single`
   as `Dockerfile`.
4. Start its `README.md` with:
   ```
   ---
   title: ShutOff
   sdk: docker
   app_port: 7860
   ---
   ```
5. Push. When the build finishes, open the Space URL in a fresh browser and check that the header shows FalkorDB
   connected (242 nodes) and that *Report a break on Maple St* gives the 4-valve plan.

## Caveats for a public instance

- All visitors share one graph, so one visitor's approval is visible to the others. *Reset demo* restores it.
- Leave LLM keys unset. Visitors would use up your quota, and the offline planner is the tested default.
- Free Spaces sleep when idle, and the first request after that waits for a restart.
