#!/usr/bin/env bash
# Render start command for the pythonserver worker service:
#     bash start_workers.sh
#
# Runs the Celery worker and the Discord listener side by side. If either one
# exits, the other is stopped and this script exits too, so Render restarts
# both. A plain "celery ... & python ..." would keep the service showing as
# live with half of it dead, and a deploy's SIGTERM would never reach the
# backgrounded process. Here SIGTERM is passed to both, so Celery still gets
# its warm shutdown.
#
# Background jobs (emails, Discord posts, placing calls, chat follow-ups)
# spend their time waiting on the network, so a pool of threads runs several
# at once; with one process, one slow email held up every other job.

celery -A celery_worker worker --loglevel=info --pool=threads --concurrency=8 &
celery_pid=$!
python discord_listener.py &
listener_pid=$!

stop() {
    kill -TERM "$celery_pid" "$listener_pid" 2>/dev/null
}
trap stop TERM INT

# Poll rather than `wait -n`, which needs bash 4.3+. Waiting on a background
# sleep, instead of sleeping in the foreground, keeps SIGTERM handled at once.
while kill -0 "$celery_pid" 2>/dev/null && kill -0 "$listener_pid" 2>/dev/null; do
    sleep 1 &
    wait $!
done

if kill -0 "$celery_pid" 2>/dev/null; then
    who=discord_listener; wait "$listener_pid"; status=$?
else
    who=celery; wait "$celery_pid"; status=$?
fi
echo "start_workers: $who exited (status $status); stopping the other" >&2
stop
wait
exit "$status"
