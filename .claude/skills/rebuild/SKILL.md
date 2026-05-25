# Rebuild & Restart Services
1. cd to the active worktree (verify with `git rev-parse --show-toplevel`)
2. git checkout main && git pull
3. Ensure Docker daemon is running; start Docker Desktop and poll if not
4. docker compose build && docker compose up -d
5. Verify all containers are healthy; report status
