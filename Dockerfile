# A small "Linux server" for the demo: the app runs as service user `app`, the operator `dev` has sudo
# (py-spy needs root to read another user's process).
#   default   PY=3.14.10 PYSPY=0.4.4 REQS=requirements-314.txt   script modes + web service
#   Tachyon   PY=3.15.2  PYSPY=0.4.4 REQS=requirements-none.txt  script modes; python -m profiling.sampling
ARG PY=3.14.10
FROM python:${PY}-slim
ARG PYSPY=0.4.4
ARG REQS=requirements-314.txt
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends sudo procps tini >/dev/null \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -u 1000 -m -s /usr/sbin/nologin app \
 && useradd -u 1001 -m -s /bin/bash dev \
 && echo 'dev ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/dev && chmod 0440 /etc/sudoers.d/dev
COPY ${REQS} /srv/requirements.txt
RUN pip install --no-cache-dir -q -r /srv/requirements.txt && pip install --no-cache-dir -q py-spy==${PYSPY}
COPY app /srv/app
COPY loadgen /srv/loadgen
COPY tests /srv/tests
WORKDIR /srv
EXPOSE 8000
# tini is PID 1; uvicorn runs directly as `app`. Find it with `pgrep -u app -f uvicorn`.
ENTRYPOINT ["tini", "--"]
CMD ["setpriv", "--reuid=app", "--regid=app", "--init-groups", "--", \
     "uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
